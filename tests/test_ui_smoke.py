import os
import subprocess
import sys
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

from unified_can_lin_host_tool.ui import app as ui_app


class UiSmokeTest(unittest.TestCase):
    def test_default_update_service_is_built_only_for_official_identity(self):
        development = ui_app.ToolIdentity("0.2.0", "development", "", "", False)
        self.assertIsNone(ui_app.build_default_update_service(development))
        repository_missing = ui_app.ToolIdentity(
            "0.2.0", "01" * 20, "2026-07-14T12:00:00Z", "", True
        )
        self.assertIsNone(ui_app.build_default_update_service(repository_missing))

        official = ui_app.ToolIdentity(
            "0.2.0", "01" * 20, "2026-07-14T12:00:00Z",
            "owner/ecu-firmware-release-tool", True,
        )
        with patch.dict(os.environ, {"LOCALAPPDATA": r"D:\Temp\update-cache"}), \
             patch.object(ui_app, "load_release_public_keys", return_value={"k": b"x" * 32}):
            service = ui_app.build_default_update_service(official)

        self.assertIsNotNone(service)
        self.assertEqual(service._cache_root, Path(r"D:\Temp\update-cache") / "EcuReleaseTool" / "updates")

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
            patch.object(
                ui_app,
                "ReleaseMainWindow",
                lambda **_kwargs: events.append("window"),
            ),
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
