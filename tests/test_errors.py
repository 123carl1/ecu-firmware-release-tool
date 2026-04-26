import unittest

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError


class ErrorTests(unittest.TestCase):
    def test_error_keeps_category_and_message(self):
        err = HostToolError(ErrorCategory.PROFILE, "缺少 NAD")

        self.assertEqual(err.category, ErrorCategory.PROFILE)
        self.assertIn("缺少 NAD", str(err))

