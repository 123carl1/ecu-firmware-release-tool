import unittest

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError:
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.ui.release_workspace import ReleaseMainWindow, release_cli_process_command


class ReleaseWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_as5pr_has_package_and_automatic_identity_actions_without_manual_boot_switch(self):
        window = ReleaseMainWindow()
        try:
            self.assertEqual(window.project_combo.currentText(), "AS5PR")
            self.assertTrue(window.probe_button.isEnabled())
            self.assertTrue(window.flash_button.isEnabled())
            self.assertFalse(hasattr(window, "start_in_bootloader_check"))
        finally:
            window.close()

    def test_e68_real_flash_is_disabled(self):
        window = ReleaseMainWindow()
        try:
            window.project_combo.setCurrentText("E68")
            self.assertFalse(window.probe_button.isEnabled())
            self.assertFalse(window.flash_button.isEnabled())
        finally:
            window.close()

    def test_frozen_gui_invokes_sibling_cli_instead_of_itself(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"D:\software\EcuReleaseTool\EcuReleaseTool.exe"):
            program, arguments = release_cli_process_command(["inspect", "x.erel", "--project", "AS5PR"])

        self.assertEqual(program, r"D:\software\EcuReleaseTool\EcuReleaseCLI.exe")
        self.assertEqual(arguments, ["inspect", "x.erel", "--project", "AS5PR"])

    def test_development_gui_invokes_python_module(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "frozen", False, create=True):
            program, arguments = release_cli_process_command(["probe", "--project", "AS5PR"])

        self.assertEqual(program, sys.executable)
        self.assertEqual(arguments[:2], ["-m", "unified_can_lin_host_tool.cli.release"])
