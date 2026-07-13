import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from unified_can_lin_host_tool.release.as5pr_signer import As5prSignPolicy, sign_as5pr
from unified_can_lin_host_tool.release.composer import ComposePolicy, compose_full_image
from unified_can_lin_host_tool.release.image_parser import parse_image
from unified_can_lin_host_tool.release.inspector import InspectionContext, inspect_artifact
from unified_can_lin_host_tool.release.manifest import load_verified_manifest


KEY = bytes(range(32))


def _inputs(tmp_path: Path, *, allow_offline_preset: bool = True):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    boot = b"BOOT"
    resources = {}
    for name, payload in (("profile", b"profile"), ("boot", boot),
                          ("flash_driver", b"driver")):
        (bundle / f"{name}.bin").write_bytes(payload)
        resources[name] = {"path": f"{name}.bin", "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "kind": name,
            "bundleId": "bundle-1", "targetId": "fm33ht-as5pr"}
    document = {"schemaVersion": 1, "bundleId": "bundle-1",
        "targetId": "fm33ht-as5pr", "projectId": "AS5PR", "version": "1",
        "source": {"commit": "x", "dirty": False, "toolchain": "x",
                   "configHash": "x", "builtAt": "x"},
        "memory": {"appStart": 0x7000, "appEnd": 0x7100, "pageSize": 4,
                   "flashDriverRam": 0x20001000, "flashDriverMaxSize": 8192,
                   "bootStart": 0, "appValid": {"start": 0x6800, "size": 8,
                   "fieldOffset": 0, "validValue": 0x5AA55AA5,
                   "byteOrder": "little", "reservedFill": 0xFF,
                   "erasedFill": 0xFF, "allowOfflinePreset": allow_offline_preset}},
        "normalization": {"start": 0x7000, "end": 0x7004, "gapFill": 0xFF},
        "authentication": {"formatVersion": 0, "signPolicyId": "hmac-v1",
                           "keyId": "dev", "magic": 0xA5A5A5A5},
        "workflow": {"name": "can-ota", "version": 1}, "resources": resources}
    raw = yaml.safe_dump(document, sort_keys=False).encode()
    private = Ed25519PrivateKey.generate()
    (bundle / "manifest.yaml").write_bytes(raw)
    (bundle / "manifest.sig").write_bytes(private.sign(raw))
    manifest = load_verified_manifest(bundle, private.public_key())
    policy = As5prSignPolicy.from_verified_manifest(manifest)
    source = tmp_path / "app.bin"
    source.write_bytes(b"\x01\x02\x03\x04")
    inspected = inspect_artifact(source, InspectionContext(
        0x41503541, "bundle-1", "as5pr", "1", b"\0" * 32,
        "hmac-v1", 0x7000, 0x7004, 0xFF))
    return manifest, policy, sign_as5pr(inspected, policy, KEY), boot


def test_valid_app_composes_hex_and_s19_and_rereads_exact_bytes(tmp_path: Path) -> None:
    manifest, policy, signed, boot = _inputs(tmp_path)
    result = compose_full_image(tmp_path / "out", signed, manifest, policy, KEY,
                                ComposePolicy.VALID_APP)
    assert result.app_valid_state == "offline-prevalidated-image"
    assert result.hex_path.exists() and result.s19_path.exists()
    expected = {
        0: boot,
        0x6800: (0x5AA55AA5).to_bytes(4, "little") + b"\xff" * 4,
        0x7000: signed.signed_bytes,
    }
    for path in (result.hex_path, result.s19_path):
        actual = {segment.address: segment.data for segment in parse_image(path)}
        assert actual == expected
        assert result.output_sha256[path.suffix] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_erased_policy_never_presets_app_valid(tmp_path: Path) -> None:
    manifest, policy, signed, _ = _inputs(tmp_path)
    result = compose_full_image(tmp_path / "out", signed, manifest, policy, KEY,
                                ComposePolicy.ERASED_APP_VALID)
    assert result.app_valid_state == "erased"
    segments = {segment.address: segment.data for segment in parse_image(result.hex_path)}
    assert segments[0x6800] == b"\xff" * 8


def test_valid_policy_rejects_tampered_signed_artifact(tmp_path: Path) -> None:
    manifest, policy, signed, _ = _inputs(tmp_path)
    bad = signed.__class__(signed.artifact, signed.signed_bytes[:-1] + b"X",
        signed.signed_file_sha256, signed.auth_block, signed.auth_block_sha256,
        signed.manifest_bundle_sha256, signed.signed_artifact_id)
    with pytest.raises(ValueError):
        compose_full_image(tmp_path / "out", bad, manifest, policy, KEY,
                           ComposePolicy.VALID_APP)
    assert not (tmp_path / "out").exists()


def test_tampered_signed_artifact_can_only_produce_erased_app_valid(tmp_path: Path) -> None:
    manifest, policy, signed, _ = _inputs(tmp_path)
    bad = signed.__class__(signed.artifact, signed.signed_bytes[:-1] + b"X",
        signed.signed_file_sha256, signed.auth_block, signed.auth_block_sha256,
        signed.manifest_bundle_sha256, signed.signed_artifact_id)
    result = compose_full_image(tmp_path / "out", bad, manifest, policy, KEY,
                                ComposePolicy.ERASED_APP_VALID)
    assert result.app_valid_state == "erased"
    segments = {segment.address: segment.data for segment in parse_image(result.hex_path)}
    assert segments[0x6800] == b"\xff" * 8


def test_valid_policy_requires_offline_preset_permission(tmp_path: Path) -> None:
    manifest, policy, signed, _ = _inputs(tmp_path, allow_offline_preset=False)
    with pytest.raises(ValueError, match="offline"):
        compose_full_image(tmp_path / "out", signed, manifest, policy, KEY,
                           ComposePolicy.VALID_APP)


def test_composer_rejects_relocation_against_manifest_app_start(tmp_path: Path) -> None:
    manifest, policy, signed, _ = _inputs(tmp_path)
    forged_identity = replace(signed.artifact.identity, normalization_start=0x7100,
                              normalization_end=0x7104)
    forged_artifact = replace(signed.artifact, identity=forged_identity)
    forged = replace(signed, artifact=forged_artifact)
    with pytest.raises(ValueError, match="normalization start"):
        compose_full_image(tmp_path / "out", forged, manifest, policy, KEY,
                           ComposePolicy.ERASED_APP_VALID)
