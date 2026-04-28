import tempfile
import unittest
from pathlib import Path

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError:  # pragma: no cover - exercised only when UI deps are absent.
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.core.cancel import OperationCancelled
from unified_can_lin_host_tool.ui.workers import FlashWorker, UdsWorker


class CancellingSession:
    def request_uds(self, *args, **kwargs):
        raise OperationCancelled("operation cancelled")

    def flash_e68(self, *args, **kwargs):
        raise OperationCancelled("operation cancelled")


class RecordingSession:
    def __init__(self):
        self.start_in_bootloader = None

    def flash_e68(self, *args, **kwargs):
        self.start_in_bootloader = kwargs["start_in_bootloader"]
        return []


class WorkerCancellationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_uds_worker_reports_cancelled_event_instead_of_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = UdsWorker(CancellingSession(), bytes.fromhex("10 01"), log_dir=Path(tmp))
            events = []
            failures = []
            worker.event.connect(events.append)
            worker.failed.connect(failures.append)

            worker.run()

        self.assertEqual(failures, [])
        self.assertEqual(events[-1].kind, "cancelled")
        self.assertEqual(events[-1].message, "operation cancelled")

    def test_flash_worker_passes_start_in_bootloader_to_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = RecordingSession()
            worker = FlashWorker(
                session,
                flash_driver_path=Path("tests/fixtures/flash_driver_18b.bin"),
                app_path=Path("tests/fixtures/app_20b.bin"),
                log_dir=Path(tmp),
                start_in_bootloader=True,
            )

            worker.run()

        self.assertTrue(session.start_in_bootloader)

    def test_flash_worker_reports_cancelled_event_instead_of_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = FlashWorker(
                CancellingSession(),
                flash_driver_path=Path("tests/fixtures/flash_driver_18b.bin"),
                app_path=Path("tests/fixtures/app_20b.bin"),
                log_dir=Path(tmp),
            )
            events = []
            failures = []
            worker.event.connect(events.append)
            worker.failed.connect(failures.append)

            worker.run()

        self.assertEqual(failures, [])
        self.assertEqual(events[-1].kind, "cancelled")
        self.assertEqual(events[-1].message, "operation cancelled")
