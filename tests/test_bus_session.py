import unittest

from unified_can_lin_host_tool.core.session import BusSession


class BusSessionTests(unittest.TestCase):
    def test_diag_exclusive_blocks_second_owner(self):
        session = BusSession()

        self.assertTrue(session.enter_diag_exclusive("uds"))
        self.assertFalse(session.enter_diag_exclusive("flash"))
        session.release_diag_exclusive("uds")
        self.assertTrue(session.enter_diag_exclusive("flash"))

    def test_wrong_owner_cannot_release(self):
        session = BusSession()
        session.enter_diag_exclusive("flash")

        with self.assertRaisesRegex(RuntimeError, "owner"):
            session.release_diag_exclusive("uds")

