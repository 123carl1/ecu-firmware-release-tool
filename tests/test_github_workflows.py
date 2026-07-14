from __future__ import annotations

import re
from pathlib import Path
import subprocess
import sys

import yaml


PINNED_ACTIONS = {
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}


def _workflow(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_release_build_job_has_no_secret_and_publish_job_uses_environment():
    text = _workflow(".github/workflows/release.yml")
    build_text, publish_text = text.split("  publish:", 1)
    assert "secrets." not in build_text
    assert "environment: release" in publish_text
    assert "UPDATE_SIGNING_KEY_PEM: ${{ secrets.UPDATE_SIGNING_KEY_PEM }}" in publish_text
    assert "permissions:\n      contents: write" in publish_text


def test_all_actions_are_pinned_to_expected_full_commit():
    text = "\n".join(
        _workflow(path)
        for path in [".github/workflows/ci.yml", ".github/workflows/release.yml"]
    )
    for action, commit in PINNED_ACTIONS.items():
        assert f"uses: {action}@{commit}" in text
    assert not re.search(r"uses:\s+actions/[^@]+@v[0-9]", text)
    for action, commit in re.findall(r"uses:\s+(actions/[^@\s]+)@([^\s]+)", text):
        assert commit == PINNED_ACTIONS[action]
        assert re.fullmatch(r"[0-9a-f]{40}", commit)


def test_manual_dispatch_cannot_publish():
    text = _workflow(".github/workflows/release.yml")
    assert "workflow_dispatch" not in text
    assert "tags:" in text and "v*.*.*" in text
    assert "pull_request:" not in text


def test_ci_is_read_only_secret_free_and_runs_all_required_gates():
    text = _workflow(".github/workflows/ci.yml")
    assert "pull_request:" in text and "push:" in text
    assert "permissions:\n  contents: read" in text
    assert "name: Windows test and build" in text
    assert "python-version: '3.11.9'" in text
    assert "pip install --require-hashes -r requirements-release.lock" in text
    assert "git . --log-opts=\"--all\" --config=.gitleaks.toml --redact" in text
    assert "dir . --config=.gitleaks.toml --redact" in text
    assert "D:\\software\\gitleaks\\8.30.1\\gitleaks.exe" in text
    assert "python -m pytest -q" in text
    assert "scripts/build_windows_installer.ps1" in text
    assert "dist/release-audit.json" in text
    assert "secrets." not in text
    assert "gh release" not in text


def test_release_build_gates_tag_history_clean_tree_and_audited_artifact():
    text = _workflow(".github/workflows/release.yml")
    build_text, _ = text.split("  publish:", 1)
    assert "fetch-depth: 0" in build_text
    assert "github.event.repository.default_branch" in build_text
    assert "git merge-base --is-ancestor" in build_text
    assert "git status --porcelain" in build_text
    assert "pyproject.toml" in build_text
    assert "GITHUB_REF_NAME" in build_text
    assert "git . --log-opts=\"--all\" --config=.gitleaks.toml --redact" in build_text
    assert "python -m pytest -q" in build_text
    assert "scripts/build_windows_installer.ps1" in build_text
    assert "dist/release-audit.json" in build_text


def test_publish_verifies_assets_before_making_draft_public():
    text = _workflow(".github/workflows/release.yml")
    _, publish_text = text.split("  publish:", 1)
    create = publish_text.index("gh release create")
    digest_check = publish_text.index(".digest")
    make_public = publish_text.index("gh release edit")
    assert "releases/tags/$env:GITHUB_REF_NAME" in publish_text
    absence_check = publish_text.index("releases?per_page=100")
    assert "--draft --verify-tag" in publish_text
    assert "release_signing.py assert-key" in publish_text
    assert "build-update" in publish_text
    assert "write-sha256sums" in publish_text
    assert "update.json.sig" in publish_text
    assert "SHA256SUMS.txt" in publish_text
    assert absence_check < create < digest_check < make_public
    assert "$LASTEXITCODE -eq 0" not in publish_text
    assert "--draft=false --latest" in publish_text


def test_gitleaks_allowlist_is_limited_to_two_public_bench_constants():
    text = Path(".gitleaks.toml").read_text(encoding="utf-8")
    assert "src/unified_can_lin_host_tool/release/development_keys\\.py" in text
    assert "DEVELOPMENT_PACKAGE_PRIVATE_SEED" in text
    assert "DEVELOPMENT_BOOT_HMAC_KEY" in text
    assert "DEVELOPMENT_PACKAGE_PUBLIC_KEY" not in text
    assert text.count("[[allowlists]]") == 2


def test_public_documentation_states_distribution_and_security_boundaries():
    readme = Path("README.md").read_text(encoding="utf-8")
    notes = Path("docs/releases/0.2.0.md").read_text(encoding="utf-8")
    for phrase in [
        "EcuReleaseCLI.exe --version",
        "未知发布者",
        "USB2XXX",
        "同星 DLL 不随安装包发布",
        "仅限开发台架",
        "量产禁止使用",
        "源码公开可查看不等于授予额外开源许可",
    ]:
        assert phrase in readme
    assert "0.2.0" in notes
    assert "未知发布者" in notes


def _native_command_count(script: str) -> int:
    pattern = re.compile(
        r"^\s*(?:&\s+['\"][^'\"]+\.exe['\"](?=\s|$)|(?:git|gh|python|pwsh\.exe)\b)"
    )
    return sum(pattern.search(line) is not None for line in script.splitlines())


def test_multi_native_command_pwsh_blocks_stop_on_first_failure():
    checked_steps: list[str] = []
    for path in [".github/workflows/ci.yml", ".github/workflows/release.yml"]:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        for job in document["jobs"].values():
            for step in job.get("steps", []):
                script = step.get("run")
                if step.get("shell") != "pwsh" or not isinstance(script, str):
                    continue
                if _native_command_count(script) < 2:
                    continue
                lines = [line.strip() for line in script.splitlines() if line.strip()]
                checked_steps.append(step["name"])
                assert lines[:2] == [
                    "$PSNativeCommandUseErrorActionPreference = $true",
                    "$ErrorActionPreference = 'Stop'",
                ], step["name"]

    assert set(checked_steps) == {
        "Gate tag, version, default branch history and clean inputs",
        "Scan current tree and complete history",
        "Verify signing key and generate signed release files",
        "Create draft, verify uploaded assets, then publish",
    }
    assert checked_steps.count("Scan current tree and complete history") == 2


def test_pwsh_failure_preamble_prevents_later_native_commands(tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    python = sys.executable.replace("'", "''")
    marker_text = str(marker).replace("'", "''")
    script = "\n".join(
        [
            "$PSNativeCommandUseErrorActionPreference = $true",
            "$ErrorActionPreference = 'Stop'",
            f"& '{python}' -c \"import sys; sys.exit(23)\"",
            f"& '{python}' -c \"from pathlib import Path; Path(r'{marker_text}').write_text('continued')\"",
        ]
    )

    result = subprocess.run(
        ["pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert not marker.exists()
