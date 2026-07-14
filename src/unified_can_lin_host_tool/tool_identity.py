"""读取并严格校验当前工具的构建身份。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import importlib.metadata
import importlib.resources
import json
import re
import sys

from unified_can_lin_host_tool.versioning import SemanticVersion


_DISTRIBUTION_NAME = "unified-can-lin-host-tool"
_IDENTITY_RESOURCE = "_tool_build_identity.json"
_IDENTITY_FIELDS = {
    "version",
    "commit",
    "buildTimeUtc",
    "repository",
    "officialBuild",
}
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_UTC_TIME_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/ecu-firmware-release-tool\Z"
)


@dataclass(frozen=True)
class ToolIdentity:
    version: str
    commit: str
    build_time_utc: str
    repository: str
    official_build: bool

    @property
    def short_commit(self) -> str:
        return self.commit[:7] if len(self.commit) == 40 else self.commit


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"构建身份包含重复 JSON key：{key}")
        result[key] = value
    return result


def _decode_identity(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        raise ValueError("构建身份必须为 UTF-8 JSON 字节")
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("构建身份不是有效的 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("构建身份顶层必须是 JSON 对象")
    if set(payload) != _IDENTITY_FIELDS:
        raise ValueError("构建身份字段必须与固定契约完全一致")
    return payload


def load_tool_identity(raw: bytes | None, *, installed_version: str) -> ToolIdentity:
    if raw is None:
        return ToolIdentity(installed_version, "development", "", "", False)

    payload = _decode_identity(raw)
    version = payload["version"]
    commit = payload["commit"]
    build_time_utc = payload["buildTimeUtc"]
    repository = payload["repository"]
    official_build = payload["officialBuild"]

    if type(version) is not str:
        raise ValueError("构建身份版本必须是字符串")
    SemanticVersion.parse(version)
    if version != installed_version:
        raise ValueError("构建身份版本与安装元数据版本不一致")
    if type(commit) is not str or _COMMIT_RE.fullmatch(commit) is None:
        raise ValueError("构建身份提交号必须是 40 位小写十六进制字符")
    if type(build_time_utc) is not str or _UTC_TIME_RE.fullmatch(build_time_utc) is None:
        raise ValueError("构建身份时间必须为以 Z 结尾的 UTC 时间")
    try:
        datetime.strptime(build_time_utc, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("构建身份时间不是有效的 UTC 时间") from exc
    if type(official_build) is not bool:
        raise ValueError("构建身份 officialBuild 必须是布尔值")
    if type(repository) is not str:
        raise ValueError("构建身份仓库必须是字符串")
    if official_build and _REPOSITORY_RE.fullmatch(repository) is None:
        raise ValueError("正式构建身份仓库必须为所有者/ecu-firmware-release-tool")
    if not official_build and repository:
        raise ValueError("开发构建身份仓库必须为空")

    return ToolIdentity(
        version,
        commit,
        build_time_utc,
        repository,
        official_build,
    )


@lru_cache(maxsize=1)
def get_tool_identity() -> ToolIdentity:
    try:
        installed_version = importlib.metadata.version(_DISTRIBUTION_NAME)
    except importlib.metadata.PackageNotFoundError:
        installed_version = "0+unknown"

    raw = None
    if getattr(sys, "frozen", False):
        raw = (
            importlib.resources.files("unified_can_lin_host_tool")
            .joinpath(_IDENTITY_RESOURCE)
            .read_bytes()
        )
    return load_tool_identity(raw, installed_version=installed_version)
