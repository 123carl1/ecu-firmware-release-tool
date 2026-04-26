import unittest

from unified_can_lin_host_tool.core.cancel import CancellationToken, OperationCancelled


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
