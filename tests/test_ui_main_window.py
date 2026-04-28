import unittest

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError:  # pragma: no cover - exercised only when UI deps are absent.
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.ui.main_window import MainWindow
from unified_can_lin_host_tool.ui.models import WorkerEvent


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
        self.assertIn("TSMaster.project_dir", summary_text)
        self.assertIn("TSMaster.close_mode: skip", summary_text)
        self.assertIn("USB2XXX.channel_index", summary_text)
        window.close()

    def test_main_window_registers_real_tsmaster_backend_by_default(self):
        window = MainWindow()

        try:
            self.assertIn("TSMaster", [window.backend_combo.itemText(i) for i in range(window.backend_combo.count())])
            self.assertIn("Fake", [window.backend_combo.itemText(i) for i in range(window.backend_combo.count())])
            self.assertEqual(window.backend_combo.currentText(), "TSMaster")
        finally:
            window.close()

    def test_main_window_defaults_flash_paths_to_real_e68_outputs(self):
        window = MainWindow()

        try:
            flash_path = window.flash_driver_edit.text().replace("\\", "/")
            app_path = window.app_edit.text().replace("\\", "/")
            self.assertIn("artifacts/release/e68_flash_driver_auth.s19", flash_path)
            self.assertIn("artifacts/release/dau_fm33_auth.s19", app_path)
        finally:
            window.close()

    def test_flash_tab_has_browse_buttons_with_s19_filter(self):
        window = MainWindow()

        try:
            self.assertEqual(window.flash_driver_browse_button.text(), "浏览...")
            self.assertEqual(window.app_browse_button.text(), "浏览...")
            self.assertIn("*.s19", window.firmware_file_filter)
            self.assertIn("*.bin", window.firmware_file_filter)
        finally:
            window.close()

    def test_flash_tab_has_explicit_bootloader_start_checkbox(self):
        window = MainWindow()

        try:
            self.assertEqual(window.start_in_bootloader_check.text(), "目标已在 Bootloader")
            self.assertFalse(window.start_in_bootloader_check.isChecked())
        finally:
            window.close()

    def test_flash_progress_log_is_separate_from_raw_trace(self):
        window = MainWindow()

        try:
            progress_event = WorkerEvent(kind="progress", message="下载 App: block 3/10, 18/60 bytes", progress=72)
            trace_event = WorkerEvent(
                kind="trace",
                message="LIN frame",
                trace=TraceEvent(
                    direction="TX",
                    frame_id=0x3C,
                    data=bytes.fromhex("02 02 10 03 FF FF FF FF"),
                ),
            )

            window._on_worker_event(progress_event)
            window._on_worker_event(trace_event)

            stage_text = window.flash_stage_log.toPlainText()
            trace_text = window.trace_log.toPlainText()
            self.assertIn("[ 72%] 下载 App", stage_text)
            self.assertNotIn("LIN id=0x3C", stage_text)
            self.assertEqual(window.flash_status_label.text(), "下载 App: block 3/10, 18/60 bytes")
            self.assertIn("TX  LIN 0x3C", trace_text)
            self.assertIn("DiagnosticSessionControl", trace_text)
        finally:
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
