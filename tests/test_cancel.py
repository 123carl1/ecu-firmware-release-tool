import unittest
from pathlib import Path

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.cancel import CancellationToken, OperationCancelled
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_bin_image
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport, UdsResponse


class CancellationTokenTest(unittest.TestCase):
    def test_new_token_is_not_cancelled(self):
        token = CancellationToken()

        self.assertFalse(token.is_cancelled)

    def test_cancel_sets_flag(self):
        token = CancellationToken()

        token.cancel()

        self.assertTrue(token.is_cancelled)

    def test_throw_if_cancelled_raises_cancelled_exception(self):
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(OperationCancelled):
            token.throw_if_cancelled()


class LinDiagCancellationTest(unittest.TestCase):
    def test_request_polling_can_be_cancelled(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        token = CancellationToken()

        def cancel_on_sleep(_seconds):
            token.cancel()

        transport = LinDiagTransport(
            FakeLinAdapter(),
            profile,
            sleep_func=cancel_on_sleep,
        )

        with self.assertRaises(OperationCancelled):
            transport.request(bytes.fromhex("10 01"), cancel_token=token)


class FlashWorkflowCancellationTest(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("profiles/e68_lin_bootloader.yaml")
        self.flash_driver = load_bin_image(
            Path("tests/fixtures/flash_driver_18b.bin"),
            start_address=self.profile.memory.flash_driver_ram,
            max_size=self.profile.memory.flash_driver_max_size,
        )
        self.app = load_bin_image(
            Path("tests/fixtures/app_20b.bin"),
            start_address=self.profile.memory.app_start,
            max_size=self.profile.memory.app_size,
        )

    def test_flash_workflow_cancels_at_download_safe_point(self):
        adapter = FakeLinAdapter.for_e68_flash_success(
            self.profile,
            flash_driver_data=self.flash_driver.data,
            app_data=self.app.data,
        )
        token = CancellationToken()
        session = BusSession()
        request_count = 0

        class CancellingTransport(LinDiagTransport):
            def request(self, *args, **kwargs):
                nonlocal request_count
                request_count += 1
                if request_count == 12:
                    token.cancel()
                return super().request(*args, **kwargs)

        transport = CancellingTransport(adapter, self.profile, sleep_func=lambda _: None)
        workflow = FlashWorkflow(self.profile, transport, session)

        with self.assertRaises(OperationCancelled):
            workflow.run(flash_driver=self.flash_driver, app=self.app, cancel_token=token)

        self.assertFalse(session.is_diag_exclusive)

    def test_flash_workflow_cancels_during_boot_fbl_security_request(self):
        token = CancellationToken()
        session = BusSession()
        transport = BootFblSecurityCancellingTransport(token)
        workflow = FlashWorkflow(self.profile, transport, session)

        with self.assertRaises(OperationCancelled):
            workflow.run(flash_driver=self.flash_driver, app=self.app, cancel_token=token)

        self.assertFalse(session.is_diag_exclusive)


class BootFblSecurityCancellingTransport:
    def __init__(self, token: CancellationToken):
        self._token = token
        self._programming_session_seen = False

    def request(
        self,
        uds_payload: bytes,
        *,
        expect_sid=None,
        expect_prefix=None,
        timeout_ms=None,
        allow_response_pending=False,
        ignore_invalid_responses=False,
        cancel_token=None,
    ):
        self.assert_same_token(cancel_token)
        if uds_payload == bytes.fromhex("10 02"):
            self._programming_session_seen = True
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
            if self._programming_session_seen:
                self._token.cancel()
                self._token.throw_if_cancelled()
            return UdsResponse(payload=bytes.fromhex("67 09 24 68 35 79"), raw_frames=())

        raise AssertionError(f"unexpected UDS request before boot FBL security cancellation: {uds_payload.hex(' ')}")

    def assert_same_token(self, cancel_token):
        if cancel_token is not self._token:
            raise AssertionError("cancel_token was not propagated to transport")
