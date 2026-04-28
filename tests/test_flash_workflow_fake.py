import unittest
from pathlib import Path

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import FirmwareImage, load_bin_image
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport, UdsResponse


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
        workflow = FlashWorkflow(self.profile, transport, session, sleep_func=lambda _: None)

        result = workflow.run(flash_driver=self.flash_driver, app=self.app)

        self.assertTrue(result.success)
        self.assertFalse(session.is_diag_exclusive)
        uds_payloads = adapter.sent_uds_payloads()
        self.assertEqual(uds_payloads[0], bytes.fromhex("10 01"))
        self.assertIn(bytes.fromhex("31 01 02 03"), uds_payloads)
        self.assertIn(bytes.fromhex("11 01"), uds_payloads)
        self.assertEqual(uds_payloads[-1], bytes.fromhex("22 30 00"))

    def test_failure_releases_diag_exclusive(self):
        session = BusSession()
        adapter = FakeLinAdapter(responses=[])
        transport = LinDiagTransport(adapter, self.profile, sleep_func=lambda _: None)
        workflow = FlashWorkflow(self.profile, transport, session, sleep_func=lambda _: None)

        with self.assertRaises(Exception):
            workflow.run(flash_driver=self.flash_driver, app=self.app)

        self.assertFalse(session.is_diag_exclusive)

    def test_retries_boot_programming_session_until_boot_is_ready(self):
        session = BusSession()
        transport = BootDelayedTransport(self.profile)
        workflow = FlashWorkflow(self.profile, transport, session, sleep_func=lambda _: None)

        result = workflow.run(flash_driver=self.flash_driver, app=self.app)

        self.assertTrue(result.success)
        self.assertEqual(transport.boot_session_attempts, 3)
        self.assertTrue(transport.app_check_pending_allowed)
        self.assertEqual(transport.app_check_timeout_ms, self.profile.uds.p2_star_ms)
        self.assertFalse(session.is_diag_exclusive)

    def test_flash_workflow_emits_human_readable_progress_stages(self):
        session = BusSession()
        adapter = FakeLinAdapter.for_e68_flash_success(
            self.profile,
            flash_driver_data=self.flash_driver.data,
            app_data=self.app.data,
        )
        transport = LinDiagTransport(adapter, self.profile, sleep_func=lambda _: None)
        progress_events = []
        workflow = FlashWorkflow(
            self.profile,
            transport,
            session,
            sleep_func=lambda _: None,
            progress_callback=progress_events.append,
        )

        workflow.run(flash_driver=self.flash_driver, app=self.app)

        messages = [event.message for event in progress_events]
        percents = [event.percent for event in progress_events]
        self.assertIn("进入 App 默认会话", messages)
        self.assertIn("等待 Boot 编程会话", messages)
        self.assertIn("擦除 App 区域", messages)
        self.assertIn("等待 App DID 恢复通信", messages)
        self.assertEqual(percents[0], 5)
        self.assertEqual(percents[-1], 100)
        self.assertTrue(all(0 <= percent <= 100 for percent in percents))

    def test_app_download_progress_reports_block_position(self):
        session = BusSession()
        adapter = FakeLinAdapter.for_e68_flash_success(
            self.profile,
            flash_driver_data=self.flash_driver.data,
            app_data=self.app.data,
        )
        transport = LinDiagTransport(adapter, self.profile, sleep_func=lambda _: None)
        progress_events = []
        workflow = FlashWorkflow(
            self.profile,
            transport,
            session,
            sleep_func=lambda _: None,
            progress_callback=progress_events.append,
        )

        workflow.run(flash_driver=self.flash_driver, app=self.app)

        app_events = [event for event in progress_events if event.stage == "下载 App" and event.total is not None]
        self.assertTrue(app_events)
        self.assertEqual(app_events[-1].current, app_events[-1].total)
        self.assertIn("block", app_events[-1].message)
        self.assertIn("bytes", app_events[-1].message)

    def test_request_download_rejects_too_small_ecu_block_length(self):
        class TooSmallBlockTransport(BootDelayedTransport):
            def request(
                self,
                uds_payload: bytes,
                *,
                expect_sid=None,
                expect_prefix=None,
                timeout_ms=None,
                allow_response_pending=False,
                cancel_token=None,
            ):
                if uds_payload.startswith(bytes.fromhex("34")):
                    return UdsResponse(payload=bytes.fromhex("74 20 00 06"), raw_frames=())
                return super().request(
                    uds_payload,
                    expect_sid=expect_sid,
                    expect_prefix=expect_prefix,
                    timeout_ms=timeout_ms,
                    allow_response_pending=allow_response_pending,
                    cancel_token=cancel_token,
                )

        session = BusSession()
        transport = TooSmallBlockTransport(self.profile)
        workflow = FlashWorkflow(self.profile, transport, session, sleep_func=lambda _: None)

        with self.assertRaisesRegex(HostToolError, "maxNumberOfBlockLength"):
            workflow.run(flash_driver=self.flash_driver, app=self.app)

    def test_app_image_is_split_into_1024_byte_transfer_data_chunks(self):
        session = BusSession()
        transport = BootDelayedTransport(self.profile)
        workflow = FlashWorkflow(self.profile, transport, session, sleep_func=lambda _: None)
        app = FirmwareImage(
            path=Path("app_2050.bin"),
            start_address=self.profile.memory.app_start,
            data=bytes([0x5A]) * 2050,
        )

        result = workflow.run(flash_driver=self.flash_driver, app=app)

        self.assertTrue(result.success)
        transfer_lengths = [len(req) for req in transport.requests if req.startswith(bytes([0x36]))]
        self.assertEqual(transfer_lengths[-3:], [1026, 1026, 4])


