"""AS5PR 开发签名的固定字节契约。"""

from dataclasses import dataclass, replace
import hashlib
import hmac
import os
from pathlib import Path
import struct
import tempfile

from .artifact_identity import compute_artifact_id, compute_signed_artifact_id
from .image_parser import normalize_segments
from .inspector import InspectedArtifact, revalidate_source
from .manifest import VerifiedReleaseManifest


_POLICY_FACTORY_TOKEN = object()
_AS5PR_TARGET_IDS = {"fm33ht-as5pr": 0x41503541}


@dataclass(frozen=True, init=False)
class As5prSignPolicy:
    target_id: int
    version: int
    manifest_bundle_sha256: bytes
    sign_policy_id: str
    magic: int
    bundle_id: str
    manifest_sha256: bytes
    _binding_sha256: bytes

    def __init__(self, *, _token: object, target_id: int, version: int,
                 manifest_bundle_sha256: bytes, sign_policy_id: str, magic: int,
                 bundle_id: str, manifest_sha256: bytes) -> None:
        if _token is not _POLICY_FACTORY_TOKEN:
            raise TypeError("As5prSignPolicy must be created from a verified manifest")
        values = (struct.pack("<III", magic, target_id, version) + sign_policy_id.encode() + b"\0"
                  + bundle_id.encode() + b"\0" + manifest_bundle_sha256 + manifest_sha256)
        for name, value in (("target_id", target_id), ("version", version),
                            ("manifest_bundle_sha256", manifest_bundle_sha256),
                            ("sign_policy_id", sign_policy_id), ("magic", magic),
                            ("bundle_id", bundle_id), ("manifest_sha256", manifest_sha256),
                            ("_binding_sha256", hashlib.sha256(values).digest())):
            object.__setattr__(self, name, value)

    @classmethod
    def from_verified_manifest(cls, manifest: VerifiedReleaseManifest) -> "As5prSignPolicy":
        if not isinstance(manifest, VerifiedReleaseManifest):
            raise TypeError("manifest must be a VerifiedReleaseManifest")
        actual_manifest_hash = hashlib.sha256(manifest.manifest_bytes).digest()
        if manifest.manifest_sha256.lower() != actual_manifest_hash.hex():
            raise ValueError("verified manifest content hash is inconsistent")
        try:
            target_id = _AS5PR_TARGET_IDS[manifest.target_id]
            version = manifest.authentication["formatVersion"]
            sign_policy_id = manifest.authentication["signPolicyId"]
            magic = manifest.authentication["magic"]
        except KeyError as exc:
            raise ValueError("manifest lacks AS5PR signing policy fields") from exc
        if type(version) is not int or not isinstance(sign_policy_id, str) or type(magic) is not int:
            raise ValueError("manifest AS5PR signing policy fields have invalid types")
        if magic != 0xA5A5A5A5:
            raise ValueError("manifest AS5PR magic is unsupported")
        return cls(_token=_POLICY_FACTORY_TOKEN, target_id=target_id, version=version,
                   manifest_bundle_sha256=actual_manifest_hash,
                   sign_policy_id=sign_policy_id, magic=magic, bundle_id=manifest.bundle_id,
                   manifest_sha256=actual_manifest_hash)


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
    if not isinstance(policy, As5prSignPolicy):
        raise TypeError("policy must be As5prSignPolicy")
    bound = (struct.pack("<III", policy.magic, policy.target_id, policy.version)
             + policy.sign_policy_id.encode() + b"\0" + policy.bundle_id.encode() + b"\0"
             + policy.manifest_bundle_sha256 + policy.manifest_sha256)
    if not hmac.compare_digest(policy._binding_sha256, hashlib.sha256(bound).digest()):
        raise ValueError("signing policy is not bound to its verified manifest")
    if artifact.identity.target_id != policy.target_id:
        raise ValueError("artifact target identity does not match signing policy")
    if artifact.identity.sign_policy_id != policy.sign_policy_id:
        raise ValueError("artifact signing policy identity does not match manifest policy")
    if artifact.identity.bundle_id != policy.bundle_id:
        raise ValueError("artifact bundle identity does not match manifest")
    if len(policy.manifest_bundle_sha256) != 32:
        raise ValueError("manifest bundle hash must be exactly 32 bytes")
    for value, name in ((policy.magic, "magic"), (policy.target_id, "target_id"),
                        (policy.version, "version")):
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"{name} must be a u32")


def _validate_artifact(artifact: InspectedArtifact) -> None:
    identity = artifact.identity
    if artifact.source_file_sha256 != identity.source_file_sha256:
        raise ValueError("source hash is inconsistent with artifact identity")
    if artifact.segments != identity.segments:
        raise ValueError("artifact segments are inconsistent with artifact identity")
    normalized = normalize_segments(artifact.segments, start=identity.normalization_start,
                                    end=identity.normalization_end, gap_fill=identity.gap_fill)
    if not hmac.compare_digest(normalized, artifact.normalized_payload):
        raise ValueError("normalized payload is inconsistent with artifact segments")
    if not hmac.compare_digest(hashlib.sha256(normalized).digest(),
                               identity.normalized_payload_sha256):
        raise ValueError("normalized payload hash is inconsistent with artifact identity")
    if not hmac.compare_digest(compute_artifact_id(identity), artifact.artifact_id):
        raise ValueError("artifact identity is inconsistent")


def _build_signed(artifact: InspectedArtifact, policy: As5prSignPolicy, key: bytes) -> SignedArtifact:
    payload = artifact.normalized_payload
    header = struct.pack("<IIII", policy.magic, len(payload), policy.target_id, policy.version)
    auth_block = header + hmac.new(key, payload + header, hashlib.sha256).digest()
    signed_bytes = payload + auth_block
    signed_hash = hashlib.sha256(signed_bytes).digest()
    auth_hash = hashlib.sha256(auth_block).digest()
    signed_id = compute_signed_artifact_id(artifact.artifact_id, signed_hash, auth_hash,
                                           policy.manifest_bundle_sha256)
    return SignedArtifact(artifact, signed_bytes, signed_hash, auth_block, auth_hash,
                          policy.manifest_bundle_sha256, signed_id)



def sign_as5pr(artifact: InspectedArtifact, policy: As5prSignPolicy, key: bytes) -> SignedArtifact:
    _key(key)
    _validate_policy(artifact, policy)
    _validate_artifact(artifact)
    revalidate_source(artifact)
    return _build_signed(artifact, policy, key)


def verify_as5pr(signed: SignedArtifact, policy: As5prSignPolicy, key: bytes) -> None:
    _key(key)
    _validate_policy(signed.artifact, policy)
    _validate_artifact(signed.artifact)
    expected = _build_signed(signed.artifact, policy, key)
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
    revalidate_source(signed.artifact)
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
