from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from unified_can_lin_host_tool.backends.base import HostBackend, HostSession
from unified_can_lin_host_tool.core.cancel import CancellationToken, OperationCancelled
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.ui.models import UiChannel, WorkerEvent


class DeviceScanWorker(QObject):
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, backend: HostBackend) -> None:
        super().__init__()
        self._backend = backend

    @Slot()
    def run(self) -> None:
        try:
            self.result.emit(self._backend.scan())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ConnectWorker(QObject):
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, backend: HostBackend, channel: UiChannel, profile: ToolProfile) -> None:
        super().__init__()
        self._backend = backend
        self._channel = channel
        self._profile = profile

    @Slot()
    def run(self) -> None:
        try:
            self.result.emit(self._backend.connect(self._channel, self._profile))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class UdsWorker(QObject):
    event = Signal(object)
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, session: HostSession, payload: bytes, *, log_dir: Path = Path("logs")) -> None:
        super().__init__()
        self._session = session
        self._payload = payload
        self._log_dir = log_dir
        self._cancel_token = CancellationToken()

    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        try:
            response = self._session.request_uds(
                self._payload,
                log_dir=self._log_dir,
                on_event=self.event.emit,
                cancel_token=self._cancel_token,
            )
            self.result.emit(response)
        except OperationCancelled:
            self.event.emit(WorkerEvent(kind="cancelled", message="operation cancelled"))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class FlashWorker(QObject):
    event = Signal(object)
    result = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        session: HostSession,
        *,
        flash_driver_path: Path,
        app_path: Path,
        log_dir: Path,
        dry_run: bool = True,
        start_in_bootloader: bool = False,
    ) -> None:
        super().__init__()
        self._session = session
        self._flash_driver_path = flash_driver_path
        self._app_path = app_path
        self._log_dir = log_dir
        self._dry_run = dry_run
        self._start_in_bootloader = start_in_bootloader
        self._cancel_token = CancellationToken()

    def cancel(self) -> None:
        self._cancel_token.cancel()

    @Slot()
    def run(self) -> None:
        try:
            events = self._session.flash_e68(
                flash_driver_path=self._flash_driver_path,
                app_path=self._app_path,
                log_dir=self._log_dir,
                dry_run=self._dry_run,
                start_in_bootloader=self._start_in_bootloader,
                on_event=self.event.emit,
                cancel_token=self._cancel_token,
            )
            self.result.emit(events)
        except OperationCancelled:
            self.event.emit(WorkerEvent(kind="cancelled", message="operation cancelled"))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
