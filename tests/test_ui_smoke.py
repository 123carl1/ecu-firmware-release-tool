import os
import subprocess
import sys
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

from unified_can_lin_host_tool.ui import app as ui_app


class UiSmokeTest(unittest.TestCase):
    def test_ui_holds_product_mutex_for_qapplication_lifetime(self):
        events = []

        @contextmanager
        def mutex():
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

        class Application:
            @staticmethod
            def instance():
                return None

            def __init__(self, _argv):
                events.append("app")

            def processEvents(self):
                events.append("events")

        with (
            patch.object(ui_app, "product_run_mutex", mutex, create=True),
            patch.object(ui_app, "QApplication", Application),
            patch.object(ui_app, "ReleaseMainWindow", lambda: events.append("window")),
        ):
            self.assertEqual(ui_app.main(["--smoke"]), 0)

        self.assertEqual(events, ["enter", "app", "window", "events", "exit"])

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
