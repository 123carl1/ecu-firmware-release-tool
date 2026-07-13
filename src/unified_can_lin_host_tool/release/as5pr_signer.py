"""AS5PR 开发签名的固定字节契约。"""

from dataclasses import dataclass, replace
import hashlib
import hmac
import os
from pathlib import Path
import struct
import tempfile

from .artifact_identity import compute_artifact_id, compute_signed_artifact_id
from .inspector import InspectedArtifact


@dataclass(frozen=True)
class As5prSignPolicy:
    target_id: int
    version: int
    manifest_bundle_sha256: bytes
    sign_policy_id: str
    magic: int = 0xA5A5A5A5


@dataclass(frozen=True)
class SignedArtifact:
    artifact: InspectedArtifact
    signed_bytes: bytes
    signed_file_sha256: bytes
    auth_block: bytes
    auth_block_sha256: bytes
    manifest_bundle_sha256: bytes
    signed_artifact_id: str


def _key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("development signing key must be exactly 32 bytes")


def _validate_policy(artifact: InspectedArtifact, policy: As5prSignPolicy) -> None:
    if artifact.identity.target_id != policy.target_id:
        raise ValueError("artifact target identity does not match signing policy")
    if artifact.identity.sign_policy_id != policy.sign_policy_id:
        raise ValueError("artifact signing policy identity does not match manifest policy")
    if len(policy.manifest_bundle_sha256) != 32:
        raise ValueError("manifest bundle hash must be exactly 32 bytes")
    for value, name in ((policy.magic, "magic"), (policy.target_id, "target_id"),
                        (policy.version, "version")):
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"{name} must be a u32")


def sign_as5pr(artifact: InspectedArtifact, policy: As5prSignPolicy, key: bytes) -> SignedArtifact:
    _key(key)
    _validate_policy(artifact, policy)
    if compute_artifact_id(artifact.identity) != artifact.artifact_id:
        raise ValueError("artifact identity is inconsistent")
    payload = artifact.normalized_payload
    if hashlib.sha256(payload).digest() != artifact.identity.normalized_payload_sha256:
        raise ValueError("normalized payload is inconsistent with artifact identity")
    header = struct.pack("<IIII", policy.magic, len(payload), policy.target_id, policy.version)
    auth_block = header + hmac.new(key, payload + header, hashlib.sha256).digest()
    signed_bytes = payload + auth_block
    signed_hash = hashlib.sha256(signed_bytes).digest()
    auth_hash = hashlib.sha256(auth_block).digest()
    signed_id = compute_signed_artifact_id(artifact.artifact_id, signed_hash, auth_hash,
                                           policy.manifest_bundle_sha256)
    return SignedArtifact(artifact, signed_bytes, signed_hash, auth_block, auth_hash,
                          policy.manifest_bundle_sha256, signed_id)


def verify_as5pr(signed: SignedArtifact, policy: As5prSignPolicy, key: bytes) -> None:
    _key(key)
    _validate_policy(signed.artifact, policy)
    expected = sign_as5pr(signed.artifact, policy, key)
    if len(signed.auth_block) != 48:
        raise ValueError("AS5PR authentication block must be 48 bytes")
    checks = (
        hmac.compare_digest(signed.auth_block, expected.auth_block),
        hmac.compare_digest(signed.signed_bytes, expected.signed_bytes),
        hmac.compare_digest(signed.signed_file_sha256, expected.signed_file_sha256),
        hmac.compare_digest(signed.auth_block_sha256, expected.auth_block_sha256),
        hmac.compare_digest(signed.manifest_bundle_sha256, expected.manifest_bundle_sha256),
        hmac.compare_digest(signed.signed_artifact_id, expected.signed_artifact_id),
    )
    if not all(checks):
        raise ValueError("signed artifact verification failed")


def write_signed_as5pr(path: Path, signed: SignedArtifact, policy: As5prSignPolicy,
                       key: bytes) -> None:
    """在目标同目录写临时文件，复读并认证成功后原子替换。"""
    verify_as5pr(signed, policy, key)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp",
                                            dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(signed.signed_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        reread = temporary.read_bytes()
        candidate = replace(signed, signed_bytes=reread,
                            signed_file_sha256=hashlib.sha256(reread).digest(),
                            auth_block=reread[-48:],
                            auth_block_sha256=hashlib.sha256(reread[-48:]).digest())
        verify_as5pr(candidate, policy, key)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
