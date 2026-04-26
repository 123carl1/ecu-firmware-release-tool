import tempfile
import unittest
from pathlib import Path

from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.trace import TraceLogger


class TraceTests(unittest.TestCase):
    def test_trace_logger_writes_tx_rx(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(Path(tmp))
            logger.write(
                TraceEvent(
                    direction="TX",
                    frame_id=0x3C,
                    data=bytes.fromhex("02 02 10 01 FF FF FF FF"),
                    note="$10 01",
                )
            )
            logger.close()

            text = logger.path.read_text(encoding="utf-8")
            self.assertIn("TX", text)
            self.assertIn("0x3C", text)
            self.assertIn("02 02 10 01", text)

