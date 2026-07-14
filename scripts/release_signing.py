"""生成独立发布密钥并为更新信息生成原始 Ed25519 签名。"""

from __future__ import annotations

import argparse
import getpass
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

parse_release_public_keys = import_module(
    "unified_can_lin_host_tool.update.release_keys"
).parse_release_public_keys
SemanticVersion = import_module(
    "unified_can_lin_host_tool.versioning"
).SemanticVersion


_SIGNING_KEY_ENVIRONMENT = "UPDATE_SIGNING_KEY_PEM"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _harden_windows_private_key(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    domain = os.environ.get("USERDOMAIN")
    username = os.environ.get("USERNAME") or getpass.getuser()
    principal = f"{domain}\\{username}" if domain else username
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(F)"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def generate_release_key(private_output: Path, public_output: Path, key_id: str) -> None:
    """独占创建私钥，加固权限后原子替换公钥 JSON。"""

    if not key_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in key_id):
        raise ValueError("keyId 只能使用小写字母、数字和连字符")
    private_output.parent.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    try:
        with private_output.open("xb") as private_file:
            private_file.write(private_pem)
            private_file.flush()
            os.fsync(private_file.fileno())
    except FileExistsError as exc:
        raise FileExistsError(f"拒绝覆盖现有发布私钥：{private_output}") from exc

    try:
        _harden_windows_private_key(private_output)
    except (OSError, subprocess.SubprocessError) as exc:
        private_output.unlink(missing_ok=True)
        raise RuntimeError("发布私钥权限设置失败，已删除新私钥") from exc

    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    encoded = json.dumps({key_id: public_key.hex()}, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_write(public_output, encoded)


def load_signing_key_from_environment() -> Ed25519PrivateKey:
    """只从进程环境读取发布私钥，不接受路径或命令行文本。"""

    pem = os.environ.get(_SIGNING_KEY_ENVIRONMENT)
    if not pem:
        raise RuntimeError(f"环境变量 {_SIGNING_KEY_ENVIRONMENT} 未设置")
    try:
        private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("环境变量中的发布私钥不是有效 Ed25519 PEM") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError("环境变量中的发布私钥不是 Ed25519 私钥")
    return private_key


def assert_public_key_matches(
    private_key: Ed25519PrivateKey,
    public_keys_path: Path,
    key_id: str,
) -> None:
    """确认环境私钥对应仓库中指定 keyId 的固化公钥。"""

    try:
        public_keys = parse_release_public_keys(public_keys_path.read_bytes())
        expected = public_keys[key_id]
    except OSError as exc:
        raise RuntimeError("发布公钥文件无法读取") from exc
    except KeyError as exc:
        raise RuntimeError("发布公钥文件或 keyId 无效") from exc
    actual = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if len(expected) != 32 or actual != expected:
        raise RuntimeError("发布私钥与固化公钥不匹配")


def sign_update_file(input_path: Path, signature_path: Path) -> None:
    """使用环境私钥签署输入文件的完整原始字节。"""

    private_key = load_signing_key_from_environment()
    signature = private_key.sign(input_path.read_bytes())
    _atomic_write(signature_path, signature)


def build_update_json(
    *,
    repository: str,
    version: str,
    commit: str,
    generated_at: str,
    release_notes: str,
    installer: Path,
    key_id: str,
) -> bytes:
    """按固定字段顺序生成待签名的稳定通道更新信息。"""

    parsed = SemanticVersion.parse(version)
    payload = {
        "schemaVersion": 1,
        "repository": repository,
        "version": str(parsed),
        "tag": f"v{parsed}",
        "commit": commit,
        "generatedAt": generated_at,
        "channel": "stable",
        "releaseNotes": release_notes,
        "installer": {
            "name": installer.name,
            "size": installer.stat().st_size,
            "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        },
        "keyId": key_id,
    }
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_sha256sums(files: Sequence[Path], output: Path) -> None:
    """按调用方给定顺序写入 GNU 风格 SHA-256 清单。"""

    rows = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in files]
    output.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="自动更新发布签名工具")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate-key", help="生成独立发布密钥对")
    generate.add_argument("--private-output", required=True, type=Path)
    generate.add_argument("--public-output", required=True, type=Path)
    generate.add_argument("--key-id", required=True)

    sign = commands.add_parser("sign", help="使用环境私钥签署更新信息")
    sign.add_argument("--input", required=True, type=Path)
    sign.add_argument("--signature-output", required=True, type=Path)

    verify_key = commands.add_parser("assert-key", help="核对环境私钥与固化公钥")
    verify_key.add_argument("--public-keys", required=True, type=Path)
    verify_key.add_argument("--key-id", required=True)

    build_update = commands.add_parser("build-update", help="生成规范更新信息")
    build_update.add_argument("--repository", required=True)
    build_update.add_argument("--version", required=True)
    build_update.add_argument("--commit", required=True)
    build_update.add_argument("--generated-at", required=True)
    build_update.add_argument("--release-notes", required=True, type=Path)
    build_update.add_argument("--installer", required=True, type=Path)
    build_update.add_argument("--key-id", required=True)
    build_update.add_argument("--output", required=True, type=Path)

    checksums = commands.add_parser("write-sha256sums", help="生成发布文件 SHA-256 清单")
    checksums.add_argument("--output", required=True, type=Path)
    checksums.add_argument("files", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argv)
    try:
        if arguments.command == "generate-key":
            generate_release_key(arguments.private_output, arguments.public_output, arguments.key_id)
        elif arguments.command == "sign":
            sign_update_file(arguments.input, arguments.signature_output)
        elif arguments.command == "assert-key":
            assert_public_key_matches(
                load_signing_key_from_environment(),
                arguments.public_keys,
                arguments.key_id,
            )
        elif arguments.command == "build-update":
            raw = build_update_json(
                repository=arguments.repository,
                version=arguments.version,
                commit=arguments.commit,
                generated_at=arguments.generated_at,
                release_notes=arguments.release_notes.read_text(encoding="utf-8"),
                installer=arguments.installer,
                key_id=arguments.key_id,
            )
            _atomic_write(arguments.output, raw)
        elif arguments.command == "write-sha256sums":
            write_sha256sums(arguments.files, arguments.output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
