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

    def test_fake_dry_run_accepts_s19_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flash_s19 = root / "flash_driver.s19"
            app_s19 = root / "app.s19"
            _write_s19(flash_s19, 0x20001000, Path("tests/fixtures/flash_driver_18b.bin").read_bytes())
            _write_s19(app_s19, 0x00007000, Path("tests/fixtures/app_20b.bin").read_bytes())
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--adapter",
                        "fake",
                        "--profile",
                        "profiles/e68_lin_bootloader.yaml",
                        "--flash-driver",
                        str(flash_s19),
                        "--app",
                        str(app_s19),
                        "--log-dir",
                        str(root / "logs"),
                        "--dry-run",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("FLASH SUCCESS", output.getvalue())

    def test_fake_dry_run_start_in_bootloader_skips_app_preprogramming(self):
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
                        "--start-in-bootloader",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("start_in_bootloader=True", output.getvalue())
            log_text = next(Path(tmp).glob("trace_*.log")).read_text(encoding="utf-8")
            self.assertIn("data=02 02 10 02", log_text)
            self.assertNotIn("data=02 02 27 01", log_text)

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
                    "--tsmaster-project-dir",
                    "D:/project/TS_Master",
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
        self.assertIn("tsmaster_project_dir=D:\\project\\TS_Master", text)
        self.assertIn("tsmaster_hw_channel=3", text)
        self.assertIn("tsmaster_close_mode=skip", text)


def _write_s19(path: Path, base_address: int, data: bytes) -> None:
    lines = []
    for offset in range(0, len(data), 16):
        lines.append(_srec("S3", base_address + offset, data[offset : offset + 16]))
    lines.append(_srec("S7", base_address, b""))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _srec(record_type: str, address: int, data: bytes) -> str:
    address_len = {"S3": 4, "S7": 4}[record_type]
    count = address_len + len(data) + 1
    address_bytes = address.to_bytes(address_len, "big")
    checksum = (~((count + sum(address_bytes) + sum(data)) & 0xFF)) & 0xFF
    return f"{record_type}{count:02X}{address_bytes.hex().upper()}{data.hex().upper()}{checksum:02X}"
