"""从固化 GitHub 仓库安全取得成对的更新信息与签名。"""

from __future__ import annotations

from collections.abc import Mapping
import re

from unified_can_lin_host_tool.update.errors import (
    UpdateMetadataError,
    UpdateSecurityError,
)
from unified_can_lin_host_tool.update.https_client import SafeHttpsClient
from unified_can_lin_host_tool.update.metadata import (
    MAX_UPDATE_JSON_BYTES,
    UpdateInfo,
    parse_locator_tag,
    verify_signed_update,
)


_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/ecu-firmware-release-tool\Z"
)
_SIGNATURE_BYTES = 64


class GitHubReleaseSource:
    """读取固化仓库的稳定通道更新信息。"""

    def __init__(self, repository: str, http: SafeHttpsClient):
        if type(repository) is not str or _REPOSITORY_RE.fullmatch(repository) is None:
            raise UpdateSecurityError("更新仓库身份无效")
        self._repository = repository
        self._http = http
        self._base_url = f"https://github.com/{repository}/releases"

    def fetch(self, public_keys: Mapping[str, bytes]) -> UpdateInfo:
        """定位标签并读取同一标签下的一对已签名资源。"""

        for attempt in range(2):
            try:
                return self._fetch_once(public_keys)
            except (UpdateMetadataError, UpdateSecurityError) as exc:
                if attempt == 1:
                    raise UpdateSecurityError("更新信息资源配对或签名验证失败") from exc
        raise AssertionError("unreachable")

    def _fetch_once(self, public_keys: Mapping[str, bytes]) -> UpdateInfo:
        locator_url = f"{self._base_url}/latest/download/update.json"
        locator = self._http.read_bytes(
            locator_url,
            max_bytes=MAX_UPDATE_JSON_BYTES,
            no_cache=True,
        )
        tag = parse_locator_tag(locator)
        tagged_url = f"{self._base_url}/download/{tag}/update.json"
        raw = self._http.read_bytes(tagged_url, max_bytes=MAX_UPDATE_JSON_BYTES)
        signature = self._http.read_bytes(
            f"{tagged_url}.sig",
            max_bytes=_SIGNATURE_BYTES,
        )
        info = verify_signed_update(raw, signature, public_keys, self._repository)
        if info.tag != tag or f"v{info.version}" != tag:
            raise UpdateSecurityError("更新信息与定位标签无法安全配对")
        return info
