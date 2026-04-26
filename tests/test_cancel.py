import unittest

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.cancel import CancellationToken, OperationCancelled
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


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
