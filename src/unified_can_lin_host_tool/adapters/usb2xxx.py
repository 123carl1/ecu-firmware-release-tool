"""图莫斯 USB2XXX 官方 SDK 的经典 CAN 适配器。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from ctypes import Structure, WinDLL, byref, c_char, c_ubyte, c_uint
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from time import monotonic, sleep

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.transport.base import CanFrame


CAN_CLASSIC_DLC = 8
CAN_MODE_NORMAL_WITH_RESISTOR = 0x80
MAX_USB2XXX_DEVICES = 20


class DEVICE_INFO(Structure):
    _fields_ = [
        ("FirmwareName", c_char * 32),
        ("BuildDate", c_char * 32),
        ("HardwareVersion", c_uint),
        ("FirmwareVersion", c_uint),
        ("SerialNumber", c_uint * 3),
        ("Functions", c_uint),
    ]


class HARDWARE_INFO(Structure):
    _fields_ = [
        ("McuModel", c_char * 16),
        ("ProductModel", c_char * 16),
        ("Version", c_uint),
        ("CANChannelNum", c_char),
        ("LINChannelNum", c_char),
        ("PWMChannelNum", c_char),
        ("HaveCANFD", c_char),
        ("DIChannelNum", c_char),
        ("DOChannelNum", c_char),
        ("HaveIsolation", c_char),
        ("ExPowerSupply", c_char),
        ("IsOEM", c_char),
        ("EECapacity", c_char),
        ("SPIFlashCapacity", c_char),
        ("TFCardSupport", c_char),
        ("ProductDate", c_char * 12),
        ("USBControl", c_char),
        ("SerialControl", c_char),
        ("EthControl", c_char),
        ("VbatChannel", c_char),
    ]


class CAN_MSG(Structure):
    _fields_ = [
        ("ID", c_uint),
        ("TimeStamp", c_uint),
        ("RemoteFlag", c_ubyte),
        ("ExternFlag", c_ubyte),
        ("DataLen", c_ubyte),
        ("Data", c_ubyte * CAN_CLASSIC_DLC),
        ("TimeStampHigh", c_ubyte),
    ]


class CAN_INIT_CONFIG(Structure):
    _fields_ = [
        ("CAN_BRP", c_uint),
        ("CAN_SJW", c_ubyte),
        ("CAN_BS1", c_ubyte),
        ("CAN_BS2", c_ubyte),
        ("CAN_Mode", c_ubyte),
        ("CAN_ABOM", c_ubyte),
        ("CAN_NART", c_ubyte),
        ("CAN_RFLM", c_ubyte),
        ("CAN_TXFP", c_ubyte),
    ]


def _default_usb2xxx_dll() -> str:
    candidates: list[Path] = []
    configured = os.environ.get("USB2XXX_DLL")
    if configured:
        candidates.append(Path(configured))
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "USB2XXX.dll")
    candidates.extend([
        Path(sys.executable).resolve().parent / "USB2XXX.dll",
        Path(r"D:\software\USB2XXX\USB2XXX.dll"),
    ])
    return str(next((path for path in candidates if path.is_file()), candidates[0]))


DEFAULT_USB2XXX_DLL = _default_usb2xxx_dll()


def _byte_value(value: bytes | int) -> int:
    if isinstance(value, bytes):
        return value[0] if value else 0
    return int(value)


def _text(value) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("ascii", errors="replace")


class _Usb2xxxApi:
    def __init__(self, dll_path: str) -> None:
        path = Path(dll_path)
        if not path.is_file():
            raise HostToolError(ErrorCategory.DEVICE, f"找不到图莫斯 USB2XXX.dll：{path}")
        libusb = path.with_name("libusb-1.0.dll")
        if libusb.is_file():
            WinDLL(str(libusb))
        self.dll = WinDLL(str(path))

    def scan_handles(self) -> list[int]:
        handles = (c_uint * MAX_USB2XXX_DEVICES)()
        count = int(self.dll.USB_ScanDevice(byref(handles)))
        if count < 0:
            raise HostToolError(ErrorCategory.DEVICE, f"USB2XXX 设备扫描失败：{count}")
        return [int(handles[index]) for index in range(min(count, MAX_USB2XXX_DEVICES))]

    def open_device(self, handle: int) -> int:
        return int(self.dll.USB_OpenDevice(handle))

    def close_device(self, handle: int) -> int:
        return int(self.dll.USB_CloseDevice(handle))

    def read_device(self, handle: int) -> dict:
        device = DEVICE_INFO()
        functions = (c_char * 256)()
        if int(self.dll.DEV_GetDeviceInfo(handle, byref(device), byref(functions))) != 1:
            raise HostToolError(ErrorCategory.DEVICE, "读取图莫斯设备信息失败")
        hardware = HARDWARE_INFO()
        if int(self.dll.DEV_GetHardwareInfo(handle, byref(hardware))) != 1:
            raise HostToolError(ErrorCategory.DEVICE, "读取图莫斯硬件信息失败")
        return {
            "serial": "".join(f"{int(part):08X}" for part in device.SerialNumber),
            "product": _text(hardware.ProductModel) or "USB2XXX",
            "firmware": _text(device.FirmwareName),
            "can_channel_count": _byte_value(hardware.CANChannelNum),
            "is_can_fd": bool(_byte_value(hardware.HaveCANFD)),
        }

    def initialize_can(
        self,
        handle: int,
        channel: int,
        baudrate: int,
        receive_ids: tuple[int, ...],
    ) -> None:
        config = CAN_INIT_CONFIG()
        result = int(self.dll.CAN_GetCANSpeedArg(handle, byref(config), baudrate))
        if result != 0:
            raise HostToolError(ErrorCategory.DEVICE, f"图莫斯 CAN 波特率配置失败：{result}")
        config.CAN_Mode = CAN_MODE_NORMAL_WITH_RESISTOR
        result = int(self.dll.CAN_Init(handle, channel, byref(config)))
        if result != 0:
            raise HostToolError(ErrorCategory.DEVICE, f"图莫斯 CAN{channel + 1} 初始化失败：{result}")
        if receive_ids:
            identifiers = (c_uint * len(receive_ids))(*receive_ids)
            result = int(self.dll.CAN_FilterList_Init(
                handle, channel, identifiers, len(receive_ids)
            ))
            if result != 0:
                raise HostToolError(
                    ErrorCategory.DEVICE,
                    f"图莫斯 CAN{channel + 1} 接收过滤配置失败：{result}",
                )
        result = int(self.dll.CAN_StartGetMsg(handle, channel))
        if result != 0:
            raise HostToolError(ErrorCategory.DEVICE, f"图莫斯 CAN{channel + 1} 接收启动失败：{result}")
        self.clear_can(handle, channel)

    def clear_can(self, handle: int, channel: int) -> None:
        result = int(self.dll.CAN_ClearMsg(handle, channel))
        if result != 0:
            raise HostToolError(ErrorCategory.DEVICE, f"图莫斯 CAN{channel + 1} 接收清理失败：{result}")

    def send_can(self, handle: int, channel: int, can_id: int, payload: bytes) -> int:
        message = CAN_MSG()
        message.ID = can_id
        message.DataLen = len(payload)
        for index, value in enumerate(payload):
            message.Data[index] = value
        # CAN_SendMsg 会在发送期间独占 USB，总线端快速返回的 ISO-TP FC 可能因此丢失。
        # 新版 SDK 的同步接口不会独占 USB；旧版 DLL 不具备该符号时才回退。
        send = getattr(self.dll, "CAN_SendMsgSynch", None)
        if send is None:
            send = self.dll.CAN_SendMsg
        return int(send(handle, channel, byref(message), 1))

    def receive_can(self, handle: int, channel: int) -> list[dict]:
        messages = (CAN_MSG * 4096)()
        get_with_size = getattr(self.dll, "CAN_GetMsgWithSize", None)
        if get_with_size is None:
            count = int(self.dll.CAN_GetMsg(handle, channel, messages))
        else:
            count = int(get_with_size(handle, channel, messages, len(messages)))
        if count < 0:
            raise HostToolError(ErrorCategory.TRANSPORT, f"图莫斯 CAN 接收失败：{count}")
        return [
            {
                "id": int(message.ID),
                "data": bytes(message.Data[index] for index in range(message.DataLen)),
                "timestamp_us": int(message.TimeStamp) | (int(message.TimeStampHigh) << 32),
            }
            for message in messages[:count]
        ]


@dataclass(frozen=True)
class Usb2xxxCanDevice:
    device_name: str
    product: str
    serial: str
    manufacturer: str
    device_index: int
    can_channel_count: int
    is_can_fd: bool


ApiFactory = Callable[[str], object]


class Usb2xxxAdapter:
    def __init__(
        self,
        *,
        dll_path: str = DEFAULT_USB2XXX_DLL,
        device_serial: str,
        channel: int,
        baudrate: int,
        device_index: int = 0,
        receive_ids: tuple[int, ...] = (),
        api_factory: ApiFactory = _Usb2xxxApi,
    ) -> None:
        self.dll_path = dll_path
        self.device_serial = device_serial
        self.device_index = device_index
        self.channel = channel
        self.baudrate = baudrate
        self.receive_ids = receive_ids
        self._api_factory = api_factory
        self._api = None
        self._handle: int | None = None
        self._pending_frames: deque[dict] = deque()

    @classmethod
    def probe_can_devices(
        cls,
        *,
        dll_path: str = DEFAULT_USB2XXX_DLL,
        api_factory: ApiFactory = _Usb2xxxApi,
    ) -> list[Usb2xxxCanDevice]:
        api = api_factory(dll_path)
        devices: list[Usb2xxxCanDevice] = []
        for index, handle in enumerate(api.scan_handles()):
            if api.open_device(handle) != 1:
                continue
            try:
                info = api.read_device(handle)
                channel_count = int(info["can_channel_count"])
                if channel_count <= 0:
                    continue
                devices.append(Usb2xxxCanDevice(
                    device_name=f"图莫斯 {info['product']}",
                    product=str(info["product"]),
                    serial=str(info["serial"]),
                    manufacturer="TOOMOSS",
                    device_index=index,
                    can_channel_count=channel_count,
                    is_can_fd=bool(info["is_can_fd"]),
                ))
            finally:
                api.close_device(handle)
        return devices

    def open_can(self) -> None:
        if self._handle is not None:
            return
        api = self._api_factory(self.dll_path)
        handles = api.scan_handles()
        for index, handle in enumerate(handles):
            if api.open_device(handle) != 1:
                continue
            try:
                info = api.read_device(handle)
                if str(info["serial"]) != self.device_serial:
                    api.close_device(handle)
                    continue
                channel_count = int(info["can_channel_count"])
                if not 0 <= self.channel < channel_count:
                    raise HostToolError(
                        ErrorCategory.DEVICE,
                        f"图莫斯设备仅有 {channel_count} 个 CAN 通道，不能选择 CAN{self.channel + 1}",
                    )
                api.initialize_can(
                    handle,
                    self.channel,
                    self.baudrate,
                    self.receive_ids,
                )
                self.device_index = index
                self._api = api
                self._handle = handle
                return
            except Exception:
                api.close_device(handle)
                raise
        raise HostToolError(ErrorCategory.DEVICE, f"所选图莫斯设备已离线：SN {self.device_serial}")

    def close(self) -> None:
        if self._api is not None and self._handle is not None:
            try:
                self._api.close_device(self._handle)
            finally:
                self._handle = None
                self._api = None

    def send_can_frame(self, can_id: int, data: bytes) -> None:
        if len(data) != CAN_CLASSIC_DLC:
            raise HostToolError(ErrorCategory.TRANSPORT, "图莫斯经典 CAN 帧必须为 8 字节")
        api, handle = self._require_open()
        result = api.send_can(handle, self.channel, can_id, data)
        if result != 1:
            raise HostToolError(ErrorCategory.TRANSPORT, f"图莫斯 CAN 发送失败：{result}")

    def receive_can_frame(self, can_id: int, timeout_ms: int) -> CanFrame | None:
        api, handle = self._require_open()
        deadline = monotonic() + timeout_ms / 1000.0
        while monotonic() <= deadline:
            self._pending_frames.extend(api.receive_can(handle, self.channel))
            for _ in range(len(self._pending_frames)):
                frame = self._pending_frames.popleft()
                if frame["id"] != can_id:
                    self._pending_frames.append(frame)
                    continue
                return CanFrame(
                    can_id=int(frame["id"]),
                    data=bytes(frame["data"]),
                    timestamp_us=int(frame["timestamp_us"]),
                )
            sleep(0.001)
        return None

    def clear_can_receive_buffer(self) -> None:
        api, handle = self._require_open()
        api.clear_can(handle, self.channel)
        self._pending_frames.clear()

    def _require_open(self):
        if self._api is None or self._handle is None:
            raise HostToolError(ErrorCategory.DEVICE, "图莫斯 CAN 设备尚未打开")
        return self._api, self._handle
