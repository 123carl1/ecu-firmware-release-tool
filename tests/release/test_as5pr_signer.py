from dataclasses import FrozenInstanceError, replace
import hashlib
import hmac
from pathlib import Path
import struct
from types import MappingProxyType
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import pytest
import unified_can_lin_host_tool.release.as5pr_signer as signer_module

from unified_can_lin_host_tool.core.errors import HostToolError
from unified_can_lin_host_tool.release.artifact_identity import compute_artifact_id
from unified_can_lin_host_tool.release.as5pr_signer import (
    As5prSignPolicy, SignedArtifact, sign_as5pr, verify_as5pr, write_signed_as5pr,
)
from unified_can_lin_host_tool.release.inspector import InspectedArtifact
from unified_can_lin_host_tool.release.models import ArtifactIdentityV1, Segment
from unified_can_lin_host_tool.release.manifest import ReleaseManifest, load_verified_manifest


KEY = bytes(range(32))
PAYLOAD = b"\x01\x02\xff\x04"
_VERIFIED_MANIFEST = None


def artifact(target_id: int = 0x41503541, sign_policy_id: str = "hmac-v1") -> InspectedArtifact:
    source_path = Path(__file__).resolve()
    source_hash = hashlib.sha256(source_path.read_bytes()).digest()
    identity = ArtifactIdentityV1(target_id, "bundle-1", "as5pr", "1", b"\x00" * 32,
        sign_policy_id, source_hash, 0x7000, 0x7004, 0xFF,
        (Segment(0x7000, PAYLOAD),), hashlib.sha256(PAYLOAD).digest())
    return InspectedArtifact(source_path, source_hash, identity.segments, PAYLOAD,
                             identity, compute_artifact_id(identity))


def manifest(**changes: object) -> ReleaseManifest:
    values = dict(bundle_root=Path("."), manifest_bytes=b"verified manifest",
        manifest_sha256=hashlib.sha256(b"verified manifest").hexdigest(), schema_version=1,
        bundle_id="bundle-1", target_id="fm33ht-as5pr", project_id="AS5PR", version="1",
        source=MappingProxyType({}), memory=MappingProxyType({}), normalization=MappingProxyType({}),
        authentication=MappingProxyType({"formatVersion": 0, "signPolicyId": "hmac-v1",
                                         "magic": 0xA5A5A5A5}),
        workflow=MappingProxyType({}), resources=MappingProxyType({}))
    values.update(changes)
    return ReleaseManifest(**values)


@pytest.fixture(scope="session", autouse=True)
def verified_manifest(tmp_path_factory: pytest.TempPathFactory) -> None:
    global _VERIFIED_MANIFEST
    root = tmp_path_factory.mktemp("signer-bundle")
    resources = {}
    for resource_id in ("profile", "boot", "flash_driver"):
        data = resource_id.encode()
        path = root / f"{resource_id}.bin"
        path.write_bytes(data)
        resources[resource_id] = {"path": path.name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "kind": resource_id,
            "bundleId": "bundle-1", "targetId": "fm33ht-as5pr"}
    document = {"schemaVersion": 1, "bundleId": "bundle-1", "targetId": "fm33ht-as5pr",
        "projectId": "AS5PR", "version": "1", "source": {"commit": "x", "dirty": False,
        "toolchain": "x", "configHash": "x", "builtAt": "x"},
        "memory": {"appStart": 0x7000, "appEnd": 0x7004, "pageSize": 512,
        "flashDriverRam": 0x20001000, "flashDriverMaxSize": 8192},
        "normalization": {"start": 0x7000, "end": 0x7004, "gapFill": 0xFF},
        "authentication": {"formatVersion": 0, "signPolicyId": "hmac-v1", "keyId": "dev",
                           "magic": 0xA5A5A5A5},
        "workflow": {"name": "can-ota", "version": 1}, "resources": resources}
    raw = yaml.safe_dump(document, sort_keys=False).encode()
    private = Ed25519PrivateKey.generate()
    (root / "manifest.yaml").write_bytes(raw)
    (root / "manifest.sig").write_bytes(private.sign(raw))
    _VERIFIED_MANIFEST = load_verified_manifest(root, private.public_key())


