from dataclasses import FrozenInstanceError, replace
import hashlib
import hmac
from pathlib import Path
import struct

import pytest
import unified_can_lin_host_tool.release.as5pr_signer as signer_module

from unified_can_lin_host_tool.release.artifact_identity import compute_artifact_id
from unified_can_lin_host_tool.release.as5pr_signer import (
    As5prSignPolicy, SignedArtifact, sign_as5pr, verify_as5pr, write_signed_as5pr,
)
from unified_can_lin_host_tool.release.inspector import InspectedArtifact
from unified_can_lin_host_tool.release.models import ArtifactIdentityV1, Segment


KEY = bytes(range(32))
PAYLOAD = b"\x01\x02\xff\x04"
MANIFEST_HASH = b"\x55" * 32


def artifact(target_id: int = 0x41503541, sign_policy_id: str = "hmac-v1") -> InspectedArtifact:
    identity = ArtifactIdentityV1(target_id, "bundle-1", "as5pr", "1", b"\x00" * 32,
        sign_policy_id, b"\x11" * 32, 0x7000, 0x7004, 0xFF,
        (Segment(0x7000, PAYLOAD),), hashlib.sha256(PAYLOAD).digest())
    return InspectedArtifact(Path("app.bin"), b"\x11" * 32, identity.segments, PAYLOAD,
                             identity, compute_artifact_id(identity))


def policy(**changes: object) -> As5prSignPolicy:
    values = dict(target_id=0x41503541, version=0, manifest_bundle_sha256=MANIFEST_HASH,
                  sign_policy_id="hmac-v1", magic=0xA5A5A5A5)
    values.update(changes)
    return As5prSignPolicy(**values)


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
