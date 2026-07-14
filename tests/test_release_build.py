from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.release_build import (
    ToolIdentity,
    _normalize_version_info,
    read_locked_requirements,
    validate_distribution_inventory,
    validate_pyinstaller_archive,
    fetch_usb2xxx_runtime,
    is_official_release_environment,
    prepare_build,
    read_project_version,
    validate_release_git_state,
    write_tool_identity,
)


def test_only_tag_actions_build_is_an_official_release():
    assert not is_official_release_environment({})
    assert not is_official_release_environment(
        {"GITHUB_ACTIONS": "true", "GITHUB_REF_TYPE": "branch"}
    )
    assert is_official_release_environment(
        {"GITHUB_ACTIONS": "true", "GITHUB_REF_TYPE": "tag"}
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


def test_locked_requirements_and_distribution_inventory_are_exact(tmp_path: Path):
    lock = tmp_path / "release.lock"
    lock.write_text(
        "Alpha_Pkg==1.2.3 \\\n+    --hash=sha256:" + "01" * 32 + "\n"
        "beta-pkg==4.5.6 \\\n+    --hash=sha256:" + "02" * 32 + "\n",
        encoding="utf-8",
    )
    locked = read_locked_requirements(lock)

    assert locked == {"alpha-pkg": "1.2.3", "beta-pkg": "4.5.6"}
    validate_distribution_inventory(
        {
            "alpha-pkg": "1.2.3",
            "beta_pkg": "4.5.6",
            "unified-can-lin-host-tool": "0.2.0",
        },
        locked,
        project_name="unified-can-lin-host-tool",
        project_version="0.2.0",
    )
    with pytest.raises(ValueError, match="严格一致"):
        validate_distribution_inventory(
            {
                "alpha-pkg": "1.2.3",
                "beta-pkg": "4.5.6",
                "unexpected": "9.9.9",
                "unified-can-lin-host-tool": "0.2.0",
            },
            locked,
            project_name="unified-can-lin-host-tool",
            project_version="0.2.0",
        )


def test_archive_validation_checks_embedded_identity_keys_metadata_and_cli_dlls():
    identity = ToolIdentity(
        version="0.2.0", commit="01" * 20,
        build_time_utc="2026-07-14T12:00:00Z",
        repository="", official_build=False,
    )
    identity_raw = json.dumps(
        {
            "version": identity.version,
            "commit": identity.commit,
            "buildTimeUtc": identity.build_time_utc,
            "repository": identity.repository,
            "officialBuild": identity.official_build,
        }
    ).encode()
    keys = b'{"release-v1":"' + b"41" * 32 + b'"}'
    usb = b"usb-runtime"
    libusb = b"libusb-runtime"
    entries = {
        "unified_can_lin_host_tool\\_tool_build_identity.json": identity_raw,
        "unified_can_lin_host_tool\\update\\release_public_keys.json": keys,
        "unified_can_lin_host_tool-0.2.0.dist-info\\METADATA": (
            b"Metadata-Version: 2.4\nName: unified-can-lin-host-tool\nVersion: 0.2.0\n"
        ),
        "USB2XXX.dll": usb,
        "libusb-1.0.dll": libusb,
    }
    report = validate_pyinstaller_archive(
        entries,
        identity=identity,
        public_keys=keys,
        role="cli",
        usb_hashes={
            "USB2XXX.dll": hashlib.sha256(usb).hexdigest(),
            "libusb-1.0.dll": hashlib.sha256(libusb).hexdigest(),
        },
    )

    assert report["metadataVersion"] == "0.2.0"
    assert report["identitySha256"] == hashlib.sha256(identity_raw).hexdigest()
    damaged = dict(entries)
    damaged["USB2XXX.dll"] = usb + b"x"
    with pytest.raises(ValueError, match="USB2XXX.dll"):
        validate_pyinstaller_archive(
            damaged,
            identity=identity,
            public_keys=keys,
            role="cli",
            usb_hashes={
                "USB2XXX.dll": hashlib.sha256(usb).hexdigest(),
                "libusb-1.0.dll": hashlib.sha256(libusb).hexdigest(),
            },
        )
    with pytest.raises(ValueError, match="GUI"):
        validate_pyinstaller_archive(
            entries,
            identity=identity,
            public_keys=keys,
            role="gui",
            usb_hashes={},
        )


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


def test_fetch_usb2xxx_populates_an_existing_empty_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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
    actual_mkdtemp = tempfile.mkdtemp
    temp_parents: list[Path] = []

    def record_mkdtemp(*, prefix: str, dir: Path) -> str:
        temp_parents.append(Path(dir).resolve())
        return actual_mkdtemp(prefix=prefix, dir=dir)

    monkeypatch.setattr("scripts.release_build.tempfile.mkdtemp", record_mkdtemp)

    usb, libusb = fetch_usb2xxx_runtime(source, output)

    assert temp_parents == [output.parent.resolve()]
    assert usb.read_bytes() == payloads["USB2XXX.dll"]
    assert libusb.read_bytes() == payloads["libusb-1.0.dll"]
