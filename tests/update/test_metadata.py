from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unified_can_lin_host_tool.update.errors import UpdateMetadataError, UpdateSecurityError
from unified_can_lin_host_tool.update.metadata import parse_locator_tag, verify_signed_update


@pytest.fixture
def ed25519_key_pair() -> tuple[Ed25519PrivateKey, bytes]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_key, public_key


def signed_update(private_key: Ed25519PrivateKey, **changes: object) -> tuple[bytes, bytes]:
    payload = {
        "schemaVersion": 1,
        "repository": "owner/ecu-firmware-release-tool",
        "version": "0.2.1",
        "tag": "v0.2.1",
        "commit": "01" * 20,
        "generatedAt": "2026-07-14T12:00:00Z",
        "channel": "stable",
        "releaseNotes": "修复更新检查。",
        "installer": {
            "name": "EcuReleaseTool_Setup_0.2.1.exe",
            "size": 123,
            "sha256": "ab" * 32,
        },
        "keyId": "test-v1",
    } | changes
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return raw, private_key.sign(raw)


def test_signature_is_verified_before_key_id_is_trusted(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(private_key)

    info = verify_signed_update(
        raw,
        signature,
        {"test-v1": public_key},
        "owner/ecu-firmware-release-tool",
    )

    assert str(info.version) == "0.2.1"
    assert info.verified_key_id == "test-v1"
    assert info.installer.size == 123


def test_bad_signature_is_rejected(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(private_key)

    with pytest.raises(UpdateSecurityError, match="签名"):
        verify_signed_update(
            raw + b" ",
            signature,
            {"test-v1": public_key},
            "owner/ecu-firmware-release-tool",
        )


def test_duplicate_json_key_is_rejected(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw = b'{"schemaVersion":1,"schemaVersion":1}'

    with pytest.raises(UpdateMetadataError, match="重复"):
        verify_signed_update(
            raw,
            private_key.sign(raw),
            {"test-v1": public_key},
            "owner/ecu-firmware-release-tool",
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"keyId": "other-v1"}, "keyId"),
        ({"repository": "attacker/ecu-firmware-release-tool"}, "仓库"),
        ({"channel": "preview"}, "stable"),
        ({"releaseNotes": "更" * (16 * 1024 + 1)}, "说明"),
        ({"tag": "v0.2.2"}, "标签"),
        ({"commit": "A1" * 20}, "提交"),
        ({"installer": {"name": "EcuReleaseTool_Setup_0.2.2.exe", "size": 123, "sha256": "ab" * 32}}, "安装包"),
        ({"installer": {"name": "EcuReleaseTool_Setup_0.2.1.exe", "size": True, "sha256": "ab" * 32}}, "大小"),
        ({"installer": {"name": "EcuReleaseTool_Setup_0.2.1.exe", "size": 123, "sha256": "AB" * 32}}, "SHA-256"),
        ({"schemaVersion": True}, "schemaVersion"),
        ({"schemaVersion": 2}, "schemaVersion"),
        ({"unexpected": "value"}, "未知字段"),
    ],
)
def test_invalid_signed_fields_are_rejected(ed25519_key_pair, changes, message):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(private_key, **changes)

    with pytest.raises((UpdateMetadataError, UpdateSecurityError), match=message):
        verify_signed_update(
            raw,
            signature,
            {"test-v1": public_key},
            "owner/ecu-firmware-release-tool",
        )


