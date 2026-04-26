import unittest


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
