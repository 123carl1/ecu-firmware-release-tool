"""可复现 Windows 发布构建的准备、第三方运行库获取和产物审计入口。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
from typing import Any, Mapping

from unified_can_lin_host_tool.tool_identity import ToolIdentity, load_tool_identity
from unified_can_lin_host_tool.versioning import SemanticVersion


_OFFICIAL_USB_REPOSITORY = "https://gitee.com/toomoss/usb2can_lin_pwm_example.git"
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class BuildOutputs:
    identity: Path
    gui_version: Path
    cli_version: Path


def is_official_release_environment(environment: Mapping[str, str]) -> bool:
    return (
        environment.get("GITHUB_ACTIONS") == "true"
        and environment.get("GITHUB_REF_TYPE") == "tag"
    )


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


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_locked_requirements(lock_file: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)\s*\\?$")
    for line in Path(lock_file).read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(line)
        if match is None:
            continue
        name = _normalized_distribution_name(match.group(1))
        if name in requirements:
            raise ValueError(f"发布锁包含重复分布：{name}")
        requirements[name] = match.group(2)
    if not requirements:
        raise ValueError("发布锁没有固定分布")
    return requirements


def validate_distribution_inventory(
    installed: Mapping[str, str],
    locked: Mapping[str, str],
    *,
    project_name: str,
    project_version: str,
) -> None:
    actual = {
        _normalized_distribution_name(name): version
        for name, version in installed.items()
    }
    expected = {
        _normalized_distribution_name(name): version
        for name, version in locked.items()
    }
    if project_name:
        expected[_normalized_distribution_name(project_name)] = project_version
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        wrong = sorted(
            name for name in set(actual) & set(expected)
            if actual[name] != expected[name]
        )
        raise ValueError(
            "发布环境已安装分布必须与锁定分布严格一致："
            f"missing={missing}, extra={extra}, wrongVersion={wrong}"
        )


def audit_release_environment(
    python_path: Path,
    lock_file: Path,
    *,
    project_name: str,
    project_version: str,
) -> dict[str, object]:
    script = (
        "import importlib.metadata as m,json;"
        "print(json.dumps({d.metadata['Name']:d.version for d in m.distributions()}))"
    )
    result = _run(str(Path(python_path)), "-I", "-c", script)
    installed = json.loads(result.stdout)
    locked = read_locked_requirements(lock_file)
    validate_distribution_inventory(
        installed, locked, project_name=project_name, project_version=project_version
    )
    return {
        "python": str(Path(python_path).resolve()),
        "lockedDistributionCount": len(locked),
        "installedDistributions": dict(sorted(installed.items())),
    }


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


def fetch_usb2xxx_runtime(
    source_file: Path, output_dir: Path, *, allow_network: bool = True
) -> tuple[Path, Path]:
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
    if not allow_network:
        raise ValueError("离线构建缺少已验证的 USB2XXX 运行库缓存")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(
        prefix=f".{output_dir.name}-usb2xxx-", dir=output_dir.parent
    ))
    checkout = temp_root / "checkout"
    stage = temp_root / "verified"
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
        stage.replace(output_dir)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
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


def _one_archive_entry(
    entries: Mapping[str, bytes], suffix: str, description: str
) -> bytes:
    normalized_suffix = suffix.replace("/", "\\").lower()
    matches = [
        content for name, content in entries.items()
        if name.replace("/", "\\").lower().endswith(normalized_suffix)
    ]
    if len(matches) != 1:
        raise ValueError(f"onefile 必须包含且仅包含一个{description}")
    return matches[0]


def validate_pyinstaller_archive(
    entries: Mapping[str, bytes],
    *,
    identity: ToolIdentity,
    public_keys: bytes,
    role: str,
    usb_hashes: Mapping[str, str],
) -> dict[str, object]:
    identity_raw = _one_archive_entry(
        entries, "unified_can_lin_host_tool\\_tool_build_identity.json", "构建身份"
    )
    archived_identity = load_tool_identity(identity_raw, installed_version=identity.version)
    if archived_identity != identity:
        raise ValueError("onefile 内嵌构建身份与本次构建不一致")
    keys_raw = _one_archive_entry(
        entries,
        "unified_can_lin_host_tool\\update\\release_public_keys.json",
        "发布公钥",
    )
    if keys_raw != public_keys:
        raise ValueError("onefile 内嵌发布公钥与源码资源不一致")
    metadata_suffix = (
        f"unified_can_lin_host_tool-{identity.version}.dist-info\\metadata"
    )
    metadata_raw = _one_archive_entry(entries, metadata_suffix, "项目 METADATA")
    metadata = BytesParser(policy=policy.default).parsebytes(metadata_raw)
    if (
        _normalized_distribution_name(metadata.get("Name", ""))
        != "unified-can-lin-host-tool"
        or metadata.get("Version") != identity.version
    ):
        raise ValueError("onefile 项目 METADATA 名称或版本不一致")

    normalized_entries = {
        name.replace("/", "\\").lower(): content for name, content in entries.items()
    }
    dll_names = ("USB2XXX.dll", "libusb-1.0.dll")
    dll_report: dict[str, str] = {}
    for name in dll_names:
        matches = [
            content for entry_name, content in normalized_entries.items()
            if entry_name == name.lower()
        ]
        if role == "gui":
            if matches:
                raise ValueError(f"GUI onefile 不得包含 {name}")
            continue
        if len(matches) != 1:
            raise ValueError(f"CLI onefile 必须包含且仅包含一个 {name}")
        digest = hashlib.sha256(matches[0]).hexdigest()
        if digest != usb_hashes.get(name):
            raise ValueError(f"CLI onefile 内嵌 {name} SHA-256 不一致")
        dll_report[name] = digest
    return {
        "identitySha256": hashlib.sha256(identity_raw).hexdigest(),
        "publicKeysSha256": hashlib.sha256(keys_raw).hexdigest(),
        "metadataVersion": metadata["Version"],
        "embeddedRuntimeSha256": dll_report,
    }


def _read_pyinstaller_archive_entries(path: Path) -> dict[str, bytes]:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(Path(path)))
    selected: dict[str, bytes] = {}
    for name in archive.toc:
        normalized = name.replace("/", "\\").lower()
        if (
            normalized.endswith("unified_can_lin_host_tool\\_tool_build_identity.json")
            or normalized.endswith(
                "unified_can_lin_host_tool\\update\\release_public_keys.json"
            )
            or (
                "unified_can_lin_host_tool-" in normalized
                and normalized.endswith(".dist-info\\metadata")
            )
            or normalized in {"usb2xxx.dll", "libusb-1.0.dll"}
        ):
            selected[name] = archive.extract(name)
    return selected


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
    root = dist_dir.parent.parent
    public_keys = (
        root / "src" / "unified_can_lin_host_tool" / "update" / "release_public_keys.json"
    ).read_bytes()
    usb_source = json.loads(
        (root / "third_party" / "usb2xxx_runtime_source.json").read_text(encoding="utf-8")
    )
    usb_hashes = {
        name: contract["sha256"] for name, contract in usb_source["files"].items()
    }
    environment_audit_path = root / "build" / "release" / "release-environment-audit.json"
    if not environment_audit_path.is_file():
        raise ValueError("缺少发布虚拟环境分布审计")
    environment_audit = json.loads(environment_audit_path.read_text(encoding="utf-8"))
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
        role = "cli" if name == "EcuReleaseCLI.exe" else "gui"
        archive_report = validate_pyinstaller_archive(
            _read_pyinstaller_archive_entries(path),
            identity=identity,
            public_keys=public_keys,
            role=role,
            usb_hashes=usb_hashes if role == "cli" else {},
        )
        binaries[name] = {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "versionInfo": version_info,
            "archive": archive_report,
        }
    expected_cli_version = f"EcuReleaseCLI {identity.version} (commit {identity.short_commit})"
    cli_version = _run(str(dist_dir / "EcuReleaseCLI.exe"), "--version").stdout.strip()
    if cli_version != expected_cli_version:
        raise ValueError("CLI --version 与构建身份不一致")
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
        "cliVersion": cli_version,
        "environment": environment_audit,
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
    fetch.add_argument("--offline", action="store_true")
    audit = commands.add_parser("audit-build")
    audit.add_argument("--dist-dir", type=Path, required=True)
    audit.add_argument("--installer", type=Path, required=True)
    audit.add_argument("--identity", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    environment = commands.add_parser("audit-environment")
    environment.add_argument("--python", type=Path, required=True)
    environment.add_argument("--lock", type=Path, required=True)
    environment.add_argument("--project-name", default="")
    environment.add_argument("--project-version", default="")
    environment.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "fetch-usb2xxx":
        fetch_usb2xxx_runtime(
            args.source_file, args.output_dir, allow_network=not args.offline
        )
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
    if args.command == "audit-environment":
        report = audit_release_environment(
            args.python,
            args.lock,
            project_name=args.project_name,
            project_version=args.project_version,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return 0

    repo = args.repo.resolve()
    version = str(read_project_version(repo / "pyproject.toml"))
    commit = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    official = is_official_release_environment(os.environ)
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
