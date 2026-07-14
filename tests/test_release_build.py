from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.release_build import (
    ToolIdentity,
    _normalize_version_info,
    fetch_usb2xxx_runtime,
    prepare_build,
    read_project_version,
    validate_release_git_state,
    write_tool_identity,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, *, version: str = "0.2.0") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "release-test"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_prepare_build_writes_identity_and_pyinstaller_version_files(tmp_path: Path):
    outputs = prepare_build(
        version="0.2.0",
        commit="01" * 20,
        repository="owner/ecu-firmware-release-tool",
        tag="v0.2.0",
        build_time_utc="2026-07-14T12:00:00Z",
        official=True,
        output_dir=tmp_path,
    )

    assert json.loads(outputs.identity.read_text(encoding="utf-8"))["officialBuild"] is True
    assert "filevers=(0, 2, 0, 0)" in outputs.gui_version.read_text(encoding="utf-8")
    assert "EcuReleaseTool" in outputs.gui_version.read_text(encoding="utf-8")
    assert "EcuReleaseCLI" in outputs.cli_version.read_text(encoding="utf-8")


def test_version_info_normalization_removes_inno_padding():
    assert _normalize_version_info(
        {"FileVersion": "0.2.0.0             ", "ProductVersion": "0.2.0  "}
    ) == {"FileVersion": "0.2.0.0", "ProductVersion": "0.2.0"}


def test_read_project_version_rejects_non_three_part_version(tmp_path: Path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "0.2"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="三段"):
        read_project_version(project)


def test_release_tag_must_match_source_version(tmp_path: Path):
    repo = _repository(tmp_path)
    _git(repo, "tag", "v0.2.1")

    with pytest.raises(ValueError, match="源码版本"):
        validate_release_git_state(repo, "v0.2.1", "refs/heads/main")


def test_release_tag_commit_must_be_default_branch_ancestor(tmp_path: Path):
    repo = _repository(tmp_path)
    _git(repo, "checkout", "-b", "release-side")
    (repo / "side.txt").write_text("side", encoding="utf-8")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-m", "side")
    _git(repo, "tag", "v0.2.0")
    _git(repo, "checkout", "--detach", "v0.2.0")

    with pytest.raises(ValueError, match="默认分支祖先"):
        validate_release_git_state(repo, "v0.2.0", "refs/heads/main")


def test_release_rejects_untracked_input(tmp_path: Path):
    repo = _repository(tmp_path)
    _git(repo, "tag", "v0.2.0")
    (repo / "untracked-release-input.bin").write_bytes(b"release input")

    with pytest.raises(ValueError, match="干净检出"):
        validate_release_git_state(repo, "v0.2.0", "refs/heads/main")


def test_development_identity_cannot_claim_repository(tmp_path: Path):
    identity = ToolIdentity(
        version="0.2.0",
        commit="01" * 20,
        build_time_utc="2026-07-14T12:00:00Z",
        repository="owner/ecu-firmware-release-tool",
        official_build=False,
    )

    with pytest.raises(ValueError, match="开发构建"):
        write_tool_identity(tmp_path, identity)


def test_fetch_usb2xxx_rejects_changed_dll_before_copy(tmp_path: Path):
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    _git(sdk, "init", "-b", "main")
    _git(sdk, "config", "user.name", "SDK Test")
    _git(sdk, "config", "user.email", "sdk-test@example.invalid")
    runtime = sdk / "sdk" / "libs" / "windows" / "x86_64"
    runtime.mkdir(parents=True)
    original_usb = b"USB2XXX original"
    libusb = b"libusb original"
    (runtime / "USB2XXX.dll").write_bytes(original_usb[:-1] + b"X")
    (runtime / "libusb-1.0.dll").write_bytes(libusb)
    _git(sdk, "add", ".")
    _git(sdk, "commit", "-m", "sdk")
    commit = _git(sdk, "rev-parse", "HEAD")
    source = tmp_path / "usb-source.json"
    source.write_text(
        json.dumps(
            {
                "repository": sdk.as_uri(),
                "commit": commit,
                "runtimePath": "sdk/libs/windows/x86_64",
                "files": {
                    "USB2XXX.dll": {
                        "size": len(original_usb),
                        "sha256": hashlib.sha256(original_usb).hexdigest(),
                    },
                    "libusb-1.0.dll": {
                        "size": len(libusb),
                        "sha256": hashlib.sha256(libusb).hexdigest(),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"

    with pytest.raises(ValueError, match="USB2XXX.dll SHA-256"):
        fetch_usb2xxx_runtime(source, output)
    assert not (output / "USB2XXX.dll").exists()
    assert not (output / "libusb-1.0.dll").exists()


def test_fetch_usb2xxx_populates_an_existing_empty_output_directory(tmp_path: Path):
    sdk = tmp_path / "sdk-valid"
    sdk.mkdir()
    _git(sdk, "init", "-b", "main")
    _git(sdk, "config", "user.name", "SDK Test")
    _git(sdk, "config", "user.email", "sdk-test@example.invalid")
    runtime = sdk / "sdk" / "libs" / "windows" / "x86_64"
    runtime.mkdir(parents=True)
    payloads = {"USB2XXX.dll": b"valid usb", "libusb-1.0.dll": b"valid libusb"}
    for name, payload in payloads.items():
        (runtime / name).write_bytes(payload)
    _git(sdk, "add", ".")
    _git(sdk, "commit", "-m", "sdk")
    source = tmp_path / "valid-source.json"
    source.write_text(
        json.dumps(
            {
                "repository": sdk.as_uri(),
                "commit": _git(sdk, "rev-parse", "HEAD"),
                "runtimePath": "sdk/libs/windows/x86_64",
                "files": {
                    name: {
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                    for name, payload in payloads.items()
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "existing-empty"
    output.mkdir()

    usb, libusb = fetch_usb2xxx_runtime(source, output)

    assert usb.read_bytes() == payloads["USB2XXX.dll"]
    assert libusb.read_bytes() == payloads["libusb-1.0.dll"]
