"""加载随安装包固化的自动更新发布公钥。"""

from __future__ import annotations

import importlib.resources
import json
import re

from unified_can_lin_host_tool.update.errors import UpdateSecurityError


_MAX_RELEASE_KEYS = 4
_KEY_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_PUBLIC_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise UpdateSecurityError(f"发布公钥文件包含重复 keyId：{key}")
        result[key] = value
    return result


def parse_release_public_keys(raw: bytes) -> dict[str, bytes]:
    """严格解析最多四把 32 字节 Ed25519 发布公钥。"""

    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except UpdateSecurityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateSecurityError("发布公钥文件不是有效 UTF-8 JSON") from exc
    if type(document) is not dict or not document:
        raise UpdateSecurityError("发布公钥文件必须是非空 JSON 对象")
    if len(document) > _MAX_RELEASE_KEYS:
        raise UpdateSecurityError("发布公钥不能超过 4 把")

    keys: dict[str, bytes] = {}
    seen: set[bytes] = set()
    for key_id, encoded in document.items():
        if type(key_id) is not str or _KEY_ID_RE.fullmatch(key_id) is None:
            raise UpdateSecurityError("发布公钥 keyId 无效")
        if type(encoded) is not str or _PUBLIC_KEY_RE.fullmatch(encoded) is None:
            raise UpdateSecurityError(f"发布公钥 {key_id} 必须是 32 字节小写十六进制")
        public_key = bytes.fromhex(encoded)
        if public_key in seen:
            raise UpdateSecurityError("发布公钥文件包含重复公钥")
        seen.add(public_key)
        keys[key_id] = public_key
    return keys


def load_release_public_keys() -> dict[str, bytes]:
    """读取客户端随安装包固化的发布公钥。"""

    resource = importlib.resources.files("unified_can_lin_host_tool.update").joinpath(
        "release_public_keys.json"
    )
    try:
        raw = resource.read_bytes()
    except OSError as exc:
        raise UpdateSecurityError("内置发布公钥文件无法读取") from exc
    return parse_release_public_keys(raw)