def test_unknown_installer_field_is_rejected(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(
        private_key,
        installer={
            "name": "EcuReleaseTool_Setup_0.2.1.exe",
            "size": 123,
            "sha256": "ab" * 32,
            "url": "https://attacker.invalid/setup.exe",
        },
    )

    with pytest.raises(UpdateMetadataError, match="未知字段"):
        verify_signed_update(raw, signature, {"test-v1": public_key}, "owner/ecu-firmware-release-tool")


@pytest.mark.parametrize(
    ("raw", "signature", "keys", "message"),
    [
        (b"{}", b"x" * 63, {"test-v1": b"x" * 32}, "64"),
        (b"x" * (64 * 1024 + 1), b"x" * 64, {"test-v1": b"x" * 32}, "64 KiB"),
        (b"{}", b"x" * 64, {f"key-{index}": bytes([index]) * 32 for index in range(5)}, "4"),
        (b"{}", b"x" * 64, {"key-1": b"x" * 31}, "32"),
        (b"{}", b"x" * 64, {"key-1": b"x" * 32, "key-2": b"x" * 32}, "重复"),
    ],
    ids=["signature-size", "json-size", "key-count", "public-key-size", "duplicate-public-key"],
)
def test_verification_input_limits_are_enforced(raw, signature, keys, message):
    with pytest.raises((UpdateMetadataError, UpdateSecurityError), match=message):
        verify_signed_update(raw, signature, keys, "owner/ecu-firmware-release-tool")


def test_non_utf8_signed_json_is_rejected_after_signature_verification(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw = b"\xff"

    with pytest.raises(UpdateMetadataError, match="UTF-8"):
        verify_signed_update(raw, private_key.sign(raw), {"test-v1": public_key}, "owner/ecu-firmware-release-tool")


def test_unpaired_surrogate_is_reported_as_stable_metadata_error(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    payload = {
        "schemaVersion": 1,
        "repository": "owner/ecu-firmware-release-tool",
        "version": "0.2.1",
        "tag": "v0.2.1",
        "commit": "01" * 20,
        "generatedAt": "2026-07-14T12:00:00Z",
        "channel": "stable",
        "releaseNotes": "\ud800",
        "installer": {
            "name": "EcuReleaseTool_Setup_0.2.1.exe",
            "size": 123,
            "sha256": "ab" * 32,
        },
        "keyId": "test-v1",
    }
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")

    with pytest.raises(UpdateMetadataError, match="UTF-8") as raised:
        verify_signed_update(
            raw,
            private_key.sign(raw),
            {"test-v1": public_key},
            "owner/ecu-firmware-release-tool",
        )

    assert raised.value.code == "UPDATE_METADATA_INVALID"


def test_generated_at_must_be_a_real_utc_second(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(private_key, generatedAt="2026-99-99T99:99:99Z")

    with pytest.raises(UpdateMetadataError, match="generatedAt"):
        verify_signed_update(
            raw,
            signature,
            {"test-v1": public_key},
            "owner/ecu-firmware-release-tool",
        )


def test_locator_only_extracts_a_strict_tag():
    raw = json.dumps({"tag": "v0.2.1", "repository": "untrusted/value"}).encode()
    assert parse_locator_tag(raw) == "v0.2.1"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"tag":"0.2.1"}',
        b'{"tag":"v00.2.1"}',
        b'{"tag":"v65536.0.0"}',
        b'{"tag":true}',
        b'{"tag":"v0.2.1","tag":"v0.2.2"}',
        b"\xff",
        b"x" * (64 * 1024 + 1),
    ],
    ids=["missing-v", "leading-zero", "segment-overflow", "bool-tag", "duplicate-tag", "non-utf8", "too-large"],
)
def test_locator_rejects_ambiguous_or_invalid_input(raw):
    with pytest.raises(UpdateMetadataError):
        parse_locator_tag(raw)


def test_error_classes_expose_stable_codes():
    from unified_can_lin_host_tool.update.errors import (
        UpdateBusyError,
        UpdateError,
        UpdateInstallerError,
        UpdateIntegrityError,
        UpdateNetworkError,
    )

    assert UpdateError("失败").code == "UPDATE_FAILED"
    assert UpdateMetadataError("失败").code == "UPDATE_METADATA_INVALID"
    assert UpdateSecurityError("失败").code == "UPDATE_SIGNATURE_INVALID"
    assert UpdateNetworkError("失败").code == "UPDATE_NETWORK_UNAVAILABLE"
    assert UpdateIntegrityError("失败").code == "UPDATE_INSTALLER_INTEGRITY_FAILED"
    assert UpdateBusyError("失败").code == "UPDATE_TOOL_BUSY"
    assert UpdateInstallerError("失败").code == "UPDATE_INSTALLER_START_FAILED"