def policy() -> As5prSignPolicy:
    assert _VERIFIED_MANIFEST is not None
    return As5prSignPolicy.from_verified_manifest(_VERIFIED_MANIFEST)


def test_policy_cannot_be_constructed_without_verified_manifest() -> None:
    with pytest.raises(TypeError):
        As5prSignPolicy(target_id=0x41503541, version=0,  # type: ignore[call-arg]
                        manifest_bundle_sha256=b"\x55" * 32,
                        sign_policy_id="hmac-v1", magic=0xA5A5A5A5)


@pytest.mark.parametrize("change", [
    {"target_id": "other"},
    {"authentication": MappingProxyType({"formatVersion": 0, "signPolicyId": "other",
                                           "magic": 0xA5A5A5A5})},
    {"authentication": MappingProxyType({"formatVersion": 0, "signPolicyId": "hmac-v1",
                                           "magic": 1})},
])
def test_ordinary_manifest_cannot_create_sign_policy(change: dict[str, object]) -> None:
    with pytest.raises(TypeError): As5prSignPolicy.from_verified_manifest(manifest(**change))


def test_policy_manifest_hash_is_computed_from_verified_raw_bytes() -> None:
    assert _VERIFIED_MANIFEST is not None
    derived = As5prSignPolicy.from_verified_manifest(_VERIFIED_MANIFEST)
    assert derived.manifest_bundle_sha256 == hashlib.sha256(_VERIFIED_MANIFEST.manifest_bytes).digest()
    with pytest.raises(TypeError):
        As5prSignPolicy.from_verified_manifest(_VERIFIED_MANIFEST, b"\x55" * 32)  # type: ignore[call-arg]


def test_matches_as5pr_reference_vector() -> None:
    signed = sign_as5pr(artifact(), policy(), KEY)
    header = struct.pack("<IIII", 0xA5A5A5A5, len(PAYLOAD), 0x41503541, 0)
    assert signed.auth_block == header + hmac.new(KEY, PAYLOAD + header, hashlib.sha256).digest()
    assert signed.signed_bytes == PAYLOAD + signed.auth_block
    verify_as5pr(signed, policy(), KEY)


@pytest.mark.parametrize("field", ["magic", "payload_size", "target_id", "version", "digest"])
def test_rejects_each_corrupt_auth_field(field: str) -> None:
    signed = sign_as5pr(artifact(), policy(), KEY)
    block = bytearray(signed.auth_block)
    offsets = {"magic": 0, "payload_size": 4, "target_id": 8, "version": 12, "digest": 16}
    block[offsets[field]] ^= 1
    with pytest.raises(ValueError):
        verify_as5pr(replace(signed, auth_block=bytes(block)), policy(), KEY)


def test_rejects_hmac_over_payload_only() -> None:
    signed = sign_as5pr(artifact(), policy(), KEY)
    wrong = signed.auth_block[:16] + hmac.new(KEY, PAYLOAD, hashlib.sha256).digest()
    with pytest.raises(ValueError): verify_as5pr(replace(signed, auth_block=wrong), policy(), KEY)


@pytest.mark.parametrize("key", [b"", b"x" * 31, b"x" * 33])
def test_requires_32_byte_key_without_leaking_it(key: bytes) -> None:
    with pytest.raises(ValueError) as caught: sign_as5pr(artifact(), policy(), key)
    if key:
        assert key.hex() not in str(caught.value)


def test_rejects_policy_and_artifact_identity_mismatch() -> None:
    with pytest.raises(ValueError): sign_as5pr(artifact(target_id=1), policy(), KEY)
    with pytest.raises(ValueError): sign_as5pr(artifact(sign_policy_id="other"), policy(), KEY)


