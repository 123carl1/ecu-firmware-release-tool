from ctypes import addressof, c_ubyte, c_uint, sizeof
from pathlib import Path
import subprocess
import tempfile

from unified_can_lin_host_tool.adapters import usb2xxx
from unified_can_lin_host_tool.adapters.usb2xxx import (
    CAN_MSG,
    HARDWARE_INFO,
    Usb2xxxAdapter,
    _Usb2xxxApi,
)


def test_installer_runtime_validation_reports_all_same_size_tampered_dlls():
    script = Path("scripts/build_windows_installer.ps1").resolve()
    script_text = script.read_text(encoding="utf-8")
    assert "ValidateUsb2xxxRuntimeOnly" in script_text, (
        "installer runtime-only validation entry is missing"
    )
    assert "7857f3c43b5f5f41414da0ce04f2914d45af805a7ad0e14a0aa84b6a16a42d1b" in script_text
    assert "a8c91f0ff68fb7802a9f4416728f0eeb4d99af4ceaa4ef7dfe9374e76e375018" in script_text

    with tempfile.TemporaryDirectory(dir=r"D:\Temp") as directory:
        sdk_root = Path(directory)
        dll_dir = sdk_root / "sdk" / "libs" / "windows" / "x86_64"
        dll_dir.mkdir(parents=True)
        (dll_dir / "USB2XXX.dll").write_bytes(b"X" * 538112)
        (dll_dir / "libusb-1.0.dll").write_bytes(b"Y" * 157696)

        result = subprocess.run(
            [
                "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script),
                "-ValidateUsb2xxxRuntimeOnly", "-Usb2xxxSdkRoot", str(sdk_root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "USB2XXX.dll SHA256 mismatch" in output
    assert "libusb-1.0.dll SHA256 mismatch" in output


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


def test_hardware_info_channel_fields_match_official_sdk_offsets():
    lin_field = getattr(HARDWARE_INFO, "LINChannelNum", None)
    actual_offsets = (
        HARDWARE_INFO.CANChannelNum.offset,
        None if lin_field is None else lin_field.offset,
        HARDWARE_INFO.PWMChannelNum.offset,
        HARDWARE_INFO.HaveCANFD.offset,
    )

    assert actual_offsets == (36, 37, 38, 39)


def test_hardware_info_raw_bytes_keep_can_lin_pwm_and_canfd_sentinels():
    hardware = HARDWARE_INFO()
    raw = (c_ubyte * sizeof(HARDWARE_INFO)).from_address(addressof(hardware))
    raw[36], raw[37], raw[38], raw[39] = 2, 3, 5, 7

    actual_values = (
        usb2xxx._byte_value(hardware.CANChannelNum),
        usb2xxx._byte_value(getattr(hardware, "LINChannelNum", b"\0")),
        usb2xxx._byte_value(hardware.PWMChannelNum),
        usb2xxx._byte_value(hardware.HaveCANFD),
    )

    assert actual_values == (2, 3, 5, 7)


def test_read_device_uses_official_have_canfd_byte_offset():
    class Dll:
        @staticmethod
        def DEV_GetDeviceInfo(_handle, _device, _functions):
            return 1

        @staticmethod
        def DEV_GetHardwareInfo(_handle, hardware_pointer):
            hardware = hardware_pointer._obj
            raw = (c_ubyte * sizeof(HARDWARE_INFO)).from_address(addressof(hardware))
            raw[36], raw[37], raw[38], raw[39] = 2, 3, 0, 1
            return 1

    api = _Usb2xxxApi.__new__(_Usb2xxxApi)
    api.dll = Dll()

    info = api.read_device(101)

    assert info["can_channel_count"] == 2
    assert info["is_can_fd"] is True


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

    api = _Usb2xxxApi.__new__(_Usb2xxxApi)
    api.dll = Dll()

    assert api.send_can(101, 0, 0x701, b"\xAA" * 8) == 1
    assert calls == [("sync", 101, 0, 1)]


def test_low_level_send_falls_back_for_legacy_dll_without_synchronous_api():
    calls = []

    class Dll:
        def CAN_SendMsg(self, handle, channel, _message, count):
            calls.append(("legacy", handle, channel, count))
            return 1

    api = _Usb2xxxApi.__new__(_Usb2xxxApi)
    api.dll = Dll()

    assert api.send_can(101, 1, 0x701, b"\xAA" * 8) == 1
    assert calls == [("legacy", 101, 1, 1)]
