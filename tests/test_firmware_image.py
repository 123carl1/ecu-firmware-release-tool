import unittest
from pathlib import Path

from unified_can_lin_host_tool.firmware.image import align_up, load_bin_image, split_transfer_chunks
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