class BootDelayedTransport:
    def __init__(self, profile):
        self.profile = profile
        self.requests: list[bytes] = []
        self._app_programming_session_seen = False
        self.boot_session_attempts = 0
        self.app_check_pending_allowed = False
        self.app_check_timeout_ms: int | None = None

    def request(
        self,
        uds_payload: bytes,
        *,
        expect_sid=None,
        expect_prefix=None,
        timeout_ms=None,
        allow_response_pending=False,
        cancel_token=None,
    ):
        self.requests.append(uds_payload)

        if uds_payload == bytes.fromhex("10 02"):
            if not self._app_programming_session_seen:
                self._app_programming_session_seen = True
                return UdsResponse(payload=bytes.fromhex("50 02"), raw_frames=())
            self.boot_session_attempts += 1
            if self.boot_session_attempts < 3:
                raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS response timeout")
            return UdsResponse(payload=bytes.fromhex("50 02"), raw_frames=())

        if uds_payload == bytes.fromhex("10 01"):
            return UdsResponse(payload=bytes.fromhex("50 01"), raw_frames=())
        if uds_payload == bytes.fromhex("10 03"):
            return UdsResponse(payload=bytes.fromhex("50 03"), raw_frames=())
        if uds_payload == bytes.fromhex("27 01"):
            return UdsResponse(payload=bytes.fromhex("67 01 35 79 24 68"), raw_frames=())
        if uds_payload.startswith(bytes.fromhex("27 02")):
            return UdsResponse(payload=bytes.fromhex("67 02"), raw_frames=())
        if uds_payload == bytes.fromhex("31 01 02 03"):
            return UdsResponse(payload=bytes.fromhex("71 01 02 03 00"), raw_frames=())
        if uds_payload == bytes.fromhex("27 09"):
            return UdsResponse(payload=bytes.fromhex("67 09 24 68 35 79"), raw_frames=())
        if uds_payload.startswith(bytes.fromhex("27 0A")):
            return UdsResponse(payload=bytes.fromhex("67 0A"), raw_frames=())
        if uds_payload.startswith(bytes.fromhex("34")):
            max_number = self.profile.uds.max_transfer_payload + 2
            return UdsResponse(payload=bytes([0x74, 0x20]) + max_number.to_bytes(2, "big"), raw_frames=())
        if uds_payload.startswith(bytes.fromhex("36")):
            return UdsResponse(payload=bytes([0x76, uds_payload[1]]), raw_frames=())
        if uds_payload.startswith(bytes.fromhex("37")):
            return UdsResponse(payload=bytes([0x77]) + uds_payload[1:5], raw_frames=())
        if uds_payload == bytes.fromhex("31 01 02 02"):
            return UdsResponse(payload=bytes.fromhex("71 01 02 02 00"), raw_frames=())
        if uds_payload.startswith(bytes.fromhex("31 01 FF 00")):
            return UdsResponse(payload=bytes.fromhex("71 01 FF 00"), raw_frames=())
        if uds_payload == bytes.fromhex("31 01 FF 01"):
            self.app_check_pending_allowed = allow_response_pending
            self.app_check_timeout_ms = timeout_ms
            return UdsResponse(payload=bytes.fromhex("71 01 FF 01 00"), raw_frames=())
        if uds_payload == bytes.fromhex("11 01"):
            return UdsResponse(payload=bytes.fromhex("51 01"), raw_frames=())
        if uds_payload == bytes.fromhex("22 30 00"):
            return UdsResponse(payload=bytes.fromhex("62 30 00 30 30 30"), raw_frames=())

        raise AssertionError(f"unexpected UDS request: {uds_payload.hex(' ')}")
