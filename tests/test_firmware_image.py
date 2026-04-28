import tempfile
import unittest
from pathlib import Path

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.firmware.image import align_up, load_bin_image, load_firmware_image, split_transfer_chunks
from unified_can_lin_host_tool.profile import load_profile


class FirmwareImageTests(unittest.TestCase):
    def test_load_app_bin_uses_profile_start(self):
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        image = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            start_address=profile.memory.app_start,
            max_size=profile.memory.app_size,
        )

        self.assertEqual(image.start_address, 0x7000)
        self.assertEqual(image.size, 20)

    def test_align_erase_length_to_page(self):
        self.assertEqual(align_up(20, 512), 512)
        self.assertEqual(align_up(1024, 512), 1024)

    def test_split_transfer_chunks_uses_payload_size_6(self):
        chunks = list(split_transfer_chunks(bytes(range(14)), max_payload=6))

        self.assertEqual(chunks, [bytes(range(6)), bytes(range(6, 12)), bytes(range(12, 14))])

    def test_load_s19_extracts_target_region_and_ignores_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flash_driver.s19"
            payload = bytes(range(18))
            metadata = b"001"
            path.write_text(
                "\n".join(
                    [
                        _srec("S3", 0x20001000, payload[:8]),
                        _srec("S3", 0x20001008, payload[8:]),
                        _srec("S3", 0x1FFFFFC0, metadata),
                        _srec("S7", 0x20001000, b""),
                    ]
                )
                + "\n",
                encoding="ascii",
            )

            image = load_firmware_image(path, start_address=0x20001000, max_size=0x2000)

        self.assertEqual(image.start_address, 0x20001000)
        self.assertEqual(image.data, payload)

    def test_load_s19_rejects_bad_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.s19"
            line = _srec("S3", 0x00007000, b"\x01\x02\x03")
            path.write_text(line[:-2] + "00\n", encoding="ascii")

            with self.assertRaises(HostToolError) as caught:
                load_firmware_image(path, start_address=0x00007000, max_size=0x19000)

        self.assertEqual(caught.exception.category, ErrorCategory.FILE)

    def test_load_s19_rejects_non_contiguous_target_region(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gap.s19"
            path.write_text(
                "\n".join(
                    [
                        _srec("S3", 0x00007000, b"\x01\x02"),
                        _srec("S3", 0x00007004, b"\x03\x04"),
                        _srec("S7", 0x00007000, b""),
                    ]
                )
                + "\n",
                encoding="ascii",
            )

            with self.assertRaises(HostToolError) as caught:
                load_firmware_image(path, start_address=0x00007000, max_size=0x19000)

        self.assertEqual(caught.exception.category, ErrorCategory.FILE)


def _srec(record_type: str, address: int, data: bytes) -> str:
    address_len = {"S1": 2, "S2": 3, "S3": 4, "S7": 4, "S8": 3, "S9": 2}[record_type]
    count = address_len + len(data) + 1
    address_bytes = address.to_bytes(address_len, "big")
    checksum = (~((count + sum(address_bytes) + sum(data)) & 0xFF)) & 0xFF
    return f"{record_type}{count:02X}{address_bytes.hex().upper()}{data.hex().upper()}{checksum:02X}"
