import os
import subprocess
import sys
import unittest
from pathlib import Path


class UiSmokeTest(unittest.TestCase):
    def test_cli_ui_smoke_exits_offscreen(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path("src").resolve())
        env["QT_QPA_PLATFORM"] = "offscreen"

        result = subprocess.run(
            [sys.executable, "-m", "unified_can_lin_host_tool.cli.ui", "--smoke"],
            capture_output=True,
            env=env,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("UI SMOKE OK", result.stdout)
