import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWidgets import QMessageBox
except ModuleNotFoundError:
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.ui.release_workspace import ReleaseMainWindow, release_cli_process_command


class ReleaseWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_workspace_exposes_only_scan_and_ota_primary_actions(self):
        window = ReleaseMainWindow()
        try:
            self.assertEqual(window.project_combo.currentText(), "AS5PR")
            self.assertTrue(window.scan_button.isEnabled())
            self.assertFalse(window.ota_button.isEnabled())
            self.assertFalse(hasattr(window, "inspect_button"))
            self.assertFalse(hasattr(window, "dry_run_button"))
            self.assertFalse(hasattr(window, "probe_button"))
            self.assertFalse(hasattr(window, "start_in_bootloader_check"))
        finally:
            window.close()

    def test_e68_ota_is_disabled_until_implemented(self):
        window = ReleaseMainWindow()
        try:
            window.project_combo.setCurrentText("E68")
            self.assertFalse(window.scan_button.isEnabled())
            self.assertFalse(window.ota_button.isEnabled())
        finally:
            window.close()

    def test_scan_result_populates_device_selector_and_enables_ota(self):
        window = ReleaseMainWindow()
        try:
            with tempfile.TemporaryDirectory(dir=r"D:\Temp") as directory:
                app = Path(directory) / "app.hex"
                app.write_text(":00000001FF", encoding="ascii")
                window.package_edit.setText(str(app))
                window._handle_output_line(json.dumps({
                    "event": "scan_result",
                    "ok": True,
                    "devices": [{
                        "name": "TC1016", "product": "TOSUN HS CANFD4.LIN2",
                        "serial": "ABC123", "deviceIndex": 0,
                        "hwSubtype": 11,
                        "channels": [
                            {"displayChannel": channel + 1, "hwChannel": channel,
                             "appChannel": channel, "canChannelCount": 4,
                             "baseHwChannel": 0}
                            for channel in range(4)
                        ],
                    }],
                }))

                self.assertEqual(window.device_combo.count(), 4)
                self.assertIn("TC1016", window.device_combo.currentText())
                self.assertIn("CANFD4.LIN2", window.device_combo.currentText())
                self.assertIn("CAN1", window.device_combo.itemText(0))
                self.assertIn("CAN2", window.device_combo.itemText(1))
                self.assertIn("CAN4", window.device_combo.itemText(3))
                self.assertIn("ABC123", window.device_combo.currentText())
                self.assertTrue(window.ota_button.isEnabled())

                window.device_combo.setCurrentIndex(3)
                selected = window.device_combo.currentData()
                self.assertEqual(selected["appChannel"], 3)
                self.assertEqual(selected["hwChannel"], 3)
                self.assertEqual(selected["canChannelCount"], 4)
        finally:
            window.close()

    def test_progress_event_updates_progress_bar_and_status(self):
        window = ReleaseMainWindow()
        try:
            window._handle_output_line(json.dumps({
                "event": "progress", "percent": 67,
                "stage": "下载 App", "message": "block 32/48",
            }))

            self.assertEqual(window.progress.value(), 67)
            self.assertEqual(window.status_label.text(), "下载 App：block 32/48")
        finally:
            window.close()

    def test_yes_confirmation_starts_real_ota(self):
        window = ReleaseMainWindow()
        try:
            window.device_combo.addItem("TC1016 CAN1", {
                "name": "TC1016", "deviceIndex": 0, "displayChannel": 1,
                "appChannel": 0, "hwChannel": 0, "canChannelCount": 2,
                "baseHwChannel": 0,
            })
            window.device_combo.setCurrentIndex(0)
            with patch.object(QMessageBox, "warning", return_value=int(QMessageBox.StandardButton.Yes)), \
                 patch.object(window, "_execute") as execute:
                window._confirm_flash()

            execute.assert_called_once_with("flash")
        finally:
            window.close()

    def test_ota_terminal_safety_status_is_not_overwritten_by_exit_code(self):
        expected = {
            "ECU_IN_BOOT": "ECU保留在 Boot",
            "FAILED_UNKNOWN": "ECU状态未知",
            "COMPLETED_UNVERIFIED": "未确认 App 通信",
        }
        for status, text in expected.items():
            with self.subTest(status=status):
                window = ReleaseMainWindow()
                try:
                    window._handle_output_line(json.dumps({
                        "event": "result", "ok": False, "status": status,
                        "message": "detail", "log": "trace.log",
                    }))
                    window._finished(4, None)
                    self.assertIn(text, window.status_label.text())
                    self.assertNotIn("exit=4", window.status_label.text())
                finally:
                    window.close()

    def test_close_is_blocked_while_real_ota_is_running(self):
        class CloseEvent:
            ignored = False

            def ignore(self):
                self.ignored = True

        window = ReleaseMainWindow()
        event = CloseEvent()
        try:
            window._process = object()
            window._operation = "flash"
            with patch.object(QMessageBox, "warning"):
                window.closeEvent(event)

            self.assertTrue(event.ignored)
        finally:
            window._process = None
            window._operation = ""
            window.close()

    def test_frozen_gui_invokes_sibling_cli_instead_of_itself(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"D:\software\EcuReleaseTool\EcuReleaseTool.exe"):
            program, arguments = release_cli_process_command(["ota", "app.hex", "--project", "AS5PR"])

        self.assertEqual(program, r"D:\software\EcuReleaseTool\EcuReleaseCLI.exe")
        self.assertEqual(arguments, ["ota", "app.hex", "--project", "AS5PR"])

    def test_development_gui_invokes_python_module(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "frozen", False, create=True):
            program, arguments = release_cli_process_command(["scan", "--project", "AS5PR"])

        self.assertEqual(program, sys.executable)
        self.assertEqual(arguments[:2], ["-m", "unified_can_lin_host_tool.cli.release"])
