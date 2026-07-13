import hashlib
import struct
import unicodedata

from .models import ArtifactIdentityV1


def _u16_prefixed_text(value: str, field: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    encoded = unicodedata.normalize("NFC", value).encode("utf-8")
    if len(encoded) > 0xFFFF:
        raise ValueError(f"{field} exceeds 65535 UTF-8 bytes")
    return struct.pack(">H", len(encoded)) + encoded


def _hash32(value: bytes, field: str) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field} must be bytes")
    if len(value) != 32:
        raise ValueError(f"{field} must contain exactly 32 bytes")
    return value


def _u32(value: int, field: str) -> bytes:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 0xFFFFFFFF
    ):
        raise ValueError(f"{field} must be a u32")
    return struct.pack(">I", value)


def encode_artifact_identity_v1(identity: ArtifactIdentityV1) -> bytes:
    if not isinstance(identity, ArtifactIdentityV1):
        raise TypeError("identity must be ArtifactIdentityV1")
    if (
        not isinstance(identity.gap_fill, int)
        or isinstance(identity.gap_fill, bool)
        or not 0 <= identity.gap_fill <= 0xFF
    ):
        raise ValueError("gap_fill must be a byte")
    if identity.normalization_start > identity.normalization_end:
        raise ValueError("normalization range is reversed")

    encoded_segments = bytearray()
    previous_end = None
    for segment in identity.segments:
        address = segment.address
        data = segment.data
        _u32(address, "segment.address")
        if not isinstance(data, bytes):
            raise TypeError("segment.data must be bytes")
        if not data:
            raise ValueError("segment.data must not be empty")
        end = address + len(data)
        if end > 0x100000000:
            raise ValueError("segment exceeds u32 address space")
        if previous_end is not None and address < previous_end:
            raise ValueError("segments must be ordered and non-overlapping")
        previous_end = end
        encoded_segments += _u32(address, "segment.address")
        encoded_segments += _u32(len(data), "segment.length")
        encoded_segments += hashlib.sha256(data).digest()

    return b"".join((
        b"LITART01",
        struct.pack(">H", 1),
        _u32(identity.target_id, "target_id"),
        _u16_prefixed_text(identity.bundle_id, "bundle_id"),
        _u16_prefixed_text(identity.profile_id, "profile_id"),
        _u16_prefixed_text(identity.profile_version, "profile_version"),
        _hash32(identity.profile_sha256, "profile_sha256"),
        _u16_prefixed_text(identity.sign_policy_id, "sign_policy_id"),
        _hash32(identity.source_file_sha256, "source_file_sha256"),
        _u32(identity.normalization_start, "normalization_start"),
        _u32(identity.normalization_end, "normalization_end"),
        bytes((identity.gap_fill,)),
        _u32(len(identity.segments), "segment_count"),
        bytes(encoded_segments),
        _hash32(identity.normalized_payload_sha256, "normalized_payload_sha256"),
    ))


def compute_artifact_id(identity: ArtifactIdentityV1) -> str:
    return hashlib.sha256(encode_artifact_identity_v1(identity)).hexdigest()


def compute_signed_artifact_id(
    artifact_id: bytes | str,
    signed_file_sha256: bytes,
    auth_block_sha256: bytes,
    manifest_bundle_sha256: bytes,
) -> str:
    if isinstance(artifact_id, str):
        if len(artifact_id) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in artifact_id
        ):
            raise ValueError("artifact_id must be a 64-character hexadecimal string")
        try:
            artifact_id_raw = bytes.fromhex(artifact_id)
        except ValueError as exc:
            raise ValueError("artifact_id must be a 64-character hexadecimal string") from exc
    else:
        artifact_id_raw = artifact_id
    return hashlib.sha256(b"".join((
        b"LITSIGN1",
        _hash32(artifact_id_raw, "artifact_id"),
        _hash32(signed_file_sha256, "signed_file_sha256"),
        _hash32(auth_block_sha256, "auth_block_sha256"),
        _hash32(manifest_bundle_sha256, "manifest_bundle_sha256"),
    ))).hexdigest()
