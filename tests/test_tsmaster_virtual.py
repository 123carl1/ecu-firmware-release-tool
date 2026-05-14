import unittest
from pathlib import Path

from unified_can_lin_host_tool.adapters.tsmaster_virtual import (
    E68FlashResponsePlan,
    LinUdsRequestAssembler,
)
from unified_can_lin_host_tool.cli.tsmaster_can_virtual_loopback import build_parser as build_can_parser
from unified_can_lin_host_tool.cli.tsmaster_lin_fifo_sim_uds import build_parser as build_lin_sim_parser
from unified_can_lin_host_tool.e68.crc32 import e68_crc32
from unified_can_lin_host_tool.firmware.image import load_bin_image
from unified_can_lin_host_tool.profile import load_profile


class TsmasterVirtualTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("profiles/e68_lin_bootloader.yaml")

    def test_lin_uds_request_assembler_rebuilds_multi_frame_payload(self):
        assembler = LinUdsRequestAssembler(request_id=0x3C, nad=0x11)

        first = assembler.feed_frame(0x3C, bytes.fromhex("11 10 08 36 01 01 02 03"))
        second = assembler.feed_frame(0x3C, bytes.fromhex("11 21 04 05 06 FF FF FF"))

        self.assertIsNone(first)
        self.assertEqual(second, bytes.fromhex("36 01 01 02 03 04 05 06"))

    def test_e68_flash_response_plan_returns_pending_then_final_for_erase(self):
        flash_driver = load_bin_image(
            Path("tests/fixtures/flash_driver_18b.bin"),
            self.profile.memory.flash_driver_ram,
            self.profile.memory.flash_driver_max_size,
        )
        app = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            self.profile.memory.app_start,
            self.profile.memory.app_size,
        )
        plan = E68FlashResponsePlan(self.profile, flash_driver_data=flash_driver.data, app_data=app.data)

        responses = plan.responses_for(bytes.fromhex("31 01 FF 00 00 00 70 00 00 00 02 00"))

        self.assertEqual(responses, [bytes.fromhex("7F 31 78"), bytes.fromhex("71 01 FF 00")])

    def test_e68_flash_response_plan_returns_pending_then_final_for_app_check(self):
        flash_driver = load_bin_image(
            Path("tests/fixtures/flash_driver_18b.bin"),
            self.profile.memory.flash_driver_ram,
            self.profile.memory.flash_driver_max_size,
        )
        app = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            self.profile.memory.app_start,
            self.profile.memory.app_size,
        )
        plan = E68FlashResponsePlan(self.profile, flash_driver_data=flash_driver.data, app_data=app.data)

        responses = plan.responses_for(bytes.fromhex("31 01 FF 01"))

        self.assertEqual(responses, [bytes.fromhex("7F 31 78"), bytes.fromhex("71 01 FF 01 00")])

    def test_e68_flash_response_plan_returns_app_did_after_reset(self):
        flash_driver = load_bin_image(
            Path("tests/fixtures/flash_driver_18b.bin"),
            self.profile.memory.flash_driver_ram,
            self.profile.memory.flash_driver_max_size,
        )
        app = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            self.profile.memory.app_start,
            self.profile.memory.app_size,
        )
        plan = E68FlashResponsePlan(self.profile, flash_driver_data=flash_driver.data, app_data=app.data)

        responses = plan.responses_for(bytes.fromhex("22 30 00"))

        self.assertEqual(responses, [bytes.fromhex("62 30 00 30 30 30")])

    def test_e68_flash_response_plan_validates_transfer_crc(self):
        flash_driver = load_bin_image(
            Path("tests/fixtures/flash_driver_18b.bin"),
            self.profile.memory.flash_driver_ram,
            self.profile.memory.flash_driver_max_size,
        )
        app = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            self.profile.memory.app_start,
            self.profile.memory.app_size,
        )
        plan = E68FlashResponsePlan(self.profile, flash_driver_data=flash_driver.data, app_data=app.data)

        plan.responses_for(bytes.fromhex("34 00 44") + self.profile.memory.flash_driver_ram.to_bytes(4, "big") + len(flash_driver.data).to_bytes(4, "big"))
        for sequence, offset in enumerate(range(0, len(flash_driver.data), self.profile.uds.max_transfer_payload), start=1):
            chunk = flash_driver.data[offset : offset + self.profile.uds.max_transfer_payload]
            plan.responses_for(bytes([0x36, sequence]) + chunk)
        responses = plan.responses_for(bytes([0x37]) + e68_crc32(flash_driver.data).to_bytes(4, "big"))

        self.assertEqual(responses, [bytes([0x77]) + e68_crc32(flash_driver.data).to_bytes(4, "big")])

    def test_cli_parsers_expose_safe_defaults(self):
        can_args = build_can_parser().parse_args([])
        lin_args = build_lin_sim_parser().parse_args([])

        self.assertEqual(can_args.device, "vector")
        self.assertEqual(lin_args.request, "10 01")
        self.assertEqual(lin_args.response, "50 01")
        self.assertIn("sim", build_lin_sim_parser().description.lower())


if __name__ == "__main__":
    unittest.main()
