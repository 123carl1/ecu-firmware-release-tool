import unittest
from pathlib import Path

from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
from unified_can_lin_host_tool.profile import load_profile


class FakeHostBackendTest(unittest.TestCase):
    def test_scan_returns_fake_tsmaster_and_usb2xxx_lin_channels(self):
        backend = FakeHostBackend()

        devices = backend.scan()

        self.assertEqual([device.vendor for device in devices], ["TSMaster", "USB2XXX"])
        self.assertTrue(all(device.channels for device in devices))
        self.assertTrue(all(device.channels[0].bus == "LIN" for device in devices))

    def test_fake_backend_connects_and_sends_uds_default_session(self):
        backend = FakeHostBackend()
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        channel = backend.scan()[0].channels[0]

        session = backend.connect(channel, profile)
        response = session.request_uds(bytes.fromhex("10 01"))

        self.assertEqual(response, bytes.fromhex("50 01"))
