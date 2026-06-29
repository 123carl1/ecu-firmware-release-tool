import unittest
from pathlib import Path

from unified_can_lin_host_tool.profile import load_profile


class ProfileTests(unittest.TestCase):
    def test_load_e68_profile(self):
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))

        self.assertEqual(profile.bus.nad, 0x11)
        self.assertEqual(profile.bus.request_id, 0x3C)
        self.assertEqual(profile.bus.response_id, 0x3D)
        self.assertEqual(profile.uds.frame_gap_ms, 12)
        self.assertEqual(profile.uds.max_transfer_payload, 1024)

    def test_load_as5pr_can_profile(self):
        profile = load_profile(Path("profiles/as5pr_can_bootloader.yaml"))

        self.assertEqual(profile.bus.type, "CAN")
        self.assertEqual(profile.bus.request_id, 0x701)
        self.assertEqual(profile.bus.response_id, 0x709)
        self.assertEqual(profile.bus.functional_request_id, 0x7DF)
        self.assertIsNone(profile.bus.nad)
        self.assertEqual(profile.bus.padding, 0xAA)
        self.assertEqual(profile.uds.max_transfer_payload, 62)

    def test_profile_rejects_transfer_payload_that_exceeds_isotp_length(self):
        raw = {
            "name": "bad",
            "bus": {
                "type": "LIN",
                "baudrate": 19200,
                "request_id": 0x3C,
                "response_id": 0x3D,
                "nad": 0x11,
            },
            "memory": {
                "app_start": 0x7000,
                "app_size": 0x19000,
                "app_end": 0x20000,
                "flash_driver_ram": 0x20001000,
                "flash_driver_max_size": 0x2000,
                "page_size": 512,
            },
            "uds": {
                "p2_ms": 50,
                "p2_star_ms": 5000,
                "max_transfer_payload": 4094,
                "request_download_format": 0x44,
                "frame_gap_ms": 12,
                "poll_timeout_ms": 300,
                "poll_gap_ms": 20,
            },
            "seedkey": {"app_level1": "e68_level1", "boot_fbl": "e68_fbl"},
            "workflow": {"name": "e68_lin_bootloader_v1"},
        }

        with self.assertRaisesRegex(Exception, "max_transfer_payload"):
            load_profile(raw)

    def test_profile_rejects_missing_nad(self):
        raw = {
            "name": "bad",
            "bus": {
                "type": "LIN",
                "baudrate": 19200,
                "request_id": 0x3C,
                "response_id": 0x3D,
            },
            "memory": {
                "app_start": 0x7000,
                "app_size": 0x19000,
                "app_end": 0x20000,
                "flash_driver_ram": 0x20001000,
                "flash_driver_max_size": 0x2000,
                "page_size": 512,
            },
            "uds": {
                "p2_ms": 50,
                "p2_star_ms": 5000,
                "max_transfer_payload": 6,
                "request_download_format": 0x44,
                "frame_gap_ms": 12,
                "poll_timeout_ms": 300,
                "poll_gap_ms": 20,
            },
            "seedkey": {"app_level1": "e68_level1", "boot_fbl": "e68_fbl"},
            "workflow": {"name": "e68_lin_bootloader_v1"},
        }

        with self.assertRaisesRegex(Exception, "nad"):
            load_profile(raw)
