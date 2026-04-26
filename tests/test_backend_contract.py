import tempfile
import unittest
from pathlib import Path

from unified_can_lin_host_tool.backends.base import HostBackend, HostSession
from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
from unified_can_lin_host_tool.profile import load_profile


class BackendContractTest(unittest.TestCase):
    def test_fake_backend_matches_host_backend_protocol(self):
        backend = FakeHostBackend()

        self.assertIsInstance(backend, HostBackend)

    def test_fake_session_matches_host_session_protocol(self):
        backend = FakeHostBackend()
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        channel = backend.scan()[0].channels[0]

        session = backend.connect(channel, profile)

        self.assertIsInstance(session, HostSession)

    def test_scan_channels_expose_mapping_fields(self):
        backend = FakeHostBackend()

        channel = backend.scan()[0].channels[0]

        self.assertEqual(channel.vendor, "TSMaster")
        self.assertEqual(channel.mapping["app_channel"], 0)
        self.assertEqual(channel.mapping["hw_channel"], 0)
        self.assertIn("lin_diag", channel.capabilities)

    def test_manual_uds_still_returns_repeatable_response(self):
        backend = FakeHostBackend()
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        session = backend.connect(backend.scan()[0].channels[0], profile)

        with tempfile.TemporaryDirectory() as tmp:
            first = session.request_uds(bytes.fromhex("10 01"), log_dir=Path(tmp))
            second = session.request_uds(bytes.fromhex("10 01"), log_dir=Path(tmp))

        self.assertEqual(first, bytes.fromhex("50 01"))
        self.assertEqual(second, bytes.fromhex("50 01"))
