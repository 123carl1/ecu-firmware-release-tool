import unittest

from unified_can_lin_host_tool.backends.settings import (
    BackendSettings,
    TsmasterSettings,
    Usb2xxxSettings,
    default_backend_settings,
)


class BackendSettingsTest(unittest.TestCase):
    def test_default_settings_include_required_tsmaster_mapping_fields(self):
        settings = default_backend_settings()

        self.assertIsInstance(settings.tsmaster, TsmasterSettings)
        self.assertEqual(settings.tsmaster.dll_path, "TSMaster.dll")
        self.assertIsNone(settings.tsmaster.project_dir)
        self.assertEqual(settings.tsmaster.app_channel, 0)
        self.assertEqual(settings.tsmaster.hw_index, 0)
        self.assertEqual(settings.tsmaster.hw_channel, 0)
        self.assertGreater(settings.tsmaster.baud_kbps, 0)

    def test_default_settings_include_required_usb2xxx_fields(self):
        settings = default_backend_settings()

        self.assertIsInstance(settings.usb2xxx, Usb2xxxSettings)
        self.assertIsInstance(settings.usb2xxx.dll_path, str)
        self.assertTrue(settings.usb2xxx.dll_path)
        self.assertEqual(settings.usb2xxx.device_index, 0)
        self.assertEqual(settings.usb2xxx.channel_index, 0)
        self.assertEqual(settings.usb2xxx.baudrate, 19200)

    def test_settings_summary_is_plain_text_friendly(self):
        settings = BackendSettings(
            tsmaster=TsmasterSettings(hw_name="TC1016", hw_channel=1),
            usb2xxx=Usb2xxxSettings(channel_index=2),
        )

        summary = settings.summary_lines()

        self.assertIn("TSMaster.hw_name: TC1016", summary)
        self.assertIn("TSMaster.hw_channel: 1", summary)
        self.assertTrue(any(line.startswith("USB2XXX.dll_path: ") for line in summary))
        self.assertIn("USB2XXX.channel_index: 2", summary)
