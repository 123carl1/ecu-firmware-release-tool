from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_bin_image
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport
from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice, WorkerEvent

EventCallback = Callable[[WorkerEvent], None]


class TraceEventBridge:
    def __init__(self, logger: TraceLogger | None, emit: EventCallback | None) -> None:
        self._logger = logger
        self._emit = emit

    def write(self, event: TraceEvent) -> None:
        if self._logger is not None:
            self._logger.write(event)
        if self._emit is not None:
            self._emit(WorkerEvent(kind="trace", message="LIN frame", trace=event))


class FakeHostSession:
    def __init__(self, profile: ToolProfile) -> None:
        self.profile = profile
        self.bus_session = BusSession()
        self.adapter = FakeLinAdapter()
        self.transport = LinDiagTransport(self.adapter, profile)

    def request_uds(
        self,
        payload: bytes,
        *,
        log_dir: Path | None = None,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> bytes:
        if not self.bus_session.enter_diag_exclusive("uds"):
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN channel is busy")

        trace_logger = TraceLogger(log_dir) if log_dir is not None else None
        try:
            response_payload = _manual_uds_response(payload)
            self.adapter = FakeLinAdapter(
                responses=[
                    (self.profile.bus.response_id, _lin_single(self.profile.bus.nad, response_payload)),
                ]
            )
            trace_bridge = TraceEventBridge(trace_logger, on_event) if trace_logger is not None or on_event is not None else None
            self.transport = LinDiagTransport(self.adapter, self.profile, trace_logger=trace_bridge)
            return self.transport.request(payload).payload
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
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> list[WorkerEvent]:
        events: list[WorkerEvent] = []

        def emit(event: WorkerEvent) -> None:
            events.append(event)
            if on_event is not None:
                on_event(event)

        emit(WorkerEvent(kind="started", message="E68 flash started", progress=0))
        trace_logger = TraceLogger(log_dir)
        try:
            flash_driver = load_bin_image(
                flash_driver_path,
                start_address=self.profile.memory.flash_driver_ram,
                max_size=self.profile.memory.flash_driver_max_size,
            )
            app = load_bin_image(
                app_path,
                start_address=self.profile.memory.app_start,
                max_size=self.profile.memory.app_size,
            )
            emit(WorkerEvent(kind="progress", message="Firmware images loaded", progress=10))

            adapter = FakeLinAdapter.for_e68_flash_success(
                self.profile,
                flash_driver_data=flash_driver.data,
                app_data=app.data,
            )
            bridge = TraceEventBridge(trace_logger, emit)
            transport = LinDiagTransport(adapter, self.profile, sleep_func=lambda _: None, trace_logger=bridge)
            workflow = FlashWorkflow(self.profile, transport, self.bus_session)

            message = "Fake flash workflow running" if dry_run else "Fake flash workflow running without hardware"
            emit(WorkerEvent(kind="progress", message=message, progress=20))
            result = workflow.run(flash_driver=flash_driver, app=app)
            if result.success:
                emit(WorkerEvent(kind="progress", message="Flash workflow completed", progress=100))
                emit(WorkerEvent(kind="result", message="FLASH SUCCESS", progress=100))
            return events
        finally:
            trace_logger.close()

    def close(self) -> None:
        pass


class FakeHostBackend:
    name = "Fake"

    def scan(self) -> list[UiDevice]:
        return [
            UiDevice(
                vendor="TSMaster",
                name="Fake TSMaster",
                serial="FAKE-TS-001",
                channels=[
                    UiChannel(
                        vendor="TSMaster",
                        device_name="Fake TSMaster",
                        channel_name="LIN 0",
                        bus="LIN",
                        channel_index=0,
                        mapping={
                            "app_channel": 0,
                            "hw_name": "FAKE-TS",
                            "hw_index": 0,
                            "hw_channel": 0,
                        },
                        capabilities=("lin_raw", "lin_diag", "e68_flash"),
                    )
                ],
            ),
            UiDevice(
                vendor="USB2XXX",
                name="Fake USB2XXX",
                serial="FAKE-USB2-001",
                channels=[
                    UiChannel(
                        vendor="USB2XXX",
                        device_name="Fake USB2XXX",
                        channel_name="LIN 0",
                        bus="LIN",
                        channel_index=0,
                        mapping={
                            "device_index": 0,
                            "channel_index": 0,
                        },
                        capabilities=("lin_raw", "lin_diag", "e68_flash"),
                    )
                ],
            ),
        ]

    def connect(self, channel: UiChannel, profile: ToolProfile) -> FakeHostSession:
        if channel.bus != "LIN":
            raise ValueError("fake backend only supports LIN in M1 Alpha")
        return FakeHostSession(profile)


def _lin_single(nad: int, payload: bytes) -> bytes:
    if len(payload) > 6:
        raise ValueError("fake LIN single-frame payload must be at most 6 bytes")
    return bytes([nad, len(payload)]) + payload + bytes([0xFF] * (6 - len(payload)))


def _manual_uds_response(payload: bytes) -> bytes:
    if payload == bytes.fromhex("10 01"):
        return bytes.fromhex("50 01")
    if payload == bytes.fromhex("10 03"):
        return bytes.fromhex("50 03")
    if payload == bytes.fromhex("27 01"):
        return bytes.fromhex("67 01 35 79 24 68")
    raise HostToolError(ErrorCategory.UDS, f"fake UDS request is not supported: {payload.hex(' ')}")
