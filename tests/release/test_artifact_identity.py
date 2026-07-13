from dataclasses import FrozenInstanceError, replace
import hashlib

import pytest

from unified_can_lin_host_tool.release.artifact_identity import (
    compute_artifact_id,
    compute_signed_artifact_id,
    encode_artifact_identity_v1,
)
from unified_can_lin_host_tool.release.models import ArtifactIdentityV1, Segment


@pytest.fixture
def identity() -> ArtifactIdentityV1:
    return ArtifactIdentityV1(
        target_id=0x41503541,
        bundle_id="bundle-1",
        profile_id="as5pr",
        profile_version="1",
        profile_sha256=bytes(32),
        sign_policy_id="hmac-v1",
        source_file_sha256=bytes.fromhex("11" * 32),
        normalization_start=0x7000,
        normalization_end=0x7002,
        gap_fill=0xFF,
        segments=(Segment(address=0x7000, data=b"\x01\x02"),),
        normalized_payload_sha256=bytes.fromhex(
            "a12871fee210fb8619291eaea194581cbd2531e4b23759d225f6806923f63222"
        ),
    )


def test_fixed_artifact_identity_vector(identity: ArtifactIdentityV1) -> None:

    assert len(encode_artifact_identity_v1(identity)) == 192
    assert compute_artifact_id(identity) == (
        "f83e3a7c2d5c1ef48ebc783b7acc870d6756f9315ea59d7ca1bde53ff43b467e"
    )


def test_models_are_immutable(identity: ArtifactIdentityV1) -> None:
    with pytest.raises(FrozenInstanceError):
        identity.target_id = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        identity.segments[0].address = 0  # type: ignore[misc]
    with pytest.raises(TypeError, match="tuple"):
        replace(identity, segments=list(identity.segments))  # type: ignore[arg-type]


def test_text_is_normalized_to_nfc(identity: ArtifactIdentityV1) -> None:
    composed = replace(identity, bundle_id="caf\N{LATIN SMALL LETTER E WITH ACUTE}")
    decomposed = replace(identity, bundle_id="cafe\N{COMBINING ACUTE ACCENT}")
    assert encode_artifact_identity_v1(composed) == encode_artifact_identity_v1(decomposed)


@pytest.mark.parametrize("field", ["bundle_id", "profile_id", "profile_version", "sign_policy_id"])
def test_rejects_text_over_u16_length(identity: ArtifactIdentityV1, field: str) -> None:
    with pytest.raises(ValueError, match="65535"):
        encode_artifact_identity_v1(replace(identity, **{field: "x" * 65536}))


@pytest.mark.parametrize(
    "field", ["profile_sha256", "source_file_sha256", "normalized_payload_sha256"]
)
def test_rejects_invalid_hash_length(identity: ArtifactIdentityV1, field: str) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        encode_artifact_identity_v1(replace(identity, **{field: bytes(31)}))


@pytest.mark.parametrize("field", ["bundle_id", "profile_id", "profile_version", "sign_policy_id"])
def test_rejects_null_text(identity: ArtifactIdentityV1, field: str) -> None:
    with pytest.raises(TypeError, match="string"):
        encode_artifact_identity_v1(replace(identity, **{field: None}))


@pytest.mark.parametrize(
    "segments",
    [
        (Segment(0x7000, b""),),
        (Segment(0x7001, b"B"), Segment(0x7000, b"A")),
        (Segment(0x7000, b"AB"), Segment(0x7001, b"C")),
        (Segment(-1, b"A"),),
        (Segment(0xFFFFFFFF, b"AB"),),
    ],
)
def test_rejects_invalid_segments(
    identity: ArtifactIdentityV1, segments: tuple[Segment, ...]
) -> None:
    with pytest.raises(ValueError):
        encode_artifact_identity_v1(replace(identity, segments=segments))


@pytest.mark.parametrize("gap_fill", [-1, 256, None])
def test_rejects_invalid_gap_fill(identity: ArtifactIdentityV1, gap_fill: object) -> None:
    with pytest.raises(ValueError, match="byte"):
        encode_artifact_identity_v1(replace(identity, gap_fill=gap_fill))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_id", 1),
        ("bundle_id", "bundle-2"),
        ("profile_id", "other"),
        ("profile_version", "2"),
        ("profile_sha256", bytes.fromhex("22" * 32)),
        ("sign_policy_id", "other"),
        ("source_file_sha256", bytes.fromhex("33" * 32)),
        ("normalization_start", 0x6FFF),
        ("normalization_end", 0x7003),
        ("gap_fill", 0),
        ("segments", (Segment(0x7000, b"\x01\x03"),)),
        ("normalized_payload_sha256", bytes.fromhex("44" * 32)),
    ],
)
def test_each_field_changes_artifact_id(
    identity: ArtifactIdentityV1, field: str, value: object
) -> None:
    assert compute_artifact_id(replace(identity, **{field: value})) != compute_artifact_id(identity)


def test_signed_artifact_id_uses_raw_hashes(identity: ArtifactIdentityV1) -> None:
    artifact_id = compute_artifact_id(identity)
    hashes = (
        bytes.fromhex("22" * 32),
        bytes.fromhex("33" * 32),
        bytes.fromhex("44" * 32),
    )
    expected = hashlib.sha256(
        b"LITSIGN1" + bytes.fromhex(artifact_id) + b"".join(hashes)
    ).hexdigest()
    assert compute_signed_artifact_id(artifact_id, *hashes) == expected


@pytest.mark.parametrize("position", range(4))
def test_signed_artifact_id_rejects_invalid_hash_parameters(position: int) -> None:
    parameters: list[bytes | str] = ["00" * 32, bytes(32), bytes(32), bytes(32)]
    parameters[position] = "not-a-hash" if position == 0 else bytes(31)
    with pytest.raises((TypeError, ValueError)):
        compute_signed_artifact_id(*parameters)  # type: ignore[arg-type]
