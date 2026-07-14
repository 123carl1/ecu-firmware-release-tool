from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PySide6.QtWidgets import QApplication

from unified_can_lin_host_tool.tool_identity import ToolIdentity
from unified_can_lin_host_tool.ui import release_workspace
from unified_can_lin_host_tool.ui.app import build_parser
from unified_can_lin_host_tool.ui.release_workspace import ReleaseMainWindow
from unified_can_lin_host_tool.update.errors import (
    UpdateIntegrityError,
    UpdateSecurityError,
)
from unified_can_lin_host_tool.update.github_release import GitHubReleaseSource
from unified_can_lin_host_tool.update.runtime_mutex import (
    is_product_mutex_present,
    product_run_mutex,
)
from unified_can_lin_host_tool.update.service import UpdateService, installer_arguments
from unified_can_lin_host_tool.versioning import SemanticVersion


REPOSITORY = "owner/ecu-firmware-release-tool"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"{RELEASES_URL}/latest/download/update.json"


@dataclass(frozen=True)
class _SignedRelease:
    version: str
    locator: bytes
    metadata: bytes
    signature: bytes
    installer: bytes

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def metadata_url(self) -> str:
        return f"{RELEASES_URL}/download/{self.tag}/update.json"

    @property
    def installer_url(self) -> str:
        return (
            f"{RELEASES_URL}/download/{self.tag}/"
            f"EcuReleaseTool_Setup_{self.version}.exe"
        )


