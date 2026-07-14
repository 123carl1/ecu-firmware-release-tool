from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from unified_can_lin_host_tool.tool_identity import ToolIdentity
from unified_can_lin_host_tool.update.errors import UpdateIntegrityError, UpdateSecurityError
from unified_can_lin_host_tool.update.metadata import InstallerAsset, UpdateInfo
from unified_can_lin_host_tool.update.service import UpdateService, installer_arguments
from unified_can_lin_host_tool.versioning import SemanticVersion


class FakeSource:
    def __init__(self, info: UpdateInfo):
        self.info = info
        self.calls = []

    def fetch(self, public_keys):
        self.calls.append(public_keys)
        return self.info


class FakeHttp:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = []

    def iter_bytes(self, url: str, *, max_bytes: int):
        self.calls.append((url, max_bytes))
        midpoint = max(1, len(self.payload) // 2)
        yield self.payload[:midpoint]
        yield self.payload[midpoint:]


@pytest.fixture
def official_identity() -> ToolIdentity:
    return ToolIdentity(
        "0.2.0",
        "01" * 20,
        "2026-07-14T12:00:00Z",
        "o/ecu-firmware-release-tool",
        True,
    )


@pytest.fixture
def payload() -> bytes:
    return b"signed installer payload"


@pytest.fixture
def update_info(payload: bytes) -> UpdateInfo:
    return make_update_info("0.2.1", payload)


def make_update_info(version: str, payload: bytes) -> UpdateInfo:
    parsed = SemanticVersion.parse(version)
    return UpdateInfo(
        repository="o/ecu-firmware-release-tool",
        version=parsed,
        tag=f"v{parsed}",
        commit="02" * 20,
        generated_at="2026-07-14T13:00:00Z",
        channel="stable",
        release_notes="安全更新",
        installer=InstallerAsset(
            name=f"EcuReleaseTool_Setup_{parsed}.exe",
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
        verified_key_id="release-v1",
    )


def test_check_only_returns_strictly_newer_stable_release(
    official_identity: ToolIdentity, payload: bytes, tmp_path: Path
):
    source = FakeSource(make_update_info("0.2.0", payload))
    service = UpdateService(official_identity, source, FakeHttp(payload), tmp_path, {"k": b"x" * 32})

    assert service.check() is None
    source.info = make_update_info("0.1.9", payload)
    assert service.check() is None
    source.info = make_update_info("0.2.1", payload)
    assert service.check().version == SemanticVersion.parse("0.2.1")


def test_development_identity_does_not_access_release_source(payload: bytes, tmp_path: Path):
    identity = ToolIdentity("0.2.0", "development", "", "", False)
    source = FakeSource(make_update_info("0.2.1", payload))

    assert UpdateService(identity, source, FakeHttp(payload), tmp_path, {}).check() is None
    assert source.calls == []


def test_check_rejects_source_result_outside_fixed_repository(
    official_identity: ToolIdentity, update_info: UpdateInfo, payload: bytes, tmp_path: Path
):
    source = FakeSource(replace(update_info, repository="attacker/ecu-firmware-release-tool"))
    service = UpdateService(official_identity, source, FakeHttp(payload), tmp_path, {})

    with pytest.raises(UpdateSecurityError, match="仓库"):
        service.check()


def test_download_writes_part_then_atomically_renames(
    official_identity: ToolIdentity, update_info: UpdateInfo, payload: bytes, tmp_path: Path
):
    http = FakeHttp(payload)
    service = UpdateService(official_identity, FakeSource(update_info), http, tmp_path, {})
    progress = []

    path = service.download(update_info, progress=lambda current, total: progress.append((current, total)))

    assert path == tmp_path / "0.2.1" / "EcuReleaseTool_Setup_0.2.1.exe"
    assert path.read_bytes() == payload
    assert not list(path.parent.glob("*.part"))
    assert http.calls == [(
        "https://github.com/o/ecu-firmware-release-tool/releases/download/v0.2.1/"
        "EcuReleaseTool_Setup_0.2.1.exe",
        len(payload),
    )]
    assert progress[-1] == (len(payload), len(payload))


def test_valid_cached_installer_is_reused_without_network(
    official_identity: ToolIdentity, update_info: UpdateInfo, payload: bytes, tmp_path: Path
):
    cached = tmp_path / "0.2.1" / update_info.installer.name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(payload)
    http = FakeHttp(b"must not download")

    path = UpdateService(official_identity, FakeSource(update_info), http, tmp_path, {}).download(update_info)

    assert path == cached
    assert http.calls == []


def test_invalid_cached_installer_is_removed_and_replaced(
    official_identity: ToolIdentity, update_info: UpdateInfo, payload: bytes, tmp_path: Path
):
    cached = tmp_path / "0.2.1" / update_info.installer.name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"damaged")
    http = FakeHttp(payload)

    path = UpdateService(official_identity, FakeSource(update_info), http, tmp_path, {}).download(update_info)

    assert path.read_bytes() == payload
    assert len(http.calls) == 1


def test_wrong_hash_never_leaves_executable_cache(
    official_identity: ToolIdentity, update_info: UpdateInfo, payload: bytes, tmp_path: Path
):
    damaged = replace(update_info, installer=replace(update_info.installer, sha256="00" * 32))
    service = UpdateService(official_identity, FakeSource(damaged), FakeHttp(payload), tmp_path, {})

    with pytest.raises(UpdateIntegrityError, match="SHA-256"):
        service.download(damaged)

    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize(
    ("payload_transform", "cancelled", "message"),
    [
        (lambda payload: payload[:-1], lambda: False, "大小"),
        (lambda payload: payload + b"extra", lambda: False, "大小"),
        (lambda payload: payload, lambda: True, "取消"),
    ],
    ids=["truncated", "oversized", "cancelled"],
)
def test_incomplete_or_cancelled_download_leaves_no_executable_or_part(
    official_identity: ToolIdentity,
    update_info: UpdateInfo,
    payload: bytes,
    tmp_path: Path,
    payload_transform,
    cancelled,
    message: str,
):
    service = UpdateService(
        official_identity,
        FakeSource(update_info),
        FakeHttp(payload_transform(payload)),
        tmp_path,
        {},
    )

    with pytest.raises(UpdateIntegrityError, match=message):
        service.download(update_info, cancelled=cancelled)

    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.part"))


def test_installer_arguments_use_decimal_pid_and_absolute_log_path(tmp_path: Path):
    log_path = tmp_path / "installer.log"

    assert installer_arguments(parent_pid=1234, log_path=log_path) == [
        "/SILENT",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/AUTO_UPDATE",
        "/PARENT_PID=1234",
        f"/LOG={log_path.resolve()}",
    ]
