import unittest

from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend


class FakeHostBackendTest(unittest.TestCase):
    def test_scan_returns_fake_tsmaster_and_usb2xxx_lin_channels(self):
        backend = FakeHostBackend()

        devices = backend.scan()

        self.assertEqual([device.vendor for device in devices], ["TSMaster", "USB2XXX"])
        self.assertTrue(all(device.channels for device in devices))
        self.assertTrue(all(device.channels[0].bus == "LIN" for device in devices))
