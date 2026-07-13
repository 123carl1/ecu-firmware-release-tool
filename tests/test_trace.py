import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from pathlib import Path

from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.trace import TraceLogger, default_log_dir


class TraceTests(unittest.TestCase):
    def test_trace_logger_writes_tx_rx(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(Path(tmp))
            logger.write(
                TraceEvent(
                    direction="TX",
                    frame_id=0x3C,
                    data=bytes.fromhex("11 02 10 01 FF FF FF FF"),
                    note="$10 01",
                )
            )
            logger.close()

            text = logger.path.read_text(encoding="utf-8")
            self.assertEqual(logger.path.suffix, ".asc")
            self.assertIn("base hex  timestamps absolute", text)
            self.assertIn("3C Tx d 8", text)
            self.assertIn("11 02 10 01", text)
            self.assertIn("End TriggerBlock", text)

    def test_trace_logger_uses_unique_paths_within_same_second(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = TraceLogger(Path(tmp))
            second = TraceLogger(Path(tmp))
            try:
                self.assertNotEqual(first.path, second.path)
            finally:
                first.close()
                second.close()

    def test_default_log_dir_uses_local_app_data(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\Tester\AppData\Local"}):
            self.assertEqual(
                default_log_dir(),
                Path(r"C:\Users\Tester\AppData\Local\EcuReleaseTool\Logs"),
            )
