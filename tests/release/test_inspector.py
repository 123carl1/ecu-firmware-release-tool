from dataclasses import replace
from pathlib import Path

import pytest

from unified_can_lin_host_tool.core.errors import HostToolError
from unified_can_lin_host_tool.release.artifact_identity import compute_artifact_id
from unified_can_lin_host_tool.release.inspector import InspectionContext, inspect_artifact, revalidate_source


def _context() -> InspectionContext:
    return InspectionContext(target_id=1, bundle_id="bundle", profile_id="profile", profile_version="1", profile_sha256=bytes.fromhex("11" * 32), sign_policy_id="policy", normalization_start=0x1000, normalization_end=0x1004, gap_fill=0xFF)


def test_inspection_builds_recomputable_artifact_identity(tmp_path: Path) -> None:
    path = tmp_path / "app.bin"; path.write_bytes(b"AB")
    artifact = inspect_artifact(path, _context())
    assert artifact.artifact_id == compute_artifact_id(artifact.identity)
    assert artifact.normalized_payload == b"AB\xFF\xFF"
    with pytest.raises(Exception): artifact.artifact_id = "changed"  # type: ignore[misc]


def test_non_data_record_change_does_not_change_payload_identity_hash(tmp_path: Path) -> None:
    first = tmp_path / "first.s19"; second = tmp_path / "second.s19"
    first.write_text("S0030000FC\nS104100041AA\nS9030000FC", encoding="ascii")
    second.write_text("S004000058A3\nS104100041AA\nS9031234B6", encoding="ascii")
    a = inspect_artifact(first, _context()); b = inspect_artifact(second, _context())
    assert a.segments == b.segments
    assert a.identity.normalized_payload_sha256 == b.identity.normalized_payload_sha256
    assert a.identity.source_file_sha256 != b.identity.source_file_sha256


def test_revalidate_rejects_replaced_source(tmp_path: Path) -> None:
    path = tmp_path / "app.bin"; path.write_bytes(b"AB")
    artifact = inspect_artifact(path, _context())
    path.write_bytes(b"CD")
    with pytest.raises(HostToolError, match="source changed"):
        revalidate_source(artifact)
