from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TraceEvent:
    direction: str
    frame_id: int
    data: bytes
    note: str = ""
    bus: str = "LIN"
    timestamp: datetime = field(default_factory=datetime.now)

