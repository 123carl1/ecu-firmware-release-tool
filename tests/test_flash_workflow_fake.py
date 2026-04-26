import unittest
from pathlib import Path

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_bin_image
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


class FlashWorkflowFakeTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("profiles/e68_lin_bootloader.yaml")
        self.flash_driver = load_bin_image(
            Path("tests/fixtures/flash_driver_18b.bin"),
            self.profile.memory.flash_driver_ram,
            self.profile.memory.flash_driver_max_size,
        )
        self.app = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            self.profile.memory.app_start,
            self.profile.memory.app_size,
        )

    def test_full_flash_sequence_uses_diag_exclusive(self):
        session = BusSession()
        adapter = FakeLinAdapter.for_e68_flash_success(
            self.profile,
            flash_driver_data=self.flash_driver.data,
            app_data=self.app.data,
        )
        transport = LinDiagTransport(adapter, self.profile, sleep_func=lambda _: None)
        workflow = FlashWorkflow(self.profile, transport, session)

        result = workflow.run(flash_driver=self.flash_driver, app=self.app)

        self.assertTrue(result.success)
        self.assertFalse(session.is_diag_exclusive)
        uds_payloads = adapter.sent_uds_payloads()
        self.assertEqual(uds_payloads[0], bytes.fromhex("10 01"))
        self.assertIn(bytes.fromhex("31 01 02 03"), uds_payloads)
        self.assertIn(bytes.fromhex("11 01"), uds_payloads)

    def test_failure_releases_diag_exclusive(self):
        session = BusSession()
        adapter = FakeLinAdapter(responses=[])
        transport = LinDiagTransport(adapter, self.profile, sleep_func=lambda _: None)
        workflow = FlashWorkflow(self.profile, transport, session)

        with self.assertRaises(Exception):
            workflow.run(flash_driver=self.flash_driver, app=self.app)

        self.assertFalse(session.is_diag_exclusive)

