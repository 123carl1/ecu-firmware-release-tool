from __future__ import annotations

from datetime import datetime
from itertools import count
from pathlib import Path
from typing import TextIO

from unified_can_lin_host_tool.core.events import TraceEvent

_TRACE_FILE_COUNTER = count()


class TraceLogger:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        suffix = next(_TRACE_FILE_COUNTER)
        self.path = log_dir / f"trace_{datetime.now():%Y%m%d_%H%M%S_%f}_{suffix:04d}.log"
        self._file: TextIO | None = self.path.open("w", encoding="utf-8", newline="\n")

    def write(self, event: TraceEvent) -> None:
        if self._file is None:
            raise RuntimeError("trace logger is closed")
        timestamp = event.timestamp.isoformat(timespec="milliseconds")
        data = " ".join(f"{value:02X}" for value in event.data)
        note = f" note={event.note}" if event.note else ""
        self._file.write(
            f"{timestamp} {event.direction} {event.bus} "
            f"id=0x{event.frame_id:02X} data={data}{note}\n"
        )
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
