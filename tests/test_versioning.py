from pathlib import Path
import tomllib

import pytest

from unified_can_lin_host_tool.versioning import SemanticVersion


@pytest.mark.parametrize(
    "text",
    ["0.2", "v0.2.0", "01.2.3", "1.65536.0", "1.-1.0", "1.2.3.4"],
)
def test_semantic_version_rejects_noncanonical_or_windows_overflow(text):
    with pytest.raises(ValueError):
        SemanticVersion.parse(text)


def test_semantic_version_compares_numerically_and_builds_windows_tuple():
    assert SemanticVersion.parse("0.10.0") > SemanticVersion.parse("0.2.9")
    assert SemanticVersion.parse("0.2.0").windows_tuple() == (0, 2, 0, 0)


def test_project_version_starts_auto_update_line_at_0_2_0():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.2.0"