class _MemoryHttpsClient:
    """保留真实 URL/流式接口，只替代外部 HTTPS 传输。"""

    def __init__(self) -> None:
        self._reads: dict[str, deque[bytes]] = {}
        self._streams: dict[str, bytes] = {}
        self.calls: list[tuple[str, str, int, bool]] = []
        self.on_stream_start = None

    def queue_read(self, url: str, *responses: bytes) -> None:
        self._reads[url] = deque(responses)

    def add_stream(self, url: str, payload: bytes) -> None:
        self._streams[url] = payload

    def read_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 15.0,
        no_cache: bool = False,
    ) -> bytes:
        del connect_timeout_s, read_timeout_s
        self.calls.append(("read", url, max_bytes, no_cache))
        responses = self._reads[url]
        response = responses.popleft() if len(responses) > 1 else responses[0]
        assert len(response) <= max_bytes
        return response

    def iter_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        connect_timeout_s: float = 5.0,
        read_timeout_s: float = 60.0,
    ):
        del connect_timeout_s, read_timeout_s
        self.calls.append(("stream", url, max_bytes, False))
        if self.on_stream_start is not None:
            self.on_stream_start()
        payload = self._streams[url]
        midpoint = max(1, len(payload) // 2)
        yield payload[:midpoint]
        yield payload[midpoint:]


@pytest.fixture
def signing_key() -> tuple[Ed25519PrivateKey, dict[str, bytes]]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return private_key, {"test-v1": public_key}


def _make_release(
    private_key: Ed25519PrivateKey,
    version: str,
    *,
    installer: bytes = b"complete signed installer payload",
    claimed_sha256: str | None = None,
) -> _SignedRelease:
    tag = f"v{version}"
    payload = {
        "schemaVersion": 1,
        "repository": REPOSITORY,
        "version": version,
        "tag": tag,
        "commit": "02" * 20,
        "generatedAt": "2026-07-14T13:00:00Z",
        "channel": "stable",
        "releaseNotes": "闭环集成验证。",
        "installer": {
            "name": f"EcuReleaseTool_Setup_{version}.exe",
            "size": len(installer),
            "sha256": claimed_sha256 or hashlib.sha256(installer).hexdigest(),
        },
        "keyId": "test-v1",
    }
    metadata = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return _SignedRelease(
        version=version,
        locator=json.dumps({"tag": tag}, separators=(",", ":")).encode(),
        metadata=metadata,
        signature=private_key.sign(metadata),
        installer=installer,
    )


def _add_release(http: _MemoryHttpsClient, release: _SignedRelease) -> None:
    http.queue_read(release.metadata_url, release.metadata)
    http.queue_read(f"{release.metadata_url}.sig", release.signature)
    http.add_stream(release.installer_url, release.installer)


def _identity(version: str = "0.2.0") -> ToolIdentity:
    return ToolIdentity(
        version,
        "01" * 20,
        "2026-07-14T12:00:00Z",
        REPOSITORY,
        True,
    )


def _service(
    version: str,
    http: _MemoryHttpsClient,
    cache_root: Path,
    public_keys: dict[str, bytes],
) -> UpdateService:
    return UpdateService(
        _identity(version),
        GitHubReleaseSource(REPOSITORY, http),
        http,
        cache_root,
        public_keys,
    )


def test_020_to_021_uses_paired_signature_atomic_cache_and_installer_arguments(
    signing_key, tmp_path: Path
):
    private_key, public_keys = signing_key
    release = _make_release(private_key, "0.2.1")
    http = _MemoryHttpsClient()
    http.queue_read(LATEST_URL, release.locator)
    _add_release(http, release)
    cache_root = tmp_path / "updates"
    stale = cache_root / "0.2.1" / "EcuReleaseTool_Setup_0.2.1.exe"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale cache")
    observed_part_names: list[str] = []

    def observe_part_before_first_download_chunk() -> None:
        assert not stale.exists()
        parts = list(stale.parent.glob("*.part"))
        assert len(parts) == 1
        observed_part_names.append(parts[0].name)

    http.on_stream_start = observe_part_before_first_download_chunk
    service = _service("0.2.0", http, cache_root, public_keys)

    info = service.check()
    assert info is not None
    assert info.version == SemanticVersion.parse("0.2.1")
    installer = service.download(info)

    assert installer == stale
    assert installer.read_bytes() == release.installer
    assert observed_part_names and not list(stale.parent.glob("*.part"))
    assert all("latest/download/update.json.sig" not in call[1] for call in http.calls)
    assert installer_arguments(parent_pid=4321, log_path=tmp_path / "installer.log") == [
        "/SILENT",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/AUTO_UPDATE",
        "/PARENT_PID=4321",
        f"/LOG={(tmp_path / 'installer.log').resolve()}",
    ]


def test_stale_latest_locator_relocates_once_and_never_cross_pairs_tags(
    signing_key, tmp_path: Path
):
    private_key, public_keys = signing_key
    old_release = _make_release(private_key, "0.2.1")
    new_release = _make_release(private_key, "0.2.2")
    http = _MemoryHttpsClient()
    http.queue_read(LATEST_URL, old_release.locator, new_release.locator)
    http.queue_read(old_release.metadata_url, old_release.metadata)
    http.queue_read(f"{old_release.metadata_url}.sig", b"x" * 64)
    _add_release(http, new_release)

    info = _service("0.2.0", http, tmp_path, public_keys).check()

    assert info is not None and str(info.version) == "0.2.2"
    latest_calls = [call for call in http.calls if call[1] == LATEST_URL]
    assert len(latest_calls) == 2
    assert all(call[3] is True for call in latest_calls)
    assert [call[1] for call in http.calls].count(old_release.metadata_url) == 1
    assert [call[1] for call in http.calls].count(new_release.metadata_url) == 1


def test_bad_signature_never_reaches_installer_download(signing_key, tmp_path: Path):
    private_key, public_keys = signing_key
    release = _make_release(private_key, "0.2.1")
    http = _MemoryHttpsClient()
    http.queue_read(LATEST_URL, release.locator)
    http.queue_read(release.metadata_url, release.metadata)
    http.queue_read(f"{release.metadata_url}.sig", b"x" * 64)
    http.add_stream(release.installer_url, release.installer)

    with pytest.raises(UpdateSecurityError, match="签名"):
        _service("0.2.0", http, tmp_path, public_keys).check()

    assert all(call[0] != "stream" for call in http.calls)


def test_wrong_hash_removes_executable_and_part_cache(signing_key, tmp_path: Path):
    private_key, public_keys = signing_key
    release = _make_release(private_key, "0.2.1", claimed_sha256="00" * 32)
    http = _MemoryHttpsClient()
    http.queue_read(LATEST_URL, release.locator)
    _add_release(http, release)
    service = _service("0.2.0", http, tmp_path, public_keys)
    info = service.check()

    with pytest.raises(UpdateIntegrityError, match="SHA-256"):
        service.download(info)

    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.part"))


def test_cancelled_download_removes_executable_and_part_cache(
    signing_key, tmp_path: Path
):
    private_key, public_keys = signing_key
    release = _make_release(private_key, "0.2.1")
    http = _MemoryHttpsClient()
    http.queue_read(LATEST_URL, release.locator)
    _add_release(http, release)
    service = _service("0.2.0", http, tmp_path, public_keys)
    info = service.check()
    cancellation_checks = 0

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 2

    with pytest.raises(UpdateIntegrityError, match="取消"):
        service.download(info, cancelled=cancelled)

    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("installed_version", ["0.2.1", "0.2.2"])
def test_same_version_and_downgrade_are_not_offered(
    signing_key, tmp_path: Path, installed_version: str
):
    private_key, public_keys = signing_key
    release = _make_release(private_key, "0.2.1")
    http = _MemoryHttpsClient()
    http.queue_read(LATEST_URL, release.locator)
    _add_release(http, release)

    assert _service(installed_version, http, tmp_path, public_keys).check() is None
    assert all(call[0] != "stream" for call in http.calls)


def test_multiple_runtime_mutex_handles_keep_product_marked_running():
    assert is_product_mutex_present() is False
    with product_run_mutex():
        assert is_product_mutex_present() is True
        with product_run_mutex():
            assert is_product_mutex_present() is True
        assert is_product_mutex_present() is True
    assert is_product_mutex_present() is False


def test_installer_start_failure_unfreezes_tasks_and_keeps_gui_running(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    window = ReleaseMainWindow(auto_check=False)
    try:
        installer = tmp_path / "EcuReleaseTool_Setup_0.2.1.exe"
        installer.write_bytes(b"process substitute: never executed")
        with patch.object(
            release_workspace.QProcess,
            "startDetached",
            return_value=(False, 0),
        ), patch.object(app, "quit") as quit_app:
            window._launch_verified_installer(installer)

        assert window._tasks_frozen is False
        assert window._update_exit_requested is False
        assert window.scan_button.isEnabled()
        assert "更新安装器启动失败" in window.status_label.text()
        quit_app.assert_not_called()
    finally:
        window.close()


def test_formal_update_service_has_no_user_configurable_repository_url():
    assert list(inspect.signature(UpdateService).parameters) == [
        "identity",
        "source",
        "http",
        "cache_root",
        "public_keys",
    ]
    assert vars(build_parser().parse_args([])) == {"smoke": False}
