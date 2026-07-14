"""可复现 Windows 发布构建的准备、第三方运行库获取和产物审计入口。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
from typing import Any

from unified_can_lin_host_tool.tool_identity import ToolIdentity, load_tool_identity
from unified_can_lin_host_tool.versioning import SemanticVersion


_OFFICIAL_USB_REPOSITORY = "https://gitee.com/toomoss/usb2can_lin_pwm_example.git"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class BuildOutputs:
    identity: Path
    gui_version: Path
    cli_version: Path


def _run(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=cwd, check=check, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def read_project_version(pyproject_path: Path) -> SemanticVersion:
    with Path(pyproject_path).open("rb") as stream:
        data = tomllib.load(stream)
    try:
        raw = data["project"]["version"]
    except (KeyError, TypeError) as exc:
        raise ValueError("pyproject.toml 缺少 [project].version") from exc
    if not isinstance(raw, str):
        raise ValueError("源码版本必须是字符串")
    return SemanticVersion.parse(raw)


def validate_release_git_state(repo: Path, tag: str, default_branch_ref: str) -> None:
    repo = Path(repo).resolve()
    version = read_project_version(repo / "pyproject.toml")
    if tag != f"v{version}":
        raise ValueError(f"发布标签 {tag} 与源码版本 {version} 不一致")
    status = _run(
        "git", "status", "--porcelain=v1", "--untracked-files=all", cwd=repo
    ).stdout
    if status:
        raise ValueError("正式发布必须使用干净检出，且不得包含未追踪发布输入")
    try:
        tag_commit = _run(
            "git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}", cwd=repo
        ).stdout.strip()
        head_commit = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
        _run("git", "rev-parse", "--verify", default_branch_ref, cwd=repo)
    except subprocess.CalledProcessError as exc:
        raise ValueError("发布标签或默认分支引用不存在，请先完整 fetch") from exc
    if head_commit != tag_commit:
        raise ValueError("正式发布检出提交必须与发布标签提交一致")
    ancestor = _run(
        "git", "merge-base", "--is-ancestor", tag_commit, default_branch_ref,
        cwd=repo, check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError("发布标签提交不在默认分支祖先链")


def _identity_payload(identity: ToolIdentity) -> dict[str, object]:
    return {
        "version": identity.version,
        "commit": identity.commit,
        "buildTimeUtc": identity.build_time_utc,
        "repository": identity.repository,
        "officialBuild": identity.official_build,
    }


def write_tool_identity(output_dir: Path, identity: ToolIdentity) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = _identity_payload(identity)
    raw = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    load_tool_identity(raw, installed_version=identity.version)
    output = output_dir / "_tool_build_identity.json"
    output.write_bytes(raw)
    return output


def write_pyinstaller_version_file(
    output: Path, identity: ToolIdentity, description: str
) -> None:
    version = SemanticVersion.parse(identity.version)
    file_version = version.windows_tuple()
    internal_name = "EcuReleaseCLI" if "CLI" in description else "EcuReleaseTool"
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={file_version!r}, prodvers={file_version!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Internal Engineering'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{identity.version}.0'),
      StringStruct('InternalName', '{internal_name}'),
      StringStruct('OriginalFilename', '{internal_name}.exe'),
      StringStruct('ProductName', 'ECU Firmware Release Tool'),
      StringStruct('ProductVersion', '{identity.version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", newline="\n")


def prepare_build(
    *, version: str, commit: str, repository: str, tag: str,
    build_time_utc: str, official: bool, output_dir: Path,
) -> BuildOutputs:
    SemanticVersion.parse(version)
    if tag != f"v{version}":
        raise ValueError("构建标签与源码版本不一致")
    if _COMMIT_RE.fullmatch(commit) is None:
        raise ValueError("构建提交必须是 40 位小写十六进制字符")
    identity = ToolIdentity(
        version=version,
        commit=commit,
        build_time_utc=build_time_utc,
        repository=repository,
        official_build=official,
    )
    identity_path = write_tool_identity(output_dir, identity)
    gui_version = Path(output_dir) / "EcuReleaseTool.version.txt"
    cli_version = Path(output_dir) / "EcuReleaseCLI.version.txt"
    write_pyinstaller_version_file(gui_version, identity, "EcuReleaseTool GUI")
    write_pyinstaller_version_file(cli_version, identity, "EcuReleaseCLI")
    return BuildOutputs(identity_path, gui_version, cli_version)


def _verified_runtime_file(path: Path, name: str, contract: dict[str, Any]) -> Path:
    if not path.is_file():
        raise ValueError(f"{name} 不存在")
    size = path.stat().st_size
    if size != contract["size"]:
        raise ValueError(f"{name} size 不一致：期望 {contract['size']}，实际 {size}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != contract["sha256"]:
        raise ValueError(
            f"{name} SHA-256 不一致：期望 {contract['sha256']}，实际 {digest}"
        )
    return path


def fetch_usb2xxx_runtime(source_file: Path, output_dir: Path) -> tuple[Path, Path]:
    source_path = Path(source_file).resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    repository = source["repository"]
    if repository != _OFFICIAL_USB_REPOSITORY and not repository.startswith("file:"):
        raise ValueError("USB2XXX 来源仓库不在允许列表")
    commit = source["commit"]
    if _COMMIT_RE.fullmatch(commit) is None:
        raise ValueError("USB2XXX 固定提交格式无效")
    files = source["files"]
    names = ("USB2XXX.dll", "libusb-1.0.dll")
    output_dir = Path(output_dir)
    outputs = tuple(output_dir / name for name in names)
    if output_dir.exists() and any(path.exists() for path in outputs):
        if not all(path.is_file() for path in outputs):
            raise ValueError("USB2XXX 本机缓存不完整")
        return tuple(
            _verified_runtime_file(path, name, files[name])
            for path, name in zip(outputs, names, strict=True)
        )  # type: ignore[return-value]
    if output_dir.exists():
        if any(output_dir.iterdir()):
            raise ValueError("USB2XXX 本机缓存包含非合同文件")
        output_dir.rmdir()

    temp_root = Path(r"D:\Temp\ecu-release-task8")
    temp_root.mkdir(parents=True, exist_ok=True)
    checkout = Path(tempfile.mkdtemp(prefix="usb2xxx-", dir=temp_root))
    stage = checkout.parent / f"{checkout.name}-verified"
    try:
        _run("git", "clone", "--no-checkout", "--filter=blob:none", repository, str(checkout))
        _run("git", "checkout", "--detach", commit, cwd=checkout)
        actual_commit = _run("git", "rev-parse", "HEAD", cwd=checkout).stdout.strip()
        if actual_commit != commit:
            raise ValueError("USB2XXX 检出提交与固定提交不一致")
        runtime = checkout / Path(source["runtimePath"])
        verified = [
            _verified_runtime_file(runtime / name, name, files[name]) for name in names
        ]
        stage.mkdir()
        for path in verified:
            shutil.copy2(path, stage / path.name)
        for name in names:
            _verified_runtime_file(stage / name, name, files[name])
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage.replace(output_dir)
    finally:
        shutil.rmtree(checkout, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
    return outputs[0], outputs[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_version_info(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.strip() if isinstance(value, str) else value
        for key, value in values.items()
    }


def _windows_version_info(path: Path) -> dict[str, str]:
    quoted = str(path.resolve()).replace("'", "''")
    command = (
        f"$v=(Get-Item -LiteralPath '{quoted}').VersionInfo; "
        "@{FileVersion=$v.FileVersion;ProductVersion=$v.ProductVersion;"
        "ProductName=$v.ProductName;FileDescription=$v.FileDescription}|ConvertTo-Json -Compress"
    )
    result = _run("pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", command)
    return _normalize_version_info(json.loads(result.stdout))


def audit_windows_build(
    dist_dir: Path, installer: Path, identity: ToolIdentity
) -> dict[str, object]:
    dist_dir = Path(dist_dir)
    installer = Path(installer)
    expected_installer = f"EcuReleaseTool_Setup_{identity.version}.exe"
    if installer.name != expected_installer or not installer.is_file():
        raise ValueError(f"安装包名称必须为 {expected_installer}")
    sidecar = dist_dir / "_tool_build_identity.json"
    embedded_identity = load_tool_identity(
        sidecar.read_bytes() if sidecar.is_file() else None,
        installed_version=identity.version,
    )
    if embedded_identity != identity:
        raise ValueError("正式产物构建身份与本次构建不一致")
    expected_file_version = f"{identity.version}.0"
    binaries: dict[str, object] = {}
    for name, description in (
        ("EcuReleaseTool.exe", "EcuReleaseTool GUI"),
        ("EcuReleaseCLI.exe", "EcuReleaseCLI"),
    ):
        path = dist_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"缺少 Windows 产物：{name}")
        version_info = _windows_version_info(path)
        if version_info.get("FileVersion") != expected_file_version:
            raise ValueError(f"{name} PE FileVersion 不一致")
        if version_info.get("ProductVersion") != identity.version:
            raise ValueError(f"{name} PE ProductVersion 不一致")
        if version_info.get("FileDescription") != description:
            raise ValueError(f"{name} PE FileDescription 不一致")
        binaries[name] = {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "versionInfo": version_info,
        }
    installer_info = _windows_version_info(installer)
    if installer_info.get("FileVersion") != expected_file_version:
        raise ValueError("安装包 PE FileVersion 不一致")
    return {
        "version": identity.version,
        "commit": identity.commit,
        "buildTimeUtc": identity.build_time_utc,
        "repository": identity.repository,
        "officialBuild": identity.official_build,
        "binaries": binaries,
        "installer": {
            "name": installer.name,
            "size": installer.stat().st_size,
            "sha256": _sha256(installer),
            "versionInfo": installer_info,
        },
    }


def _parse_identity(path: Path) -> ToolIdentity:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ToolIdentity(
        version=payload["version"], commit=payload["commit"],
        build_time_utc=payload["buildTimeUtc"], repository=payload["repository"],
        official_build=payload["officialBuild"],
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-build")
    prepare.add_argument("--repo", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--default-branch-ref")
    prepare.add_argument("--build-time-utc")
    fetch = commands.add_parser("fetch-usb2xxx")
    fetch.add_argument("--source-file", type=Path, required=True)
    fetch.add_argument("--output-dir", type=Path, required=True)
    audit = commands.add_parser("audit-build")
    audit.add_argument("--dist-dir", type=Path, required=True)
    audit.add_argument("--installer", type=Path, required=True)
    audit.add_argument("--identity", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "fetch-usb2xxx":
        fetch_usb2xxx_runtime(args.source_file, args.output_dir)
        return 0
    if args.command == "audit-build":
        report = audit_windows_build(
            args.dist_dir, args.installer, _parse_identity(args.identity)
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return 0

    repo = args.repo.resolve()
    version = str(read_project_version(repo / "pyproject.toml"))
    commit = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    official = os.environ.get("GITHUB_ACTIONS") == "true"
    if official:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        sha = os.environ.get("GITHUB_SHA", "")
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if sha != commit:
            raise ValueError("GITHUB_SHA 与当前检出提交不一致")
        if not args.default_branch_ref:
            raise ValueError("正式构建必须提供 Actions 查询到的默认分支引用")
        validate_release_git_state(repo, tag, args.default_branch_ref)
    else:
        repository = ""
        tag = f"v{version}"
    build_time = args.build_time_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    outputs = prepare_build(
        version=version, commit=commit, repository=repository, tag=tag,
        build_time_utc=build_time, official=official, output_dir=args.output_dir,
    )
    print(json.dumps({key: str(value) for key, value in asdict(outputs).items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
