import json

import pytest

from unified_can_lin_host_tool import tool_identity as tool_identity_module
from unified_can_lin_host_tool.tool_identity import (
    ToolIdentity,
    get_tool_identity,
    load_tool_identity,
)


@pytest.fixture(autouse=True)
def _clear_tool_identity_cache():
    get_tool_identity.cache_clear()
    yield
    get_tool_identity.cache_clear()


def _official_payload(**changes):
    payload = {
        "version": "0.2.0",
        "commit": "01" * 20,
        "buildTimeUtc": "2026-07-14T12:00:00Z",
        "repository": "owner/ecu-firmware-release-tool",
        "officialBuild": True,
    }
    payload.update(changes)
    return json.dumps(payload).encode()


class _IdentityResource:
    def __init__(self, raw, calls):
        self._raw = raw
        self._calls = calls

    def joinpath(self, name):
        self._calls["joinpath"] += 1
        assert name == "_tool_build_identity.json"
        return self

    def read_bytes(self):
        self._calls["read_bytes"] += 1
        return self._raw


def test_get_tool_identity_caches_metadata_and_resource_reads(monkeypatch):
    calls = {"metadata": 0, "files": 0, "joinpath": 0, "read_bytes": 0}
    resource = _IdentityResource(_official_payload(), calls)

    def read_version(name):
        calls["metadata"] += 1
        assert name == "unified-can-lin-host-tool"
        return "0.2.0"

    def find_resources(package):
        calls["files"] += 1
        assert package == "unified_can_lin_host_tool"
        return resource

    monkeypatch.setattr(tool_identity_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tool_identity_module.importlib.metadata, "version", read_version)
    monkeypatch.setattr(tool_identity_module.importlib.resources, "files", find_resources)

    first = get_tool_identity()
    second = get_tool_identity()

    assert first is second
    assert calls == {"metadata": 1, "files": 1, "joinpath": 1, "read_bytes": 1}


def test_get_tool_identity_returns_unknown_development_identity_without_metadata(
    monkeypatch,
):
    def metadata_missing(name):
        raise tool_identity_module.importlib.metadata.PackageNotFoundError(name)

    def reject_resource_read(_package):
        raise AssertionError("非冻结环境不得读取构建身份资源")

    monkeypatch.setattr(tool_identity_module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        tool_identity_module.importlib.metadata, "version", metadata_missing
    )
    monkeypatch.setattr(
        tool_identity_module.importlib.resources, "files", reject_resource_read
    )

    assert get_tool_identity() == ToolIdentity(
        "0+unknown", "development", "", "", False
    )


def test_get_tool_identity_does_not_read_resource_when_not_frozen(monkeypatch):
    def reject_resource_read(_package):
        raise AssertionError("非冻结环境不得读取构建身份资源")

    monkeypatch.setattr(tool_identity_module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        tool_identity_module.importlib.metadata, "version", lambda _name: "0.2.0"
    )
    monkeypatch.setattr(
        tool_identity_module.importlib.resources, "files", reject_resource_read
    )

    assert get_tool_identity() == ToolIdentity(
        "0.2.0", "development", "", "", False
    )


def test_get_tool_identity_reads_and_validates_frozen_official_identity(monkeypatch):
    calls = {"joinpath": 0, "read_bytes": 0}
    raw = _official_payload()
    resource = _IdentityResource(raw, calls)

    monkeypatch.setattr(tool_identity_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        tool_identity_module.importlib.metadata, "version", lambda _name: "0.2.0"
    )
    monkeypatch.setattr(
        tool_identity_module.importlib.resources,
        "files",
        lambda package: resource if package == "unified_can_lin_host_tool" else None,
    )

    assert get_tool_identity() == ToolIdentity(
        "0.2.0",
        "01" * 20,
        "2026-07-14T12:00:00Z",
        "owner/ecu-firmware-release-tool",
        True,
    )
    assert calls == {"joinpath": 1, "read_bytes": 1}


def test_official_identity_requires_matching_installed_version():
    raw = _official_payload()

    assert load_tool_identity(raw, installed_version="0.2.0").official_build is True
    with pytest.raises(ValueError, match="版本"):
        load_tool_identity(raw, installed_version="0.2.1")


def test_development_identity_never_claims_official_repository():
    identity = load_tool_identity(None, installed_version="0.2.0")

    assert identity == ToolIdentity("0.2.0", "development", "", "", False)
    assert identity.short_commit == "development"


def test_embedded_development_identity_requires_empty_repository():
    identity = load_tool_identity(
        _official_payload(officialBuild=False, repository=""),
        installed_version="0.2.0",
    )

    assert identity.official_build is False
    assert identity.repository == ""
    with pytest.raises(ValueError, match="仓库"):
        load_tool_identity(
            _official_payload(officialBuild=False), installed_version="0.2.0"
        )


def test_official_identity_exposes_short_commit():
    identity = load_tool_identity(_official_payload(), installed_version="0.2.0")

    assert identity.short_commit == "0101010"


def test_tool_identity_rejects_duplicate_json_key():
    raw = (
        b'{"version":"0.2.0","version":"0.2.0",'
        b'"commit":"0101010101010101010101010101010101010101",'
        b'"buildTimeUtc":"2026-07-14T12:00:00Z",'
        b'"repository":"owner/ecu-firmware-release-tool","officialBuild":true}'
    )

    with pytest.raises(ValueError, match="重复"):
        load_tool_identity(raw, installed_version="0.2.0")


def test_tool_identity_rejects_unknown_or_missing_fields():
    with pytest.raises(ValueError, match="字段"):
        load_tool_identity(
            _official_payload(extra="not allowed"), installed_version="0.2.0"
        )

    payload = json.loads(_official_payload())
    payload.pop("commit")
    with pytest.raises(ValueError, match="字段"):
        load_tool_identity(json.dumps(payload).encode(), installed_version="0.2.0")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"version": 2}, "版本"),
        ({"version": "01.2.0"}, "版本"),
        ({"commit": 1}, "提交"),
        ({"commit": "A1" * 20}, "提交"),
        ({"commit": "01" * 19}, "提交"),
        ({"buildTimeUtc": 1}, "时间"),
        ({"buildTimeUtc": "2026-07-14T12:00:00+00:00"}, "时间"),
        ({"buildTimeUtc": "2026-02-30T12:00:00Z"}, "时间"),
        ({"repository": 1}, "仓库"),
        ({"repository": "https://github.com/owner/ecu-firmware-release-tool"}, "仓库"),
        ({"repository": "owner/another-repository"}, "仓库"),
        ({"officialBuild": 1}, "officialBuild"),
    ],
)
def test_tool_identity_rejects_wrong_field_type_or_format(changes, message):
    with pytest.raises(ValueError, match=message):
        load_tool_identity(_official_payload(**changes), installed_version="0.2.0")


def test_tool_identity_requires_a_json_object():
    with pytest.raises(ValueError, match="对象"):
        load_tool_identity(b"[]", installed_version="0.2.0")
