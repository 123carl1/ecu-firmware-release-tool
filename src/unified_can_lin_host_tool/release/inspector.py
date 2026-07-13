import hashlib
from dataclasses import dataclass
from pathlib import Path

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError

from .artifact_identity import compute_artifact_id
from .image_parser import _parse_image_bytes, normalize_segments
from .models import ArtifactIdentityV1, Segment


@dataclass(frozen=True)
class InspectionContext:
    target_id: int
    bundle_id: str
    profile_id: str
    profile_version: str
    profile_sha256: bytes
    sign_policy_id: str
    normalization_start: int
    normalization_end: int
    gap_fill: int


@dataclass(frozen=True)
class InspectedArtifact:
    source_path: Path
    source_file_sha256: bytes
    segments: tuple[Segment, ...]
    normalized_payload: bytes
    identity: ArtifactIdentityV1
    artifact_id: str


def inspect_artifact(path: Path, context: InspectionContext) -> InspectedArtifact:
    source_path = Path(path).resolve()
    try: source = source_path.read_bytes()
    except OSError as exc: raise HostToolError(ErrorCategory.FILE, f"cannot read source: {source_path}") from exc
    source_hash = hashlib.sha256(source).digest()
    bin_start = context.normalization_start if source_path.suffix.lower() == ".bin" else None
    segments = _parse_image_bytes(source_path, source, bin_start=bin_start)
    normalized = normalize_segments(segments, start=context.normalization_start, end=context.normalization_end, gap_fill=context.gap_fill)
    identity = ArtifactIdentityV1(target_id=context.target_id, bundle_id=context.bundle_id, profile_id=context.profile_id, profile_version=context.profile_version, profile_sha256=context.profile_sha256, sign_policy_id=context.sign_policy_id, source_file_sha256=source_hash, normalization_start=context.normalization_start, normalization_end=context.normalization_end, gap_fill=context.gap_fill, segments=segments, normalized_payload_sha256=hashlib.sha256(normalized).digest())
    artifact = InspectedArtifact(source_path, source_hash, segments, normalized, identity, compute_artifact_id(identity))
    revalidate_source(artifact)
    return artifact


def revalidate_source(artifact: InspectedArtifact) -> None:
    try: current = hashlib.sha256(artifact.source_path.read_bytes()).digest()
    except OSError as exc: raise HostToolError(ErrorCategory.FILE, "source changed or unavailable") from exc
    if current != artifact.source_file_sha256:
        raise HostToolError(ErrorCategory.FILE, "source changed after inspection")
