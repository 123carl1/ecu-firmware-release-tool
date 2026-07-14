from ctypes import c_uint
from pathlib import Path

from unified_can_lin_host_tool.adapters import usb2xxx
from unified_can_lin_host_tool.adapters.usb2xxx import (
    CAN_MSG,
    Usb2xxxAdapter,
    _Usb2xxxApi,
)


def test_default_dll_search_uses_only_runtime_and_common_install_locations(monkeypatch):
    checked_paths = []
    configured = Path(r"D:\Configured\USB2XXX.dll")
    frozen = Path(r"D:\PyInstaller\USB2XXX.dll")
    executable = Path(r"D:\Apps\USB2XXX.dll")
    common_install = Path(r"D:\software\USB2XXX\USB2XXX.dll")
    monkeypatch.setenv("USB2XXX_DLL", str(configured))
    monkeypatch.setattr(usb2xxx.sys, "_MEIPASS", str(frozen.parent), raising=False)
    monkeypatch.setattr(usb2xxx.sys, "executable", str(executable.with_name("EcuReleaseCLI.exe")))

    def record_missing(path):
        checked_paths.append(path)
        return False

    monkeypatch.setattr(Path, "is_file", record_missing)

    assert usb2xxx._default_usb2xxx_dll() == str(configured)
    assert checked_paths == [configured, frozen, executable, common_install]


class FakeUsb2xxxApi:
    def __init__(self):
        self.closed = []
        self.sent = []
        self.frames = []

    def scan_handles(self):
        return [c_uint(101).value]

    def open_device(self, handle):
        return 1

    def close_device(self, handle):
        self.closed.append(handle)
        return 1

    def read_device(self, handle):
        return {
            "serial": "3041545500313034410034A8",
            "product": "UTA0401",
            "firmware": "USB2XXX FS Application",
            "can_channel_count": 2,
            "is_can_fd": False,
        }

    def initialize_can(self, handle, channel, baudrate, receive_ids):
        self.initialized = (handle, channel, baudrate, receive_ids)

    def clear_can(self, handle, channel):
        self.frames.clear()

    def send_can(self, handle, channel, can_id, payload):
        self.sent.append((handle, channel, can_id, payload))
        return 1

    def receive_can(self, handle, channel):
        frames, self.frames = self.frames, []
        return frames


def test_probe_uses_sdk_reported_can_channel_count():
    api = FakeUsb2xxxApi()

    devices = Usb2xxxAdapter.probe_can_devices(api_factory=lambda _path: api)

    assert len(devices) == 1
    assert devices[0].product == "UTA0401"
    assert devices[0].serial == "3041545500313034410034A8"
    assert devices[0].can_channel_count == 2
    assert api.closed == [101]


def test_adapter_implements_can_transport_contract():
    api = FakeUsb2xxxApi()
    adapter = Usb2xxxAdapter(
        device_serial="3041545500313034410034A8",
        channel=1,
        baudrate=500000,
        receive_ids=(0x709,),
        api_factory=lambda _path: api,
    )

    adapter.open_can()
    assert api.initialized == (101, 1, 500000, (0x709,))

    adapter.send_can_frame(0x701, bytes.fromhex("04 22 F1 A0 AA AA AA AA"))
    assert api.sent[-1][2:] == (
        0x701,
        bytes.fromhex("04 22 F1 A0 AA AA AA AA"),
    )

    api.frames = [
        {"id": 0x123, "data": b"\x00" * 8, "timestamp_us": 1},
        {"id": 0x709, "data": bytes.fromhex("03 7F 22 31 AA AA AA AA"), "timestamp_us": 2},
    ]
    frame = adapter.receive_can_frame(0x709, 10)
    assert frame is not None
    assert frame.can_id == 0x709
    assert frame.timestamp_us == 2

    adapter.close()
    assert api.closed[-1] == 101


def test_can_message_layout_keeps_classic_can_payload():
    message = CAN_MSG()
    assert len(message.Data) == 8


def test_low_level_send_prefers_usb_nonexclusive_synchronous_api():
    calls = []

    class Dll:
        def CAN_SendMsgSynch(self, handle, channel, _message, count):
            calls.append(("sync", handle, channel, count))
            return 1

        def CAN_SendMsg(self, *_args):
            raise AssertionError("exclusive USB send API must not be used")

    api = _Usb2xxxApi.__new__(_Usb2xxxApi)
    api.dll = Dll()

    assert api.send_can(101, 0, 0x701, b"\xAA" * 8) == 1
    assert calls == [("sync", 101, 0, 1)]
