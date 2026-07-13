from __future__ import annotations

import os
from ctypes import (
    POINTER,
    Structure,
    WinDLL,
    byref,
    c_bool,
    c_char,
    c_char_p,
    c_float,
    c_int32,
    c_int64,
    c_uint8,
)
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.transport.base import CanFrame, LinFrame

def _default_tsmaster_dll() -> str:
    configured = os.environ.get("TSMASTER_DLL")
    if configured:
        return configured
    candidates = (
        Path(r"D:\software\TSMaster\bin64\TSMaster.dll"),
        Path(r"C:\Program Files\TOSUN\TSMaster\bin64\TSMaster.dll"),
        Path(r"C:\Program Files\TSMaster\bin64\TSMaster.dll"),
    )
    return str(next((item for item in candidates if item.is_file()), Path("TSMaster.dll")))


DEFAULT_TSMASTER_DLL = _default_tsmaster_dll()
APP_CAN = 0
APP_LIN = 1
TS_USB_DEVICE = 3
LIN_MASTER = 0
LIN_PROTOCOL_21 = 2
CAN_CLASSIC_DLC = 8


class TLIBHWInfo(Structure):
    _pack_ = 1
    _fields_ = [
        ("FDeviceType", c_int32),
        ("FDeviceIndex", c_int32),
        ("FVendorName", c_char * 32),
        ("FDeviceName", c_char * 32),
        ("FSerialString", c_char * 64),
    ]


class TLIBLIN(Structure):
    _pack_ = 1
    _fields_ = [
        ("FIdxChn", c_uint8),
        ("FErrStatus", c_uint8),
        ("FProperties", c_uint8),
        ("FDLC", c_uint8),
        ("FIdentifier", c_uint8),
        ("FChecksum", c_uint8),
        ("FStatus", c_uint8),
        ("FTimeUs", c_int64),
        ("FData", c_uint8 * 8),
    ]


class TLIBCAN(Structure):
    _pack_ = 1
    _fields_ = [
        ("FIdxChn", c_uint8),
        ("FProperties", c_uint8),
        ("FDLC", c_uint8),
        ("FReserved", c_uint8),
        ("FIdentifier", c_int32),
        ("FTimeUs", c_int64),
        ("FData", c_uint8 * 8),
    ]


@dataclass(frozen=True)
class TsmasterDevice:
    index: int
    vendor: str
    name: str
    serial: str
    device_type: int
    device_index: int


