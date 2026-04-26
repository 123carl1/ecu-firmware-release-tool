import unittest
import tempfile
from pathlib import Path


class UiWorkerImportTest(unittest.TestCase):
    def test_workers_import_when_pyside6_available(self):
        try:
            import PySide6  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PySide6 is not installed")

        from unified_can_lin_host_tool.ui.workers import ConnectWorker, DeviceScanWorker, FlashWorker, UdsWorker

        self.assertIsNotNone(DeviceScanWorker)
        self.assertIsNotNone(ConnectWorker)
        self.assertIsNotNone(UdsWorker)
        self.assertIsNotNone(FlashWorker)

    def test_uds_worker_emits_trace_events(self):
        try:
            import PySide6  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PySide6 is not installed")

        from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
        from unified_can_lin_host_tool.profile import load_profile
        from unified_can_lin_host_tool.ui.workers import UdsWorker

        with tempfile.TemporaryDirectory() as tmp:
            backend = FakeHostBackend()
            profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
            channel = backend.scan()[0].channels[0]
            session = backend.connect(channel, profile)
            worker = UdsWorker(session, bytes.fromhex("10 01"), log_dir=Path(tmp))
            events = []
            results = []

            worker.event.connect(events.append)
            worker.result.connect(results.append)
            worker.run()

            self.assertEqual(results, [bytes.fromhex("50 01")])
            trace_events = [event for event in events if event.kind == "trace"]
            self.assertEqual([event.trace.direction for event in trace_events], ["TX", "RX"])
