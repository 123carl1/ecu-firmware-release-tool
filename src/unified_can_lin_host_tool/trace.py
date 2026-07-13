from __future__ import annotations

from datetime import datetime
from itertools import count
import os
from pathlib import Path
from typing import TextIO

from unified_can_lin_host_tool.core.events import TraceEvent


_TRACE_FILE_COUNTER = count()
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def default_log_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "EcuReleaseTool" / "Logs"
    return Path.home() / "AppData" / "Local" / "EcuReleaseTool" / "Logs"


class TraceLogger:
    """写 Vector/TSMaster 可直接打开的 classic CAN ASC 日志。"""

    def __init__(self, log_dir: Path | None = None, *, channel: int = 1) -> None:
        target_dir = Path(log_dir) if log_dir is not None else default_log_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = next(_TRACE_FILE_COUNTER)
        self.path = target_dir / f"AS5PR_OTA_{datetime.now():%Y%m%d_%H%M%S_%f}_{suffix:04d}.asc"
        self._file: TextIO | None = self.path.open("w", encoding="ascii", newline="\n")
        self._started = datetime.now()
        self._channel = channel
        weekday = _WEEKDAYS[self._started.weekday()]
        month = _MONTHS[self._started.month - 1]
        self._file.write(
            f"date {weekday} {month} {self._started.day:02d} "
            f"{self._started:%H:%M:%S.%f %Y}\n"
        )
        self._file.write("base hex  timestamps absolute\n")
        self._file.write("internal events logged\n")
        self._file.write("Begin Triggerblock\n")
        self._file.flush()

    def write(self, event: TraceEvent) -> None:
        if self._file is None:
            raise RuntimeError("trace logger is closed")
        timestamp = max(0.0, (event.timestamp - self._started).total_seconds())
        direction = "Tx" if event.direction.upper() == "TX" else "Rx"
        data = " ".join(f"{value:02X}" for value in event.data)
        self._file.write(
            f"{timestamp:12.6f} {self._channel} {event.frame_id:X} "
            f"{direction} d {len(event.data)} {data}\n"
        )
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.write("End TriggerBlock\n")
            self._file.close()
            self._file = None
