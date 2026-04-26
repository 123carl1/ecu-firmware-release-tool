import unittest
import tempfile
from pathlib import Path

from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
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

    def test_fake_backend_repeats_same_uds_request_without_response_shift(self):
        backend = FakeHostBackend()
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        channel = backend.scan()[0].channels[0]
        session = backend.connect(channel, profile)

        first_response = session.request_uds(bytes.fromhex("10 01"))
        second_response = session.request_uds(bytes.fromhex("10 01"))

        self.assertEqual(first_response, bytes.fromhex("50 01"))
        self.assertEqual(second_response, bytes.fromhex("50 01"))

    def test_fake_backend_manual_uds_emits_tx_rx_trace_and_writes_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeHostBackend()
            profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
            channel = backend.scan()[0].channels[0]
            session = backend.connect(channel, profile)
            events = []

            response = session.request_uds(
                bytes.fromhex("10 01"),
                log_dir=Path(tmp),
                on_event=events.append,
            )

            self.assertEqual(response, bytes.fromhex("50 01"))
            trace_events = [event for event in events if event.kind == "trace"]
            self.assertEqual([event.trace.direction for event in trace_events], ["TX", "RX"])
            self.assertEqual([event.trace.frame_id for event in trace_events], [0x3C, 0x3D])

            log_files = list(Path(tmp).glob("trace_*.log"))
            self.assertEqual(len(log_files), 1)
            text = log_files[0].read_text(encoding="utf-8")
            self.assertIn("TX LIN id=0x3C", text)
            self.assertIn("RX LIN id=0x3D", text)

    def test_fake_backend_uds_and_flash_share_diag_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeHostBackend()
            profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
            channel = backend.scan()[0].channels[0]
            session = backend.connect(channel, profile)

            self.assertTrue(session.bus_session.enter_diag_exclusive("external"))
            try:
                with self.assertRaises(HostToolError) as uds_error:
                    session.request_uds(bytes.fromhex("10 01"))
                self.assertEqual(uds_error.exception.category, ErrorCategory.TRANSPORT)

                with self.assertRaises(HostToolError) as flash_error:
                    session.flash_e68(
                        flash_driver_path=Path("tests/fixtures/flash_driver_18b.bin"),
                        app_path=Path("tests/fixtures/app_20b.bin"),
                        log_dir=Path(tmp),
                    )
                self.assertEqual(flash_error.exception.category, ErrorCategory.TRANSPORT)
            finally:
                session.bus_session.release_diag_exclusive("external")

    def test_fake_backend_flash_emits_progress_trace_and_success_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeHostBackend()
            profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
            channel = backend.scan()[0].channels[0]
            session = backend.connect(channel, profile)
            callback_events = []

            events = list(
                session.flash_e68(
                    flash_driver_path=Path("tests/fixtures/flash_driver_18b.bin"),
                    app_path=Path("tests/fixtures/app_20b.bin"),
                    log_dir=Path(tmp),
                    dry_run=True,
                    on_event=callback_events.append,
                )
            )

            self.assertEqual(events[0].kind, "started")
            self.assertTrue(any(event.kind == "progress" for event in events))
            self.assertTrue(any(event.kind == "trace" for event in callback_events))
            self.assertEqual(events[-1].kind, "result")
            self.assertEqual(events[-1].message, "FLASH SUCCESS")
