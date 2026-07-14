"""只允许 GitHub 发布资源地址的有界 HTTPS 读取。"""

from __future__ import annotations

from collections.abc import Iterator
import http.client
import ssl
from urllib.parse import urljoin, urlsplit, urlunsplit

from unified_can_lin_host_tool.update.errors import UpdateNetworkError


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5
_READ_CHUNK_BYTES = 64 * 1024


def validate_github_https_url(url: str) -> None:
    """拒绝 GitHub HTTPS 443 白名单以外的网络目标。"""

    if type(url) is not str or not url or any(ord(character) < 0x20 for character in url):
        raise UpdateNetworkError("更新地址无效")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UpdateNetworkError("更新地址无效") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or host is None
        or (port is not None and port != 443)
        or parsed.fragment
    ):
        raise UpdateNetworkError("更新地址不满足 HTTPS 安全约束")
    normalized_host = host.lower().rstrip(".")
    if normalized_host != host.lower():
        raise UpdateNetworkError("更新地址主机名无效")
    if normalized_host != "github.com" and not normalized_host.endswith(".githubusercontent.com"):
        raise UpdateNetworkError("更新地址不在允许的 GitHub 主机范围内")


class SafeHttpsClient:
    """使用系统 TLS 信任库读取大小受限的 GitHub HTTPS 内容。"""

    def read_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 15.0,
        no_cache: bool = False,
    ) -> bytes:
        return b"".join(
            self._iter_bytes(
                url,
                max_bytes=max_bytes,
                connect_timeout_s=connect_timeout_s,
                read_timeout_s=read_timeout_s,
                no_cache=no_cache,
            )
        )

    def iter_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 60.0,
    ) -> Iterator[bytes]:
        return self._iter_bytes(
            url,
            max_bytes=max_bytes,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
            no_cache=False,
        )

    def _iter_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        connect_timeout_s: float,
        read_timeout_s: float,
        no_cache: bool,
    ) -> Iterator[bytes]:
        self._validate_limits(max_bytes, connect_timeout_s, read_timeout_s)
        current_url = url
        redirect_count = 0

        while True:
            validate_github_https_url(current_url)
            parsed = urlsplit(current_url)
            connection = http.client.HTTPSConnection(
                parsed.hostname,
                443,
                timeout=connect_timeout_s,
                context=ssl.create_default_context(),
            )
            response = None
            try:
                headers = {
                    "Accept": "application/octet-stream",
                    "Connection": "close",
                    "User-Agent": "ecu-firmware-release-tool-updater",
                }
                if no_cache:
                    headers["Cache-Control"] = "no-cache"
                    headers["Pragma"] = "no-cache"
                request_target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                connection.request("GET", request_target, headers=headers)
                if connection.sock is not None:
                    connection.sock.settimeout(read_timeout_s)
                response = connection.getresponse()

                if response.status in _REDIRECT_STATUSES:
                    location = response.getheader("Location")
                    if not location:
                        raise UpdateNetworkError("更新服务器重定向缺少目标地址")
                    if redirect_count >= _MAX_REDIRECTS:
                        raise UpdateNetworkError("更新服务器重定向次数超过 5 次")
                    next_url = urljoin(current_url, location)
                    validate_github_https_url(next_url)
                    redirect_count += 1
                    current_url = next_url
                    continue

                if response.status < 200 or response.status >= 300:
                    raise UpdateNetworkError("更新服务器返回非成功状态")

                content_length = self._parse_content_length(response.getheader("Content-Length"))
                if content_length is not None and content_length > max_bytes:
                    raise UpdateNetworkError("更新内容大小超过允许上限")

                total = 0
                while True:
                    chunk = response.read(min(_READ_CHUNK_BYTES, max_bytes - total + 1))
                    if not chunk:
                        return
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateNetworkError("更新内容大小超过允许上限")
                    yield chunk
            except UpdateNetworkError:
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
                raise UpdateNetworkError("更新网络读取失败") from exc
            finally:
                if response is not None:
                    response.close()
                connection.close()

    @staticmethod
    def _validate_limits(max_bytes: int, connect_timeout_s: float, read_timeout_s: float) -> None:
        if type(max_bytes) is not int or max_bytes < 0:
            raise UpdateNetworkError("更新读取大小上限无效")
        if connect_timeout_s <= 0 or read_timeout_s <= 0:
            raise UpdateNetworkError("更新网络超时参数无效")

    @staticmethod
    def _parse_content_length(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            length = int(value, 10)
        except ValueError as exc:
            raise UpdateNetworkError("更新内容长度无效") from exc
        if length < 0:
            raise UpdateNetworkError("更新内容长度无效")
        return length