class TsmasterAdapter:
    def __init__(
        self,
        *,
        dll_path: str = DEFAULT_TSMASTER_DLL,
        app_name: str = "Codex_UnifiedHostTool",
        project_dir: str | Path | None = None,
        app_channel: int = 0,
        hw_name: str = "TC1016",
        hw_subtype: int = 11,
        hw_index: int = 0,
        hw_channel: int = 0,
        can_channel_count: int | None = None,
        base_hw_channel: int | None = None,
        baud_kbps: float = 19.2,
    ) -> None:
        self.dll_path = dll_path
        self.app_name = app_name
        self.project_dir = Path(project_dir) if project_dir is not None else None
        self.app_channel = app_channel
        self.hw_name = hw_name
        self.hw_subtype = hw_subtype
        self.hw_index = hw_index
        self.hw_channel = hw_channel
        self.can_channel_count = can_channel_count
        self.base_hw_channel = base_hw_channel
        self.baud_kbps = baud_kbps
        try:
            self._dll = WinDLL(dll_path)
        except OSError as exc:
            raise HostToolError(ErrorCategory.DEVICE, f"load TSMaster DLL failed: {dll_path}") from exc
        self._bind()
        self._opened = False
        self._opened_bus: str | None = None

    @classmethod
    def probe(
        cls,
        *,
        dll_path: str = DEFAULT_TSMASTER_DLL,
        app_name: str = "Codex_UnifiedHostTool_Probe",
    ) -> list[TsmasterDevice]:
        adapter = cls(dll_path=dll_path, app_name=app_name)
        adapter._initialize()
        try:
            count = c_int32(0)
            adapter._must(
                "tsapp_enumerate_hw_devices",
                adapter._tsapp_enumerate_hw_devices(byref(count)),
            )
            devices: list[TsmasterDevice] = []
            for index in range(count.value):
                info = TLIBHWInfo()
                adapter._must(
                    f"tsapp_get_hw_info_by_index({index})",
                    adapter._tsapp_get_hw_info_by_index(c_int32(index), byref(info)),
                )
                devices.append(
                    TsmasterDevice(
                        index=index,
                        vendor=_clean_char_array(info.FVendorName),
                        name=_clean_char_array(info.FDeviceName),
                        serial=_clean_char_array(info.FSerialString),
                        device_type=info.FDeviceType,
                        device_index=info.FDeviceIndex,
                    )
                )
            return devices
        finally:
            adapter._finalize()

    def open_lin(self) -> None:
        app_name = self.app_name.encode("utf-8")
        self._initialize()
        try:
            self._must("tsapp_set_can_channel_count", self._tsapp_set_can_channel_count(0))
            self._must("tsapp_set_lin_channel_count", self._tsapp_set_lin_channel_count(1))
            self._must(
                "tsapp_set_mapping_verbose(LIN)",
                self._tsapp_set_mapping_verbose(
                    app_name,
                    APP_LIN,
                    self.app_channel,
                    self.hw_name.encode("utf-8"),
                    TS_USB_DEVICE,
                    self.hw_subtype,
                    self.hw_index,
                    self.hw_channel,
                    True,
                ),
            )
            self._must(
                "tsapp_configure_baudrate_lin",
                self._tsapp_configure_baudrate_lin(self.app_channel, c_float(self.baud_kbps), LIN_PROTOCOL_21),
            )
            self._stop_lin_schedule()
            self._must("tsapp_connect", self._tsapp_connect())
            self._stop_lin_schedule()
            self._must("tslin_set_node_functiontype", self._tslin_set_node_functiontype(self.app_channel, LIN_MASTER))
            self._must("tslin_start_lin_channel", self._tslin_start_lin_channel(self.app_channel))
            self._stop_lin_schedule()
            self._tsfifo_enable_receive_fifo()
            self._ignore_optional_error(
                "tsfifo_clear_lin_receive_buffers",
                self._tsfifo_clear_lin_receive_buffers(self.app_channel),
            )
            self._opened = True
            self._opened_bus = "LIN"
        except Exception:
            self.close()
            raise

    def open_can(self) -> None:
        app_name = self.app_name.encode("utf-8")
        can_channel_count = self.can_channel_count
        base_hw_channel = self.base_hw_channel

        if can_channel_count is None:
            can_channel_count = max(1, self.app_channel + 1)
        if can_channel_count <= self.app_channel:
            raise HostToolError(ErrorCategory.DEVICE, "CAN channel count must cover the selected app channel")
        if base_hw_channel is None:
            base_hw_channel = self.hw_channel - self.app_channel

        self._initialize()
        try:
            self._must("tsapp_set_can_channel_count", self._tsapp_set_can_channel_count(can_channel_count))
            self._must("tsapp_set_lin_channel_count", self._tsapp_set_lin_channel_count(0))
            for current_app_channel in range(can_channel_count):
                current_hw_channel = base_hw_channel + current_app_channel
                self._must(
                    f"tsapp_set_mapping_verbose(CAN{current_app_channel})",
                    self._tsapp_set_mapping_verbose(
                        app_name,
                        APP_CAN,
                        current_app_channel,
                        self.hw_name.encode("utf-8"),
                        TS_USB_DEVICE,
                        self.hw_subtype,
                        self.hw_index,
                        current_hw_channel,
                        True,
                    ),
                )
                self._must(
                    f"tsapp_configure_baudrate_can({current_app_channel})",
                    self._tsapp_configure_baudrate_can(current_app_channel, c_float(self.baud_kbps), False, False),
                )
            self._must("tsapp_connect", self._tsapp_connect())
            self._tsfifo_enable_receive_fifo()
            self._opened = True
            self._opened_bus = "CAN"
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            if self._opened:
                try:
                    self._tsfifo_disable_receive_fifo()
                except Exception:
                    pass
                if self._opened_bus == "LIN":
                    try:
                        self._tslin_stop_lin_channel(self.app_channel)
                    except Exception:
                        pass
                try:
                    self._tsapp_disconnect()
                except Exception:
                    pass
        finally:
            self._opened = False
            self._opened_bus = None
            self._finalize()

    def send_lin_frame(self, frame_id: int, data: bytes) -> None:
        if len(data) != 8:
            raise HostToolError(ErrorCategory.TRANSPORT, "TSMaster LIN frame data must be 8 bytes")
        msg = TLIBLIN()
        msg.FIdxChn = self.app_channel
        msg.FProperties = 0x01
        msg.FDLC = 8
        msg.FIdentifier = frame_id
        for index, value in enumerate(data):
            msg.FData[index] = value
        self._must("tsapp_transmit_lin_async", self._tsapp_transmit_lin_async(byref(msg)))

    def receive_lin_frame(self, frame_id: int, timeout_ms: int) -> LinFrame | None:
        msg = TLIBLIN()
        code = self._tsapp_transmit_header_and_receive_msg(
            self.app_channel,
            c_uint8(frame_id),
            c_uint8(8),
            byref(msg),
            timeout_ms,
        )
        if code != 0 or msg.FDLC == 0:
            return None
        return LinFrame(frame_id=msg.FIdentifier, data=bytes(msg.FData[index] for index in range(msg.FDLC)))

    def send_can_frame(self, can_id: int, data: bytes) -> None:
        if len(data) != CAN_CLASSIC_DLC:
            raise HostToolError(ErrorCategory.TRANSPORT, "TSMaster CAN frame data must be 8 bytes")
        msg = TLIBCAN()
        msg.FIdxChn = self.app_channel
        msg.FProperties = 0
        msg.FDLC = CAN_CLASSIC_DLC
        msg.FIdentifier = can_id
        for index, value in enumerate(data):
            msg.FData[index] = value
        self._must("tsapp_transmit_can_sync", self._tsapp_transmit_can_sync(byref(msg), 1000))

    def receive_can_frame(self, can_id: int, timeout_ms: int) -> CanFrame | None:
        deadline = monotonic() + timeout_ms / 1000.0

        while monotonic() <= deadline:
            count = c_int32(0)
            self._must(
                "tsfifo_read_can_rx_buffer_frame_count",
                self._tsfifo_read_can_rx_buffer_frame_count(self.app_channel, byref(count)),
            )
            if count.value > 0:
                size = c_int32(count.value)
                buffer = (TLIBCAN * size.value)()
                self._must(
                    "tsfifo_receive_can_msgs",
                    self._tsfifo_receive_can_msgs(buffer, byref(size), self.app_channel, False),
                )
                for index in range(size.value):
                    msg = buffer[index]
                    if int(msg.FIdentifier) == can_id:
                        data = bytes(msg.FData[item] for item in range(msg.FDLC))
                        return CanFrame(can_id=int(msg.FIdentifier), data=data,
                                        timestamp_us=int(msg.FTimeUs))
            sleep(0.001)

        return None

    def _bind(self) -> None:
        self._initialize_lib_tsmaster = self._dll.initialize_lib_tsmaster
        self._initialize_lib_tsmaster.restype = c_int32
        self._initialize_lib_tsmaster.argtypes = [c_char_p]

        self._initialize_lib_tsmaster_with_project = self._dll.initialize_lib_tsmaster_with_project
        self._initialize_lib_tsmaster_with_project.restype = c_int32
        self._initialize_lib_tsmaster_with_project.argtypes = [c_char_p, c_char_p]

        self._finalize_lib_tsmaster = self._dll.finalize_lib_tsmaster
        self._finalize_lib_tsmaster.restype = None
        self._finalize_lib_tsmaster.argtypes = []

        self._tsapp_get_error_description = self._dll.tsapp_get_error_description
        self._tsapp_get_error_description.restype = c_int32
        self._tsapp_get_error_description.argtypes = [c_int32, POINTER(c_char_p)]

        self._tsapp_enumerate_hw_devices = self._dll.tsapp_enumerate_hw_devices
        self._tsapp_enumerate_hw_devices.restype = c_int32
        self._tsapp_enumerate_hw_devices.argtypes = [POINTER(c_int32)]

        self._tsapp_get_hw_info_by_index = self._dll.tsapp_get_hw_info_by_index
        self._tsapp_get_hw_info_by_index.restype = c_int32
        self._tsapp_get_hw_info_by_index.argtypes = [c_int32, POINTER(TLIBHWInfo)]

        self._tsapp_set_current_application = self._dll.tsapp_set_current_application
        self._tsapp_set_current_application.restype = c_int32
        self._tsapp_set_current_application.argtypes = [c_char_p]

        self._tsapp_set_can_channel_count = self._dll.tsapp_set_can_channel_count
        self._tsapp_set_can_channel_count.restype = c_int32
        self._tsapp_set_can_channel_count.argtypes = [c_int32]

        self._tsapp_set_lin_channel_count = self._dll.tsapp_set_lin_channel_count
        self._tsapp_set_lin_channel_count.restype = c_int32
        self._tsapp_set_lin_channel_count.argtypes = [c_int32]

        self._tsapp_set_mapping_verbose = self._dll.tsapp_set_mapping_verbose
        self._tsapp_set_mapping_verbose.restype = c_int32
        self._tsapp_set_mapping_verbose.argtypes = [
            c_char_p,
            c_int32,
            c_int32,
            c_char_p,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_bool,
        ]

        self._tsapp_configure_baudrate_lin = self._dll.tsapp_configure_baudrate_lin
        self._tsapp_configure_baudrate_lin.restype = c_int32
        self._tsapp_configure_baudrate_lin.argtypes = [c_int32, c_float, c_int32]

        self._tsapp_configure_baudrate_can = self._dll.tsapp_configure_baudrate_can
        self._tsapp_configure_baudrate_can.restype = c_int32
        self._tsapp_configure_baudrate_can.argtypes = [c_int32, c_float, c_bool, c_bool]

        self._tsapp_connect = self._dll.tsapp_connect
        self._tsapp_connect.restype = c_int32
        self._tsapp_connect.argtypes = []

        self._tsapp_disconnect = self._dll.tsapp_disconnect
        self._tsapp_disconnect.restype = c_int32
        self._tsapp_disconnect.argtypes = []

        self._tsfifo_enable_receive_fifo = self._dll.tsfifo_enable_receive_fifo
        self._tsfifo_enable_receive_fifo.restype = None
        self._tsfifo_enable_receive_fifo.argtypes = []

        self._tsfifo_disable_receive_fifo = self._dll.tsfifo_disable_receive_fifo
        self._tsfifo_disable_receive_fifo.restype = None
        self._tsfifo_disable_receive_fifo.argtypes = []

        self._tsfifo_clear_lin_receive_buffers = self._dll.tsfifo_clear_lin_receive_buffers
        self._tsfifo_clear_lin_receive_buffers.restype = c_int32
        self._tsfifo_clear_lin_receive_buffers.argtypes = [c_int32]

        self._tslin_set_node_functiontype = self._dll.tslin_set_node_functiontype
        self._tslin_set_node_functiontype.restype = c_int32
        self._tslin_set_node_functiontype.argtypes = [c_int32, c_int32]

        self._tslin_start_lin_channel = self._dll.tslin_start_lin_channel
        self._tslin_start_lin_channel.restype = c_int32
        self._tslin_start_lin_channel.argtypes = [c_int32]

        self._tslin_stop_lin_channel = self._dll.tslin_stop_lin_channel
        self._tslin_stop_lin_channel.restype = c_int32
        self._tslin_stop_lin_channel.argtypes = [c_int32]

        self._tsapp_transmit_lin_async = self._dll.tsapp_transmit_lin_async
        self._tsapp_transmit_lin_async.restype = c_int32
        self._tsapp_transmit_lin_async.argtypes = [POINTER(TLIBLIN)]

        self._tsapp_transmit_can_sync = self._dll.tsapp_transmit_can_sync
        self._tsapp_transmit_can_sync.restype = c_int32
        self._tsapp_transmit_can_sync.argtypes = [POINTER(TLIBCAN), c_int32]

        self._tsapp_transmit_header_and_receive_msg = self._dll.tsapp_transmit_header_and_receive_msg
        self._tsapp_transmit_header_and_receive_msg.restype = c_int32
        self._tsapp_transmit_header_and_receive_msg.argtypes = [
            c_int32,
            c_uint8,
            c_uint8,
            POINTER(TLIBLIN),
            c_int32,
        ]

        self._tsfifo_read_can_rx_buffer_frame_count = self._dll.tsfifo_read_can_rx_buffer_frame_count
        self._tsfifo_read_can_rx_buffer_frame_count.restype = c_int32
        self._tsfifo_read_can_rx_buffer_frame_count.argtypes = [c_int32, POINTER(c_int32)]

        self._tsfifo_receive_can_msgs = self._dll.tsfifo_receive_can_msgs
        self._tsfifo_receive_can_msgs.restype = c_int32
        self._tsfifo_receive_can_msgs.argtypes = [POINTER(TLIBCAN), POINTER(c_int32), c_int32, c_bool]

        self._tslin_switch_idle_schedule_table = self._bind_optional(
            "tslin_switch_idle_schedule_table",
            c_int32,
            [c_int32],
        )
        self._tslin_clear_schedule_tables = self._bind_optional(
            "tslin_clear_schedule_tables",
            c_int32,
            [c_int32],
        )

    def _initialize(self) -> None:
        app_name = self.app_name.encode("utf-8")
        if self.project_dir is None:
            self._must("initialize_lib_tsmaster", self._initialize_lib_tsmaster(app_name))
        else:
            self._must(
                "initialize_lib_tsmaster_with_project",
                self._initialize_lib_tsmaster_with_project(app_name, str(self.project_dir).encode("utf-8")),
            )
        self._must("tsapp_set_current_application", self._tsapp_set_current_application(app_name))

    def _finalize(self) -> None:
        self._finalize_lib_tsmaster()

    def _bind_optional(self, name, restype, argtypes):
        try:
            func = getattr(self._dll, name)
        except AttributeError:
            return None
        func.restype = restype
        func.argtypes = argtypes
        return func

    def _stop_lin_schedule(self) -> None:
        if self._tslin_switch_idle_schedule_table is not None:
            self._ignore_optional_error(
                "tslin_switch_idle_schedule_table",
                self._tslin_switch_idle_schedule_table(self.app_channel),
            )
        if self._tslin_clear_schedule_tables is not None:
            self._ignore_optional_error(
                "tslin_clear_schedule_tables",
                self._tslin_clear_schedule_tables(self.app_channel),
            )

    def _ignore_optional_error(self, label: str, code: int) -> None:
        _ = (label, code)

    def _must(self, label: str, code: int) -> None:
        if code != 0:
            raise HostToolError(ErrorCategory.DEVICE, f"{label} failed: {code} {self._error_text(code)}")

    def _error_text(self, code: int) -> str:
        if code == 0:
            return "OK"
        desc = c_char_p()
        ret = self._tsapp_get_error_description(c_int32(code), byref(desc))
        if ret == 0 and desc.value:
            return desc.value.decode("utf-8", errors="ignore")
        return f"ERR({code})"


def _clean_char_array(value) -> str:
    return bytes(value).split(b"\x00", 1)[0].decode("utf-8", errors="ignore")
