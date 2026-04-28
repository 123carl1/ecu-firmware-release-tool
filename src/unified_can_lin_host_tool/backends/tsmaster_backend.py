from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from unified_can_lin_host_tool.adapters.tsmaster import TsmasterAdapter
from unified_can_lin_host_tool.backends.fake_backend import TraceEventBridge
from unified_can_lin_host_tool.backends.settings import TsmasterSettings
from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_firmware_image
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport
from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice, WorkerEvent

EventCallback = Callable[[WorkerEvent], None]


class TsmasterHostSession:
    def __init__(self, *, profile: ToolProfile, adapter, close_mode: str = "skip") -> None:
        self.profile = profile
        self.adapter = adapter
        self.close_mode = close_mode
        self.bus_session = BusSession()
        self.transport = LinDiagTransport(adapter, profile)

    def request_uds(
        self,
        payload: bytes,
        *,
        log_dir: Path | None = None,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> bytes:
        _throw_if_cancelled(cancel_token)
        if not self.bus_session.enter_diag_exclusive("uds"):
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN channel is busy")

        trace_logger = TraceLogger(log_dir) if log_dir is not None else None
        try:
            bridge = TraceEventBridge(trace_logger, on_event) if trace_logger is not None or on_event is not None else None
            transport = LinDiagTransport(self.adapter, self.profile, trace_logger=bridge)
            return transport.request(payload, cancel_token=cancel_token).payload
        finally:
            if trace_logger is not None:
                trace_logger.close()
            self.bus_session.release_diag_exclusive("uds")

    def flash_e68(
        self,
        *,
        flash_driver_path: Path,
        app_path: Path,
        log_dir: Path,
        dry_run: bool = True,
        start_in_bootloader: bool = False,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> list[WorkerEvent]:
        _throw_if_cancelled(cancel_token)
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event is not None:
                on_event(event)

        emit(WorkerEvent(kind="started", message="E68 flash started", progress=0))
        trace_logger = TraceLogger(log_dir)
        try:
            flash_driver = load_firmware_image(
                flash_driver_path,
                start_address=self.profile.memory.flash_driver_ram,
                max_size=self.profile.memory.flash_driver_max_size,
            )
            app = load_firmware_image(
                app_path,
                start_address=self.profile.memory.app_start,
                max_size=self.profile.memory.app_size,
            )
            emit(WorkerEvent(kind="progress", message="镜像加载完成", progress=8))
            if dry_run:
                emit(WorkerEvent(kind="result", message="DRY RUN", progress=100))
                return events

            bridge = TraceEventBridge(trace_logger, emit)
            transport = LinDiagTransport(self.adapter, self.profile, trace_logger=bridge)
            workflow = FlashWorkflow(
                self.profile,
                transport,
                self.bus_session,
                progress_callback=lambda progress: emit(
                    WorkerEvent(kind="progress", message=progress.message, progress=progress.percent)
                ),
            )
            result = workflow.run(
                flash_driver=flash_driver,
                app=app,
                start_in_bootloader=start_in_bootloader,
                cancel_token=cancel_token,
            )
            if result.success:
                emit(WorkerEvent(kind="progress", message="Flash workflow completed", progress=100))
                emit(WorkerEvent(kind="result", message="FLASH SUCCESS", progress=100))
            return events
        finally:
            trace_logger.close()

    def close(self) -> None:
        if self.close_mode == "skip":
            return
        self.adapter.close()


class TsmasterHostBackend:
    name = "TSMaster"

    def __init__(self, *, settings: TsmasterSettings | None = None, adapter_cls=TsmasterAdapter) -> None:
        self.settings = settings or TsmasterSettings()
        self.adapter_cls = adapter_cls

    def scan(self) -> list[UiDevice]:
        devices = self.adapter_cls.probe(
            dll_path=self.settings.dll_path,
            app_name=f"{self.settings.app_name}_Probe",
        )
        if self.settings.hw_name:
            devices = [device for device in devices if device.name == self.settings.hw_name]
        return [
            UiDevice(
                vendor="TSMaster",
                name=device.name,
                serial=device.serial,
                channels=[
                    UiChannel(
                        vendor="TSMaster",
                        device_name=device.name,
                        channel_name=f"{device.name} 设备，默认 LIN 映射",
                        bus="LIN",
                        channel_index=self.settings.app_channel,
                        mapping={
                            "dll_path": self.settings.dll_path,
                            "app_name": self.settings.app_name,
                            "project_dir": self.settings.project_dir,
                            "app_channel": self.settings.app_channel,
                            "hw_name": self.settings.hw_name,
                            "hw_subtype": self.settings.hw_subtype,
                            "hw_index": device.device_index,
                            "hw_channel": self.settings.hw_channel,
                            "close_mode": self.settings.close_mode,
                        },
                        capabilities=("lin_raw", "lin_diag", "e68_flash"),
                    )
                ],
            )
            for device in devices
        ]

    def connect(self, channel: UiChannel, profile: ToolProfile) -> TsmasterHostSession:
        if channel.bus != "LIN":
            raise ValueError("TSMaster backend only supports LIN in M1 Alpha")
        mapping = channel.mapping
        adapter = self.adapter_cls(
            dll_path=str(mapping.get("dll_path", self.settings.dll_path)),
            app_name=str(mapping.get("app_name", self.settings.app_name)),
            project_dir=_optional_path(mapping.get("project_dir", self.settings.project_dir)),
            app_channel=int(mapping.get("app_channel", self.settings.app_channel)),
            hw_name=str(mapping.get("hw_name", self.settings.hw_name)),
            hw_subtype=int(mapping.get("hw_subtype", self.settings.hw_subtype)),
            hw_index=int(mapping.get("hw_index", self.settings.hw_index)),
            hw_channel=int(mapping.get("hw_channel", self.settings.hw_channel)),
            baud_kbps=profile.bus.baudrate / 1000.0,
        )
        adapter.open_lin()
        return TsmasterHostSession(
            profile=profile,
            adapter=adapter,
            close_mode=str(mapping.get("close_mode", self.settings.close_mode)),
        )


def _optional_path(value) -> Path | None:
    if value is None or value == "":
        return None
    return Path(str(value))


def _throw_if_cancelled(cancel_token: CancellationToken | None) -> None:
    if cancel_token is not None:
        cancel_token.throw_if_cancelled()
