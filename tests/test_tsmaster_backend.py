import unittest
from pathlib import Path

from unified_can_lin_host_tool.adapters.tsmaster import TsmasterDevice
from unified_can_lin_host_tool.backends.settings import TsmasterSettings
from unified_can_lin_host_tool.backends.tsmaster_backend import TsmasterHostBackend
from unified_can_lin_host_tool.profile import load_profile


class RecordingAdapter:
    probe_calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.sent = []
        self.responses = [
            (0x3D, bytes.fromhex("02 02 50 01 FF FF FF FF")),
        ]

    @classmethod
    def probe(cls, **kwargs):
        cls.probe_calls.append(kwargs)
        return [
            TsmasterDevice(
                index=0,
                vendor="TOSUN",
                name="TC1016",
                serial="277950ED003D1096",
                device_type=3,
                device_index=0,
            )
        ]

    def open_lin(self):
        self.opened = True

    def close(self):
        self.closed = True

    def send_lin_frame(self, frame_id, data):
        self.sent.append((frame_id, data))

    def receive_lin_frame(self, frame_id, timeout_ms):
        from unified_can_lin_host_tool.transport.base import LinFrame

        if not self.responses:
            return None
        response_id, data = self.responses.pop(0)
        return LinFrame(frame_id=response_id, data=data)


class TsmasterHostBackendTest(unittest.TestCase):
    def setUp(self):
        RecordingAdapter.probe_calls.clear()

    def test_scan_exposes_real_tsmaster_lin_channel_mapping(self):
        settings = TsmasterSettings(project_dir="D:/project/TS_Master")
        backend = TsmasterHostBackend(settings=settings, adapter_cls=RecordingAdapter)

        devices = backend.scan()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, "TC1016")
        channel = devices[0].channels[0]
        self.assertEqual(channel.vendor, "TSMaster")
        self.assertEqual(channel.mapping["project_dir"], "D:/project/TS_Master")
        self.assertEqual(channel.mapping["hw_name"], "TC1016")
        self.assertEqual(channel.mapping["hw_channel"], 0)
        self.assertIn("e68_flash", channel.capabilities)
        self.assertEqual(RecordingAdapter.probe_calls[0]["dll_path"], settings.dll_path)

    def test_scan_filters_virtual_devices_when_hw_name_is_configured(self):
        class MultiDeviceAdapter(RecordingAdapter):
            @classmethod
            def probe(cls, **kwargs):
                return [
                    TsmasterDevice(0, "TOSUN", "TC1016", "277950ED003D1096", 3, 0),
                    TsmasterDevice(1, "TOSUN", "VIRTUAL", "N/A", 3, 0),
                    TsmasterDevice(2, "TOSUN", "default", "N/A", 3, 0),
                ]

        backend = TsmasterHostBackend(settings=TsmasterSettings(hw_name="TC1016"), adapter_cls=MultiDeviceAdapter)

        devices = backend.scan()

        self.assertEqual([device.name for device in devices], ["TC1016"])

    def test_connect_opens_lin_with_project_mapping_and_profile_baudrate(self):
        settings = TsmasterSettings(project_dir="D:/project/TS_Master", app_name="Codex_E68_GUI")
        backend = TsmasterHostBackend(settings=settings, adapter_cls=RecordingAdapter)
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        channel = backend.scan()[0].channels[0]

        session = backend.connect(channel, profile)

        adapter = session.adapter
        self.assertTrue(adapter.opened)
        self.assertEqual(adapter.kwargs["app_name"], "Codex_E68_GUI")
        self.assertEqual(adapter.kwargs["project_dir"], Path("D:/project/TS_Master"))
        self.assertEqual(adapter.kwargs["baud_kbps"], 19.2)

    def test_session_request_uds_uses_real_adapter_transport(self):
        settings = TsmasterSettings(project_dir="D:/project/TS_Master")
        backend = TsmasterHostBackend(settings=settings, adapter_cls=RecordingAdapter)
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        session = backend.connect(backend.scan()[0].channels[0], profile)

        response = session.request_uds(bytes.fromhex("10 01"))

        self.assertEqual(response, bytes.fromhex("50 01"))
        self.assertEqual(session.adapter.sent[0][0], 0x3C)

    def test_skip_close_mode_leaves_tsmaster_session_open_for_process_exit(self):
        settings = TsmasterSettings(project_dir="D:/project/TS_Master", close_mode="skip")
        backend = TsmasterHostBackend(settings=settings, adapter_cls=RecordingAdapter)
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        session = backend.connect(backend.scan()[0].channels[0], profile)

        session.close()

        self.assertFalse(session.adapter.closed)
