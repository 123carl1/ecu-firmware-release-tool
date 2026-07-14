import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWidgets import QMessageBox
except ModuleNotFoundError:
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.ui.release_workspace import (
    ReleaseMainWindow,
    release_cli_process_command,
    release_ota_arguments,
)
from unified_can_lin_host_tool.ui import release_workspace
from unified_can_lin_host_tool.tool_identity import ToolIdentity
from unified_can_lin_host_tool.update.metadata import InstallerAsset, UpdateInfo
from unified_can_lin_host_tool.versioning import SemanticVersion


def update_info(version="0.2.1", *, size=1024):
    return UpdateInfo(
        repository="owner/ecu-firmware-release-tool",
        version=SemanticVersion.parse(version),
        tag=f"v{version}",
        commit="02" * 20,
        generated_at="2026-07-14T12:00:00Z",
        channel="stable",
        release_notes="修复更新流程并提升稳定性",
        installer=InstallerAsset(
            f"EcuReleaseTool_Setup_{version}.exe", size, "03" * 32
        ),
        verified_key_id="release-2026",
    )


class FakeUpdateService:
    def __init__(self, info=None, installer=None):
        self.info = info
        self.installer = installer
        self.check_calls = 0
        self.download_calls = 0

    def check(self):
        self.check_calls += 1
        return self.info

    def download(self, info, *, progress=None, cancelled=lambda: False):
        self.download_calls += 1
        if progress is not None:
            progress(info.installer.size, info.installer.size)
        if cancelled():
            raise RuntimeError("安装包下载已取消")
        return self.installer


class BlockingCancelledUpdateService(FakeUpdateService):
    def __init__(self, info):
        super().__init__(info, Path("setup.exe"))
        self.started = threading.Event()

    def download(self, info, *, progress=None, cancelled=lambda: False):
        self.download_calls += 1
        self.started.set()
        while not cancelled():
            threading.Event().wait(0.005)
        raise RuntimeError("安装包下载已取消")


class ReleaseWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_workspace_displays_tool_identity_in_title_and_startup_log(self):
        identity = ToolIdentity("0.2.0", "01" * 20, "", "", False)
        with patch.object(
            release_workspace, "get_tool_identity", return_value=identity, create=True
        ):
            window = ReleaseMainWindow()
        try:
            self.assertEqual(window.windowTitle(), "ECU Firmware Release Tool 0.2.0")
            self.assertIn("版本 0.2.0，提交 0101010", window.log.toPlainText())
        finally:
            window.close()

    def test_development_build_explains_that_official_updates_are_disabled(self):
        identity = ToolIdentity("0.2.0", "development", "", "", False)
        with patch.object(release_workspace, "get_tool_identity", return_value=identity):
            window = ReleaseMainWindow()
        try:
            self.assertIn("开发构建，不自动检查正式更新", window.log.toPlainText())
            with patch.object(QMessageBox, "about") as about:
                window._show_about()
            details = about.call_args.args[2]
            self.assertIn("完整提交：development", details)
            self.assertIn("固化仓库：未固化（开发构建）", details)
            self.assertIn("开发构建，不自动检查正式更新", details)
        finally:
            window.close()

    def test_non_official_or_repository_missing_identity_never_auto_checks_network(self):
        identities = (
            ToolIdentity("0.2.0", "development", "", "", False),
            ToolIdentity("0.2.0", "01" * 20, "2026-07-14T12:00:00Z", "", True),
        )
        for identity in identities:
            with self.subTest(identity=identity), patch.object(
                release_workspace, "get_tool_identity", return_value=identity
            ):
                service = FakeUpdateService(update_info())
                window = ReleaseMainWindow(update_service=service)
                try:
                    window.show()
                    self.app.processEvents()
                    self.app.processEvents()
                    self.assertEqual(service.check_calls, 0)
                    self.assertIn(
                        "开发构建，不自动检查正式更新", window.log.toPlainText()
                    )
                finally:
                    window.close()

    def test_help_menu_exposes_manual_update_check_and_about(self):
        window = ReleaseMainWindow(auto_check=False)
        try:
            self.assertEqual(window.check_update_action.text(), "检查更新")
            self.assertEqual(window.about_action.text(), "关于")
        finally:
            window.close()

    def test_auto_check_runs_once_after_first_show_and_manual_check_can_repeat(self):
        service = FakeUpdateService()
        identity = ToolIdentity(
            "0.2.0", "01" * 20, "2026-07-14T12:00:00Z",
            "owner/ecu-firmware-release-tool", True,
        )
        with patch.object(release_workspace, "get_tool_identity", return_value=identity):
            window = ReleaseMainWindow(update_service=service)
        try:
            window.show()
            self.app.processEvents()
            self.app.processEvents()
            self.assertTrue(self._wait_until(lambda: service.check_calls == 1))

            window.hide()
            window.show()
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(service.check_calls, 1)

            window.check_update_action.trigger()
            self.assertTrue(self._wait_until(lambda: service.check_calls == 2))
            self.assertTrue(self._wait_until(lambda: window._update_thread is None))
        finally:
            window.close()

    def test_available_update_prompt_contains_version_notes_and_size(self):
        info = update_info(size=1536)
        window = ReleaseMainWindow(auto_check=False)
        try:
            with patch.object(window, "_prompt_update", return_value=False) as prompt:
                window._handle_update_check_result(info)
            message = prompt.call_args.args[0]
            self.assertIn("当前版本：0.2.0", message)
            self.assertIn("目标版本：0.2.1", message)
            self.assertIn("修复更新流程并提升稳定性", message)
            self.assertIn("1.5 KiB", message)
        finally:
            window.close()

    def test_later_reminder_suppresses_followup_prompt_for_this_process(self):
        info = update_info()
        window = ReleaseMainWindow(auto_check=False)
        try:
            with patch.object(window, "_prompt_update", return_value=False) as prompt:
                window._handle_update_check_result(info)
                window._handle_update_check_result(info)
            prompt.assert_called_once()
            self.assertIn("本次运行已稍后提醒", window.status_label.text())
        finally:
            window.close()

    def test_immediate_update_is_disabled_while_scan_or_ota_process_exists(self):
        window = ReleaseMainWindow(auto_check=False)
        try:
            window._process = object()
            self.assertFalse(window._can_start_update_install())
            window._process = None
            window._update_thread = object()
            self.assertFalse(window._can_start_update_install())
        finally:
            window._process = None
            window._update_thread = None
            window.close()

    def test_update_error_uses_update_category_not_device_or_ota_category(self):
        window = ReleaseMainWindow(auto_check=False)
        try:
            window._handle_update_failure("检查", "network down")
            self.assertEqual(window.status_label.text(), "更新检查失败：network down")
            self.assertNotIn("设备扫描", window.status_label.text())
            self.assertNotIn("OTA", window.status_label.text())
        finally:
            window.close()

    def test_download_can_be_cancelled_and_reports_update_category(self):
        service = BlockingCancelledUpdateService(update_info())
        window = ReleaseMainWindow(update_service=service, auto_check=False)
        try:
            window._download_update(service.info)
            self.assertTrue(service.started.wait(1))
            window._cancel_update_download()
            self.assertTrue(self._wait_until(lambda: window._update_thread is None))
            self.assertIn("更新下载已取消", window.status_label.text())
            self.assertNotIn("设备扫描", window.status_label.text())
            self.assertNotIn("OTA", window.status_label.text())
        finally:
            window.close()

    def test_download_completion_does_not_install_if_scan_started(self):
        installer = Path(r"D:\Temp\update-test\setup.exe")
        service = FakeUpdateService(update_info(), installer)
        window = ReleaseMainWindow(update_service=service, auto_check=False)
        try:
            window._process = object()
            with patch.object(window, "_launch_verified_installer") as launch:
                window._handle_download_result(installer)
            launch.assert_not_called()
            self.assertIn("已缓存", window.status_label.text())
        finally:
            window._process = None
            window.close()

    def test_installer_launch_success_freezes_tasks_and_quits(self):
        window = ReleaseMainWindow(auto_check=False)
        try:
            with patch.object(
                QProcess, "startDetached", return_value=(True, 1234)
            ) as start, patch.object(
                QApplication.instance(), "quit"
            ) as quit_app, patch.object(release_workspace.os, "getpid", return_value=4321):
                window._launch_verified_installer(Path("setup.exe"))

            self.assertFalse(window.scan_button.isEnabled())
            self.assertFalse(window.project_combo.isEnabled())
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0], "setup.exe")
            self.assertIn("/PARENT_PID=4321", start.call_args.args[1])
            self.assertIn("/AUTO_UPDATE", start.call_args.args[1])
            quit_app.assert_called_once()
        finally:
            window._update_exit_requested = False
            window.close()

    def test_installer_launch_failure_unfreezes_without_closing(self):
        window = ReleaseMainWindow(auto_check=False)
        try:
            with patch.object(
                QProcess, "startDetached", return_value=(False, 0)
            ), patch.object(QApplication.instance(), "quit") as quit_app:
                window._launch_verified_installer(Path("setup.exe"))

            self.assertTrue(window.scan_button.isEnabled())
            self.assertTrue(window.project_combo.isEnabled())
            self.assertIn("更新安装器启动失败", window.status_label.text())
            quit_app.assert_not_called()
        finally:
            window.close()

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

    def test_gui_scan_arguments_explicitly_select_auto_adapter(self):
        self.assertEqual(
            release_workspace.release_scan_arguments("AS5PR"),
            ["scan", "--project", "AS5PR", "--adapter", "auto"],
        )

    def test_scan_error_is_shown_as_failure_instead_of_zero_channels(self):
        window = ReleaseMainWindow()
        try:
            window._handle_output_line(json.dumps({
                "event": "error",
                "ok": False,
                "error": "设备扫描失败：同星 SDK 不可用",
            }))

            self.assertEqual(window.device_combo.count(), 0)
            self.assertEqual(window.status_label.text(), "失败：设备扫描失败：同星 SDK 不可用")
            self.assertNotIn("扫描完成", window.status_label.text())
        finally:
            window.close()

    def test_usb2xxx_scan_result_and_ota_arguments_use_selected_sdk_channel(self):
        window = ReleaseMainWindow()
        try:
            window._handle_output_line(json.dumps({
                "event": "scan_result",
                "ok": True,
                "devices": [{
                    "adapter": "usb2xxx",
                    "name": "图莫斯 UTA0401",
                    "product": "UTA0401",
                    "serial": "USB-SERIAL",
                    "deviceIndex": 0,
                    "channels": [
                        {"displayChannel": 1, "hwChannel": 0, "canChannelCount": 2},
                        {"displayChannel": 2, "hwChannel": 1, "canChannelCount": 2},
                    ],
                }],
            }))

            self.assertEqual(window.device_combo.count(), 2)
            window.device_combo.setCurrentIndex(1)
            selected = window.device_combo.currentData()
            arguments = release_ota_arguments(Path("app.hex"), "AS5PR", selected)

            self.assertIn("usb2xxx", arguments)
            self.assertEqual(arguments[arguments.index("--hw-channel") + 1], "1")
            self.assertNotIn("--tsmaster-channel", arguments)
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

    @classmethod
    def _wait_until(cls, condition, attempts=100):
        for _ in range(attempts):
            cls.app.processEvents()
            if condition():
                return True
            threading.Event().wait(0.005)
        return condition()
