from __future__ import annotations

from collections import deque
from collections.abc import Callable
from ctypes import (
    POINTER,
    Structure,
    WinDLL,
    byref,
    c_bool,
    c_char_p,
    c_float,
    c_int32,
    c_int64,
    c_uint8,
)
from dataclasses import dataclass
from time import monotonic, sleep

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.e68.crc32 import e68_crc32
from unified_can_lin_host_tool.e68.seedkey import calc_e68_fbl_key, calc_e68_level1_key
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.transport.base import LinFrame

APP_CAN = 0
APP_LIN = 1
TS_TCP_DEVICE = 1
XL_USB_DEVICE = 2
XL_VIRTUAL_SUBTYPE = 1
LIN_PROTOCOL_21 = 2


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


@dataclass(frozen=True)
class CanFrame:
    channel: int
    frame_id: int
    data: bytes
    properties: int


@dataclass(frozen=True)
class CanLoopbackCheck:
    tx_channel: int
    rx_channel: int
    frame_id: int
    data: bytes
    received: CanFrame | None

    @property
    def passed(self) -> bool:
        return self.received is not None and self.received.frame_id == self.frame_id and self.received.data == self.data


@dataclass(frozen=True)
class CanLoopbackResult:
    checks: tuple[CanLoopbackCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


class LinUdsRequestAssembler:
    def __init__(self, *, request_id: int, nad: int) -> None:
        self._request_id = request_id
        self._nad = nad
        self._pending_total: int | None = None
        self._pending = bytearray()
        self._next_sequence = 1

    def feed_frame(self, frame_id: int, data: bytes) -> bytes | None:
        if frame_id != self._request_id:
            return None
        if len(data) != 8:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS request frame must be 8 bytes")
        if data[0] != self._nad:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS request NAD mismatch")

        pci = data[1]
        frame_type = pci & 0xF0
        if frame_type == 0x00:
            payload_len = pci & 0x0F
            if payload_len == 0 or payload_len > 6:
                raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS single-frame request length is invalid")
            self._reset()
            return data[2 : 2 + payload_len]

        if frame_type == 0x10:
            total = ((pci & 0x0F) << 8) | data[2]
            if total <= 5:
                raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS first-frame request length is invalid")
            self._pending_total = total
            self._pending = bytearray(data[3:8])
            self._next_sequence = 1
            return self._complete_if_ready()

        if frame_type == 0x20:
            if self._pending_total is None:
                raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS consecutive frame without first frame")
            sequence = pci & 0x0F
            if sequence != self._next_sequence:
                raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS consecutive frame sequence mismatch")
            self._pending.extend(data[2:8])
            self._next_sequence = (self._next_sequence + 1) & 0x0F
            return self._complete_if_ready()

        raise HostToolError(ErrorCategory.TRANSPORT, "unsupported LIN UDS request frame type")

    def _complete_if_ready(self) -> bytes | None:
        if self._pending_total is None or len(self._pending) < self._pending_total:
            return None
        payload = bytes(self._pending[: self._pending_total])
        self._reset()
        return payload

    def _reset(self) -> None:
        self._pending_total = None
        self._pending.clear()
        self._next_sequence = 1


class E68FlashResponsePlan:
    def __init__(self, profile: ToolProfile, *, flash_driver_data: bytes, app_data: bytes) -> None:
        self._profile = profile
        self._flash_driver_data = flash_driver_data
        self._app_data = app_data
        self._app_seed = bytes.fromhex("35 79 24 68")
        self._boot_seed = bytes.fromhex("24 68 35 79")
        self._active_download = bytearray()

    def responses_for(self, uds_payload: bytes) -> list[bytes]:
        if uds_payload == bytes.fromhex("10 01"):
            return [bytes.fromhex("50 01")]
        if uds_payload == bytes.fromhex("10 03"):
            return [bytes.fromhex("50 03")]
        if uds_payload == bytes.fromhex("10 02"):
            return [bytes.fromhex("50 02")]
        if uds_payload == bytes.fromhex("27 01"):
            return [bytes.fromhex("67 01") + self._app_seed]
        if uds_payload.startswith(bytes.fromhex("27 02")):
            self._require_key("27 02", uds_payload[2:], calc_e68_level1_key(self._app_seed))
            return [bytes.fromhex("67 02")]
        if uds_payload == bytes.fromhex("31 01 02 03"):
            return [bytes.fromhex("71 01 02 03 00")]
        if uds_payload == bytes.fromhex("27 09"):
            return [bytes.fromhex("67 09") + self._boot_seed]
        if uds_payload.startswith(bytes.fromhex("27 0A")):
            self._require_key("27 0A", uds_payload[2:], calc_e68_fbl_key(self._boot_seed))
            return [bytes.fromhex("67 0A")]
        if uds_payload.startswith(bytes.fromhex("34")):
            self._active_download.clear()
            return [bytes.fromhex("74 20 00 06")]
        if uds_payload.startswith(bytes.fromhex("36")):
            self._active_download.extend(uds_payload[2:])
            return [bytes([0x76, uds_payload[1]])]
        if uds_payload.startswith(bytes.fromhex("37")):
            return [bytes([0x77]) + self._validated_download_crc(uds_payload[1:5])]
        if uds_payload == bytes.fromhex("31 01 02 02"):
            return [bytes.fromhex("71 01 02 02 00")]
        if uds_payload.startswith(bytes.fromhex("31 01 FF 00")):
            return [bytes.fromhex("7F 31 78"), bytes.fromhex("71 01 FF 00")]
        if uds_payload == bytes.fromhex("31 01 FF 01"):
            return [bytes.fromhex("71 01 FF 01 00")]
        if uds_payload == bytes.fromhex("11 01"):
            return [bytes.fromhex("51 01")]
        raise HostToolError(ErrorCategory.UDS, f"unexpected simulated E68 UDS request: {uds_payload.hex(' ')}")

    def _require_key(self, service: str, actual: bytes, expected: bytes) -> None:
        if actual != expected:
            raise HostToolError(ErrorCategory.UDS, f"simulated E68 security key mismatch for {service}")

    def _validated_download_crc(self, requested_crc: bytes) -> bytes:
        downloaded = bytes(self._active_download)
        if downloaded == self._flash_driver_data:
            expected_crc = e68_crc32(self._flash_driver_data).to_bytes(4, "big")
        elif downloaded == self._app_data:
            expected_crc = e68_crc32(self._app_data).to_bytes(4, "big")
        else:
            raise HostToolError(ErrorCategory.UDS, "simulated E68 transfer data mismatch")
        if requested_crc != expected_crc:
            raise HostToolError(ErrorCategory.UDS, "simulated E68 transfer CRC mismatch")
        self._active_download.clear()
        return expected_crc


class TsmasterLinFifoSimAdapter:
    def __init__(
        self,
        profile: ToolProfile,
        response_provider: Callable[[bytes], list[bytes]],
        *,
        dll_path: str = DEFAULT_TSMASTER_DLL,
        app_name: str = "Codex_TSMasterLinFifoSim",
        app_channel: int = 0,
        hw_name: str = "TS Virtual Device",
        hw_device_type: int = TS_TCP_DEVICE,
        hw_subtype: int = -1,
        hw_index: int = 0,
        hw_channel: int = 0,
    ) -> None:
        self._profile = profile
        self._response_provider = response_provider
        self._dll_path = dll_path
        self._app_name = app_name
        self._app_channel = app_channel
        self._hw_name = hw_name
        self._hw_device_type = hw_device_type
        self._hw_subtype = hw_subtype
        self._hw_index = hw_index
        self._hw_channel = hw_channel
        self._assembler = LinUdsRequestAssembler(request_id=profile.bus.request_id, nad=profile.bus.nad)
        self._rx_cache: deque[LinFrame] = deque()
        self._dll = _load_dll(dll_path)
        self._bind_lin()
        self._opened = False

    def open(self) -> None:
        app_name = self._app_name.encode("utf-8")
        self._must("initialize_lib_tsmaster", self._initialize_lib_tsmaster(app_name))
        try:
            self._must("tsapp_set_current_application", self._tsapp_set_current_application(app_name))
            self._must("tsapp_set_can_channel_count", self._tsapp_set_can_channel_count(0))
            self._must("tsapp_set_lin_channel_count", self._tsapp_set_lin_channel_count(1))
            self._must(
                "tsapp_set_mapping_verbose(LIN sim)",
                self._tsapp_set_mapping_verbose(
                    app_name,
                    APP_LIN,
                    self._app_channel,
                    self._hw_name.encode("utf-8"),
                    self._hw_device_type,
                    self._hw_subtype,
                    self._hw_index,
                    self._hw_channel,
                    True,
                ),
            )
            self._must(
                "tsapp_configure_baudrate_lin",
                self._tsapp_configure_baudrate_lin(self._app_channel, c_float(self._profile.bus.baudrate / 1000.0), LIN_PROTOCOL_21),
            )
            self._must("tsapp_connect", self._tsapp_connect())
            self._tsfifo_enable_receive_fifo()
            self._must("tsfifo_clear_lin_receive_buffers", self._tsfifo_clear_lin_receive_buffers(self._app_channel))
            self._opened = True
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
                try:
                    self._tsapp_disconnect()
                except Exception:
                    pass
        finally:
            self._opened = False
            self._finalize_lib_tsmaster()

    def send_lin_frame(self, frame_id: int, data: bytes) -> None:
        if len(data) != 8:
            raise HostToolError(ErrorCategory.TRANSPORT, "TSMaster LIN sim frame data must be 8 bytes")
        msg = _make_lin_msg(self._app_channel, frame_id, data, properties=0x01)
        self._must("tsapp_transmit_lin_async", self._tsapp_transmit_lin_async(byref(msg)))
        uds_payload = self._assembler.feed_frame(frame_id, data)
        if uds_payload is None:
            return
        for response_payload in self._response_provider(uds_payload):
            response = _lin_single_frame(self._profile.bus.nad, response_payload)
            rx_msg = _make_lin_msg(self._app_channel, self._profile.bus.response_id, response, properties=0x00)
            self._must("simulate_lin_async(RX)", self._simulate_lin_async(byref(rx_msg), 0))

    def receive_lin_frame(self, frame_id: int, timeout_ms: int) -> LinFrame | None:
        deadline = monotonic() + timeout_ms / 1000.0
        while monotonic() <= deadline:
            cached = self._pop_cached_lin_frame(frame_id)
            if cached is not None:
                return cached
            frames = self._receive_lin_frames(include_tx=False)
            for index, frame in enumerate(frames):
                if frame.frame_id == frame_id:
                    self._rx_cache.extend(frames[index + 1 :])
                    return frame
                self._rx_cache.append(frame)
            sleep(0.005)
        return None

    def _pop_cached_lin_frame(self, frame_id: int) -> LinFrame | None:
        for index, frame in enumerate(self._rx_cache):
            if frame.frame_id == frame_id:
                del self._rx_cache[index]
                return frame
        return None

    def _receive_lin_frames(self, *, include_tx: bool) -> list[LinFrame]:
        buffer = (TLIBLIN * 64)()
        size = c_int32(64)
        code = self._tsfifo_receive_lin_msgs(buffer, byref(size), self._app_channel, include_tx)
        self._must("tsfifo_receive_lin_msgs", code)
        return [
            LinFrame(
                frame_id=buffer[index].FIdentifier,
                data=bytes(buffer[index].FData[item] for item in range(buffer[index].FDLC)),
            )
            for index in range(size.value)
        ]

    def _bind_lin(self) -> None:
        self._bind_common()
        self._tsapp_set_can_channel_count = _bind(self._dll, "tsapp_set_can_channel_count", c_int32, [c_int32])
        self._tsapp_set_lin_channel_count = _bind(self._dll, "tsapp_set_lin_channel_count", c_int32, [c_int32])
        self._tsapp_set_mapping_verbose = _bind(
            self._dll,
            "tsapp_set_mapping_verbose",
            c_int32,
            [c_char_p, c_int32, c_int32, c_char_p, c_int32, c_int32, c_int32, c_int32, c_bool],
        )
        self._tsapp_configure_baudrate_lin = _bind(self._dll, "tsapp_configure_baudrate_lin", c_int32, [c_int32, c_float, c_int32])
        self._tsfifo_enable_receive_fifo = _bind(self._dll, "tsfifo_enable_receive_fifo", None, [])
        self._tsfifo_disable_receive_fifo = _bind(self._dll, "tsfifo_disable_receive_fifo", None, [])
        self._tsfifo_clear_lin_receive_buffers = _bind(self._dll, "tsfifo_clear_lin_receive_buffers", c_int32, [c_int32])
        self._tsfifo_receive_lin_msgs = _bind(self._dll, "tsfifo_receive_lin_msgs", c_int32, [POINTER(TLIBLIN), POINTER(c_int32), c_int32, c_bool])
        self._tsapp_transmit_lin_async = _bind(self._dll, "tsapp_transmit_lin_async", c_int32, [POINTER(TLIBLIN)])
        self._simulate_lin_async = _bind(self._dll, "simulate_lin_async", c_int32, [POINTER(TLIBLIN), c_uint8])

    def _bind_common(self) -> None:
        self._initialize_lib_tsmaster = _bind(self._dll, "initialize_lib_tsmaster", c_int32, [c_char_p])
        self._finalize_lib_tsmaster = _bind(self._dll, "finalize_lib_tsmaster", None, [])
        self._tsapp_get_error_description = _bind(self._dll, "tsapp_get_error_description", c_int32, [c_int32, POINTER(c_char_p)])
        self._tsapp_set_current_application = _bind(self._dll, "tsapp_set_current_application", c_int32, [c_char_p])
        self._tsapp_connect = _bind(self._dll, "tsapp_connect", c_int32, [])
        self._tsapp_disconnect = _bind(self._dll, "tsapp_disconnect", c_int32, [])

    def _must(self, label: str, code: int | None) -> None:
        if code not in (None, 0):
            raise HostToolError(ErrorCategory.DEVICE, f"{label} failed: {code} {self._error_text(code)}")

    def _error_text(self, code: int) -> str:
        if code == 0:
            return "OK"
        desc = c_char_p()
        ret = self._tsapp_get_error_description(c_int32(code), byref(desc))
        if ret == 0 and desc.value:
            return desc.value.decode("utf-8", errors="ignore")
        return f"ERR({code})"


def run_vector_can_virtual_loopback(
    *,
    dll_path: str = DEFAULT_TSMASTER_DLL,
    app_name: str = "Codex_TsmasterCanVirtualLoopback",
    baud_kbps: float = 500.0,
    timeout_ms: int = 500,
) -> CanLoopbackResult:
    dll = _load_dll(dll_path)
    session = _CanVirtualSession(dll)
    session.open(app_name=app_name, baud_kbps=baud_kbps)
    try:
        checks = [
            session.send_and_receive(0, 1, 0x123, bytes.fromhex("11 22 33 44"), timeout_ms),
            session.send_and_receive(1, 0, 0x321, bytes.fromhex("AA 55 01"), timeout_ms),
            session.send_and_receive(0, 1, 0x124, bytes.fromhex("10 20"), timeout_ms),
        ]
        return CanLoopbackResult(checks=tuple(checks))
    finally:
        session.close()


class _CanVirtualSession:
    def __init__(self, dll) -> None:
        self._dll = dll
        self._bind()
        self._opened = False

    def open(self, *, app_name: str, baud_kbps: float) -> None:
        app = app_name.encode("utf-8")
        self._must("initialize_lib_tsmaster", self._initialize_lib_tsmaster(app))
        try:
            self._must("tsapp_set_current_application", self._tsapp_set_current_application(app))
            self._must("tsapp_set_can_channel_count", self._tsapp_set_can_channel_count(2))
            self._must("tsapp_set_lin_channel_count", self._tsapp_set_lin_channel_count(0))
            for channel in (0, 1):
                self._must(
                    f"tsapp_set_mapping_verbose(CAN{channel})",
                    self._tsapp_set_mapping_verbose(
                        app,
                        APP_CAN,
                        channel,
                        b"VIRTUAL",
                        XL_USB_DEVICE,
                        XL_VIRTUAL_SUBTYPE,
                        0,
                        channel,
                        True,
                    ),
                )
                self._must("tsapp_configure_baudrate_can", self._tsapp_configure_baudrate_can(channel, c_float(baud_kbps), False, False))
            self._must("tsapp_connect", self._tsapp_connect())
            self._tsfifo_enable_receive_fifo()
            for channel in (0, 1):
                self._must("tsfifo_clear_can_receive_buffers", self._tsfifo_clear_can_receive_buffers(channel))
            self._opened = True
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
                try:
                    self._tsapp_disconnect()
                except Exception:
                    pass
        finally:
            self._opened = False
            self._finalize_lib_tsmaster()

    def send_and_receive(self, tx_channel: int, rx_channel: int, frame_id: int, data: bytes, timeout_ms: int) -> CanLoopbackCheck:
        msg = _make_can_msg(tx_channel, frame_id, data)
        self._must("tsapp_transmit_can_async", self._tsapp_transmit_can_async(byref(msg)))
        received = self._wait_for_can(rx_channel, frame_id, timeout_ms)
        return CanLoopbackCheck(tx_channel=tx_channel, rx_channel=rx_channel, frame_id=frame_id, data=data, received=received)

    def _wait_for_can(self, channel: int, frame_id: int, timeout_ms: int) -> CanFrame | None:
        deadline = monotonic() + timeout_ms / 1000.0
        while monotonic() <= deadline:
            buffer = (TLIBCAN * 64)()
            size = c_int32(64)
            self._must("tsfifo_receive_can_msgs", self._tsfifo_receive_can_msgs(buffer, byref(size), channel, False))
            for index in range(size.value):
                msg = buffer[index]
                data = bytes(msg.FData[item] for item in range(msg.FDLC))
                if msg.FIdentifier == frame_id:
                    return CanFrame(channel=msg.FIdxChn, frame_id=msg.FIdentifier, data=data, properties=msg.FProperties)
            sleep(0.01)
        return None

    def _bind(self) -> None:
        self._initialize_lib_tsmaster = _bind(self._dll, "initialize_lib_tsmaster", c_int32, [c_char_p])
        self._finalize_lib_tsmaster = _bind(self._dll, "finalize_lib_tsmaster", None, [])
        self._tsapp_get_error_description = _bind(self._dll, "tsapp_get_error_description", c_int32, [c_int32, POINTER(c_char_p)])
        self._tsapp_set_current_application = _bind(self._dll, "tsapp_set_current_application", c_int32, [c_char_p])
        self._tsapp_set_can_channel_count = _bind(self._dll, "tsapp_set_can_channel_count", c_int32, [c_int32])
        self._tsapp_set_lin_channel_count = _bind(self._dll, "tsapp_set_lin_channel_count", c_int32, [c_int32])
        self._tsapp_set_mapping_verbose = _bind(
            self._dll,
            "tsapp_set_mapping_verbose",
            c_int32,
            [c_char_p, c_int32, c_int32, c_char_p, c_int32, c_int32, c_int32, c_int32, c_bool],
        )
        self._tsapp_configure_baudrate_can = _bind(self._dll, "tsapp_configure_baudrate_can", c_int32, [c_int32, c_float, c_bool, c_bool])
        self._tsapp_connect = _bind(self._dll, "tsapp_connect", c_int32, [])
        self._tsapp_disconnect = _bind(self._dll, "tsapp_disconnect", c_int32, [])
        self._tsfifo_enable_receive_fifo = _bind(self._dll, "tsfifo_enable_receive_fifo", None, [])
        self._tsfifo_disable_receive_fifo = _bind(self._dll, "tsfifo_disable_receive_fifo", None, [])
        self._tsfifo_clear_can_receive_buffers = _bind(self._dll, "tsfifo_clear_can_receive_buffers", c_int32, [c_int32])
        self._tsfifo_receive_can_msgs = _bind(self._dll, "tsfifo_receive_can_msgs", c_int32, [POINTER(TLIBCAN), POINTER(c_int32), c_int32, c_bool])
        self._tsapp_transmit_can_async = _bind(self._dll, "tsapp_transmit_can_async", c_int32, [POINTER(TLIBCAN)])

    def _must(self, label: str, code: int | None) -> None:
        if code not in (None, 0):
            raise HostToolError(ErrorCategory.DEVICE, f"{label} failed: {code} {self._error_text(code)}")

    def _error_text(self, code: int) -> str:
        if code == 0:
            return "OK"
        desc = c_char_p()
        ret = self._tsapp_get_error_description(c_int32(code), byref(desc))
        if ret == 0 and desc.value:
            return desc.value.decode("utf-8", errors="ignore")
        return f"ERR({code})"


def _bind(dll, name: str, restype, argtypes: list) -> object:
    func = getattr(dll, name)
    func.restype = restype
    func.argtypes = argtypes
    return func


def _load_dll(dll_path: str):
    try:
        return WinDLL(dll_path)
    except OSError as exc:
        raise HostToolError(ErrorCategory.DEVICE, f"load TSMaster DLL failed: {dll_path}") from exc


def _make_can_msg(channel: int, frame_id: int, data: bytes) -> TLIBCAN:
    if len(data) > 8:
        raise HostToolError(ErrorCategory.TRANSPORT, "classic CAN frame supports at most 8 data bytes")
    msg = TLIBCAN()
    msg.FIdxChn = channel
    msg.FProperties = 0x01
    msg.FDLC = len(data)
    msg.FIdentifier = frame_id
    for index, value in enumerate(data):
        msg.FData[index] = value
    return msg


def _make_lin_msg(channel: int, frame_id: int, data: bytes, *, properties: int) -> TLIBLIN:
    msg = TLIBLIN()
    msg.FIdxChn = channel
    msg.FProperties = properties
    msg.FDLC = len(data)
    msg.FIdentifier = frame_id
    for index, value in enumerate(data):
        msg.FData[index] = value
    return msg


def _lin_single_frame(nad: int, payload: bytes) -> bytes:
    if len(payload) > 6:
        raise HostToolError(ErrorCategory.TRANSPORT, "simulated LIN response payload must be at most 6 bytes")
    return bytes([nad, len(payload)]) + payload + bytes([0xFF] * (6 - len(payload)))
