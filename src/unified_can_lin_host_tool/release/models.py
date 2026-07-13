from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    address: int
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("data must be bytes")


@dataclass(frozen=True)
class ArtifactIdentityV1:
    target_id: int
    bundle_id: str
    profile_id: str
    profile_version: str
    profile_sha256: bytes
    sign_policy_id: str
    source_file_sha256: bytes
    normalization_start: int
    normalization_end: int
    gap_fill: int
    segments: tuple[Segment, ...]
    normalized_payload_sha256: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple):
            raise TypeError("segments must be a tuple")
        if not all(isinstance(segment, Segment) for segment in self.segments):
            raise TypeError("segments must contain only Segment instances")
