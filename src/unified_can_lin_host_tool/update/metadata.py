"""严格解析并验证自动更新信息。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from unified_can_lin_host_tool.update.errors import UpdateMetadataError, UpdateSecurityError
from unified_can_lin_host_tool.versioning import SemanticVersion


MAX_UPDATE_JSON_BYTES = 64 * 1024
MAX_RELEASE_NOTES_BYTES = 16 * 1024
MAX_RELEASE_KEYS = 4

_TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "repository",
    "version",
    "tag",
    "commit",
    "generatedAt",
    "channel",
    "releaseNotes",
    "installer",
    "keyId",
}
_INSTALLER_FIELDS = {"name", "size", "sha256"}
_LOWER_HEX_40_RE = re.compile(r"[0-9a-f]{40}\Z")
_LOWER_HEX_64_RE = re.compile(r"[0-9a-f]{64}\Z")
_GENERATED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


@dataclass(frozen=True)
class InstallerAsset:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateInfo:
    repository: str
    version: SemanticVersion
    tag: str
    commit: str
    generated_at: str
    channel: str
    release_notes: str
    installer: InstallerAsset
    verified_key_id: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateMetadataError(f"JSON 包含重复字段：{key}")
        result[key] = value
    return result


def _parse_json_object(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdateMetadataError("更新信息不是有效 UTF-8") from exc
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except UpdateMetadataError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise UpdateMetadataError("更新信息不是有效 JSON") from exc
    if type(document) is not dict:
        raise UpdateMetadataError("更新信息顶层必须是 JSON 对象")
    return document


def _check_raw_size(raw: bytes) -> None:
    if not isinstance(raw, bytes):
        raise UpdateMetadataError("更新信息必须是原始字节")
    if len(raw) > MAX_UPDATE_JSON_BYTES:
        raise UpdateMetadataError("更新信息超过 64 KiB")


def _require_exact_fields(document: dict[str, Any], expected: set[str], scope: str) -> None:
    unknown = set(document) - expected
    missing = expected - set(document)
    if unknown:
        raise UpdateMetadataError(f"{scope}包含未知字段：{sorted(unknown)[0]}")
    if missing:
        raise UpdateMetadataError(f"{scope}缺少字段：{sorted(missing)[0]}")


def _require_string(document: dict[str, Any], name: str) -> str:
    value = document[name]
    if type(value) is not str:
        raise UpdateMetadataError(f"{name} 必须是字符串")
    return value


def _parse_version(value: str) -> SemanticVersion:
    try:
        return SemanticVersion.parse(value)
    except ValueError as exc:
        raise UpdateMetadataError(f"版本无效：{exc}") from exc


def parse_locator_tag(raw: bytes) -> str:
    """从未信任的最新版本定位副本中仅提取严格标签。"""

    _check_raw_size(raw)
    document = _parse_json_object(raw)
    if "tag" not in document or type(document["tag"]) is not str:
        raise UpdateMetadataError("定位副本缺少字符串 tag")
    tag = document["tag"]
    if not tag.startswith("v"):
        raise UpdateMetadataError("定位标签必须以 v 开头")
    version = _parse_version(tag[1:])
    if tag != f"v{version}":
        raise UpdateMetadataError("定位标签格式无效")
    return tag


def _validated_public_keys(public_keys: Mapping[str, bytes]) -> list[tuple[str, bytes]]:
    if not isinstance(public_keys, Mapping):
        raise UpdateSecurityError("发布公钥集合无效")
    items = list(public_keys.items())
    if not items:
        raise UpdateSecurityError("没有可用于验签的发布公钥")
    if len(items) > MAX_RELEASE_KEYS:
        raise UpdateSecurityError("发布公钥不能超过 4 把")
    seen_keys: set[bytes] = set()
    validated: list[tuple[str, bytes]] = []
    for key_id, public_key in items:
        if type(key_id) is not str or not key_id:
            raise UpdateSecurityError("发布公钥 keyId 无效")
        if type(public_key) is not bytes or len(public_key) != 32:
            raise UpdateSecurityError(f"发布公钥 {key_id} 必须是 32 字节")
        if public_key in seen_keys:
            raise UpdateSecurityError("发布公钥集合包含重复公钥")
        seen_keys.add(public_key)
        validated.append((key_id, public_key))
    return validated


def _verify_with_unique_key(
    raw: bytes,
    signature: bytes,
    public_keys: Mapping[str, bytes],
) -> str:
    if type(signature) is not bytes or len(signature) != 64:
        raise UpdateSecurityError("更新签名必须恰好为 64 字节")
    matches: list[str] = []
    for key_id, public_key in _validated_public_keys(public_keys):
        try:
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw)
        except InvalidSignature:
            continue
        except ValueError as exc:
            raise UpdateSecurityError(f"发布公钥 {key_id} 无效") from exc
        matches.append(key_id)
    if len(matches) != 1:
        raise UpdateSecurityError("更新签名未通过唯一内置发布公钥验证")
    return matches[0]


def verify_signed_update(
    raw: bytes,
    signature: bytes,
    public_keys: Mapping[str, bytes],
    expected_repository: str,
) -> UpdateInfo:
    """先验证原始字节签名，再严格解析所有可信字段。"""

    _check_raw_size(raw)
    verified_key_id = _verify_with_unique_key(raw, signature, public_keys)
    document = _parse_json_object(raw)
    _require_exact_fields(document, _TOP_LEVEL_FIELDS, "更新信息")

    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        raise UpdateMetadataError("schemaVersion 必须是整数 1")

    repository = _require_string(document, "repository")
    if repository != expected_repository:
        raise UpdateSecurityError("更新信息仓库与固化仓库不一致")

    version = _parse_version(_require_string(document, "version"))
    tag = _require_string(document, "tag")
    if tag != f"v{version}":
        raise UpdateMetadataError("标签与版本不一致")

    commit = _require_string(document, "commit")
    if _LOWER_HEX_40_RE.fullmatch(commit) is None:
        raise UpdateMetadataError("提交号必须是 40 位小写十六进制")

    generated_at = _require_string(document, "generatedAt")
    if _GENERATED_AT_RE.fullmatch(generated_at) is None:
        raise UpdateMetadataError("generatedAt 必须是 UTC 秒级时间")

    channel = _require_string(document, "channel")
    if channel != "stable":
        raise UpdateSecurityError("更新通道必须是 stable")

    release_notes = _require_string(document, "releaseNotes")
    if len(release_notes.encode("utf-8")) > MAX_RELEASE_NOTES_BYTES:
        raise UpdateMetadataError("发布说明超过 16 KiB")

    installer_document = document["installer"]
    if type(installer_document) is not dict:
        raise UpdateMetadataError("installer 必须是 JSON 对象")
    _require_exact_fields(installer_document, _INSTALLER_FIELDS, "installer")
    installer_name = _require_string(installer_document, "name")
    expected_installer_name = f"EcuReleaseTool_Setup_{version}.exe"
    if installer_name != expected_installer_name:
        raise UpdateMetadataError("安装包名称与版本不一致")
    installer_size = installer_document["size"]
    if type(installer_size) is not int or installer_size <= 0:
        raise UpdateMetadataError("安装包大小必须是正整数")
    installer_sha256 = _require_string(installer_document, "sha256")
    if _LOWER_HEX_64_RE.fullmatch(installer_sha256) is None:
        raise UpdateMetadataError("安装包 SHA-256 必须是 64 位小写十六进制")

    key_id = _require_string(document, "keyId")
    if key_id != verified_key_id:
        raise UpdateSecurityError("更新信息 keyId 与实际验签公钥不一致")

    return UpdateInfo(
        repository=repository,
        version=version,
        tag=tag,
        commit=commit,
        generated_at=generated_at,
        channel=channel,
        release_notes=release_notes,
        installer=InstallerAsset(installer_name, installer_size, installer_sha256),
        verified_key_id=verified_key_id,
    )
