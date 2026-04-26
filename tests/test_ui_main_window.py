import unittest

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError:  # pragma: no cover - exercised only when UI deps are absent.
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.ui.main_window import MainWindow


class MissingDllBackend:
    name = "TSMaster"

    def scan(self):
        raise HostToolError(ErrorCategory.DEVICE, "load TSMaster DLL failed: missing.dll")

    def connect(self, channel, profile):
        raise AssertionError("connect should not be called")


class MainWindowBackendRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_main_window_shows_backend_mapping_summary(self):
        window = MainWindow()

        summary_text = window.config_summary_text.toPlainText()

        self.assertIn("TSMaster.hw_channel", summary_text)
        self.assertIn("USB2XXX.channel_index", summary_text)
        window.close()

    def test_main_window_uses_first_injected_backend_when_fake_is_absent(self):
        window = MainWindow(backends={"TSMaster": MissingDllBackend()})

        try:
            self.assertEqual(window.backend_combo.currentText(), "TSMaster")
            window._on_scan_clicked()
            self._wait_for_worker_threads(window)

            trace_text = window.trace_log.toPlainText()
            self.assertIn("device:", trace_text)
            self.assertIn("missing.dll", trace_text)
        finally:
            window.close()

    def _wait_for_worker_threads(self, window: MainWindow) -> None:
        loop = QEventLoop()

        def poll():
            if not window._active_threads:
                loop.quit()
                return
            QTimer.singleShot(10, poll)

        QTimer.singleShot(0, poll)
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
