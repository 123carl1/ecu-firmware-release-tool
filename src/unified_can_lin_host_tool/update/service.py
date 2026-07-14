"""编排正式更新检查并安全缓存已验证安装包。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from unified_can_lin_host_tool.tool_identity import ToolIdentity
from unified_can_lin_host_tool.update.errors import UpdateIntegrityError, UpdateSecurityError
from unified_can_lin_host_tool.update.github_release import GitHubReleaseSource
from unified_can_lin_host_tool.update.https_client import SafeHttpsClient
from unified_can_lin_host_tool.update.metadata import UpdateInfo
from unified_can_lin_host_tool.versioning import SemanticVersion


ProgressCallback = Callable[[int, int], None]


class UpdateService:
    """只使用正式构建身份中的仓库和已验签更新信息。"""

    def __init__(
        self,
        identity: ToolIdentity,
        source: GitHubReleaseSource,
        http: SafeHttpsClient,
        cache_root: Path,
        public_keys: Mapping[str, bytes],
    ):
        self._identity = identity
        self._source = source
        self._http = http
        self._cache_root = Path(cache_root)
        self._public_keys = public_keys

    def check(self) -> UpdateInfo | None:
        if not self._identity.official_build or not self._identity.repository:
            return None
        info = self._source.fetch(self._public_keys)
        self._validate_source_identity(info)
        if info.version <= SemanticVersion.parse(self._identity.version):
            return None
        return info

    def download(
        self,
        info: UpdateInfo,
        *,
        progress: ProgressCallback | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> Path:
        self._validate_download_info(info)
        version_dir = self._cache_root / str(info.version)
        version_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        version_dir.chmod(0o700)
        target = version_dir / info.installer.name

        if target.exists():
            if self._file_matches(target, info):
                if progress is not None:
                    progress(info.installer.size, info.installer.size)
                return target
            self._remove_file(target)

        part = version_dir / f".{info.installer.name}.{uuid4().hex}.part"
        digest = hashlib.sha256()
        written = 0
        try:
            if cancelled():
                raise UpdateIntegrityError("安装包下载已取消")
            with part.open("xb") as stream:
                part.chmod(0o600)
                for chunk in self._http.iter_bytes(
                    self._installer_url(info), max_bytes=info.installer.size
                ):
                    if cancelled():
                        raise UpdateIntegrityError("安装包下载已取消")
                    if not isinstance(chunk, bytes):
                        raise UpdateIntegrityError("安装包下载数据类型无效")
                    written += len(chunk)
                    if written > info.installer.size:
                        raise UpdateIntegrityError("安装包实际大小超过声明大小")
                    stream.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(written, info.installer.size)
                if cancelled():
                    raise UpdateIntegrityError("安装包下载已取消")
                if written != info.installer.size:
                    raise UpdateIntegrityError("安装包实际大小与声明大小不一致")
                if digest.hexdigest() != info.installer.sha256:
                    raise UpdateIntegrityError("安装包 SHA-256 校验失败")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(part, target)
            return target
        except BaseException:
            self._remove_file(part)
            self._remove_file(target)
            raise

    def _validate_source_identity(self, info: UpdateInfo) -> None:
        if info.repository != self._identity.repository:
            raise UpdateSecurityError("更新信息仓库与正式构建固化仓库不一致")
        if info.channel != "stable":
            raise UpdateSecurityError("只接受 stable 正式更新")

    def _validate_download_info(self, info: UpdateInfo) -> None:
        if not self._identity.official_build or not self._identity.repository:
            raise UpdateSecurityError("开发构建不能下载正式更新")
        self._validate_source_identity(info)
        if info.version <= SemanticVersion.parse(self._identity.version):
            raise UpdateSecurityError("同版本或降级安装包不得下载")
        expected_name = f"EcuReleaseTool_Setup_{info.version}.exe"
        if info.tag != f"v{info.version}" or info.installer.name != expected_name:
            raise UpdateSecurityError("更新标签或安装包名称与目标版本不一致")

    def _installer_url(self, info: UpdateInfo) -> str:
        return (
            f"https://github.com/{self._identity.repository}/releases/download/"
            f"{info.tag}/{info.installer.name}"
        )

    @staticmethod
    def _file_matches(path: Path, info: UpdateInfo) -> bool:
        try:
            if path.stat().st_size != info.installer.size:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == info.installer.sha256
        except OSError:
            return False

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def installer_arguments(*, parent_pid: int, log_path: Path) -> list[str]:
    if type(parent_pid) is not int or parent_pid <= 0:
        raise ValueError("父进程号必须为正十进制整数")
    absolute_log_path = Path(log_path).resolve()
    return [
        "/SILENT",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/AUTO_UPDATE",
        f"/PARENT_PID={parent_pid}",
        f"/LOG={absolute_log_path}",
    ]
