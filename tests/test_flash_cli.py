import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from unified_can_lin_host_tool.cli.flash_e68_lin import main


class FlashCliTests(unittest.TestCase):
    def test_fake_dry_run_writes_trace_and_reports_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--adapter",
                        "fake",
                        "--profile",
                        "profiles/e68_lin_bootloader.yaml",
                        "--flash-driver",
                        "tests/fixtures/flash_driver_18b.bin",
                        "--app",
                        "tests/fixtures/app_20b.bin",
                        "--log-dir",
                        tmp,
                        "--dry-run",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("FLASH SUCCESS", output.getvalue())
            logs = list(Path(tmp).glob("trace_*.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("0x3C", logs[0].read_text(encoding="utf-8"))

    def test_tsmaster_dry_run_accepts_mapping_arguments(self):
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "--adapter",
                    "tsmaster",
                    "--profile",
                    "profiles/e68_lin_bootloader.yaml",
                    "--flash-driver",
                    "tests/fixtures/flash_driver_18b.bin",
                    "--app",
                    "tests/fixtures/app_20b.bin",
                    "--dry-run",
                    "--tsmaster-dll",
                    "D:/custom/TSMaster.dll",
                    "--tsmaster-app",
                    "MyApp",
                    "--tsmaster-app-channel",
                    "1",
                    "--tsmaster-hw-name",
                    "TC1016",
                    "--tsmaster-hw-subtype",
                    "11",
                    "--tsmaster-hw-index",
                    "2",
                    "--tsmaster-hw-channel",
                    "3",
                ]
            )

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("DRY RUN", text)
        self.assertIn("tsmaster_app=MyApp", text)
        self.assertIn("tsmaster_hw_channel=3", text)