@pytest.mark.parametrize("field", ["source_file_sha256", "segments", "normalized_payload"])
def test_rejects_forged_inspected_artifact_fields(field: str) -> None:
    original = artifact()
    forged = {
        "source_file_sha256": b"\x22" * 32,
        "segments": (Segment(0x7000, b"evil"),),
        "normalized_payload": b"evil",
    }[field]
    with pytest.raises(ValueError): sign_as5pr(replace(original, **{field: forged}), policy(), KEY)


def test_rejects_identity_segments_that_do_not_match_payload_segments() -> None:
    original = artifact()
    forged_identity = replace(original.identity, segments=(Segment(0x7000, b"evil"),))
    forged = replace(original, identity=forged_identity,
                     artifact_id=compute_artifact_id(forged_identity))
    with pytest.raises(ValueError): sign_as5pr(forged, policy(), KEY)


@pytest.mark.parametrize("field", ["signed_file_sha256", "auth_block_sha256",
                                     "manifest_bundle_sha256", "signed_artifact_id"])
def test_rejects_tampered_derived_fields(field: str) -> None:
    signed = sign_as5pr(artifact(), policy(), KEY)
    value = "0" * 64 if field == "signed_artifact_id" else b"\x00" * 32
    with pytest.raises(ValueError): verify_as5pr(replace(signed, **{field: value}), policy(), KEY)


def test_models_are_frozen_and_repr_contains_no_key() -> None:
    signed = sign_as5pr(artifact(), policy(), KEY)
    with pytest.raises(FrozenInstanceError): signed.signed_bytes = b""  # type: ignore[misc]
    assert KEY.hex() not in repr(signed)
    assert KEY.hex() not in repr(policy())



def artifact_with_source(path: Path) -> InspectedArtifact:
    original = artifact()
    source_hash = hashlib.sha256(path.read_bytes()).digest()
    identity = replace(original.identity, source_file_sha256=source_hash)
    return replace(original, source_path=path, source_file_sha256=source_hash,
                   identity=identity, artifact_id=compute_artifact_id(identity))


def test_sign_rejects_changed_or_deleted_source(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"original")
    inspected = artifact_with_source(source)
    source.write_bytes(b"changed")
    with pytest.raises(HostToolError, match="source changed"):
        sign_as5pr(inspected, policy(), KEY)
    source.unlink()
    with pytest.raises(HostToolError, match="source changed|unavailable"):
        sign_as5pr(inspected, policy(), KEY)


def test_verify_uses_signed_snapshot_after_source_is_deleted(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"original")
    signed = sign_as5pr(artifact_with_source(source), policy(), KEY)
    source.unlink()
    verify_as5pr(signed, policy(), KEY)


def test_atomic_write_revalidates_source_before_output(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"original")
    inspected = artifact_with_source(source)
    signed = sign_as5pr(inspected, policy(), KEY)
    source.write_bytes(b"changed")
    target = tmp_path / "signed.bin"
    with pytest.raises(Exception, match="source changed"):
        write_signed_as5pr(target, signed, policy(), KEY)
    assert not target.exists()


def test_atomic_output_success_and_failure_preserves_old_target(tmp_path: Path) -> None:
    target = tmp_path / "signed.bin"
    target.write_bytes(b"old")
    signed = sign_as5pr(artifact(), policy(), KEY)
    write_signed_as5pr(target, signed, policy(), KEY)
    assert target.read_bytes() == signed.signed_bytes
    target.write_bytes(b"old-again")
    with pytest.raises(ValueError):
        write_signed_as5pr(target, replace(signed, signed_artifact_id="0" * 64), policy(), KEY)
    assert target.read_bytes() == b"old-again"
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_output_reread_verification_failure_preserves_old_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "signed.bin"
    target.write_bytes(b"old")
    signed = sign_as5pr(artifact(), policy(), KEY)
    original_verify = signer_module.verify_as5pr
    calls = 0

    def fail_second_verification(*args: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("simulated reread verification failure")
        original_verify(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(signer_module, "verify_as5pr", fail_second_verification)
    with pytest.raises(ValueError, match="simulated reread"):
        write_signed_as5pr(target, signed, policy(), KEY)
    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob("*.tmp")) == []
