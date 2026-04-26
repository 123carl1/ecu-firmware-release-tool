from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from unified_can_lin_host_tool.core.events import TraceEvent


class ConnectionState(str, Enum):
    IDLE = "idle"
    SCANNED = "scanned"
    CONNECTED = "connected"
    BUSY = "busy"
    ERROR = "error"


@dataclass(frozen=True)
class UiChannel:
    vendor: str
    device_name: str
    channel_name: str
    bus: str
    channel_index: int
    mapping: dict[str, str | int | float | bool] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class UiDevice:
    vendor: str
    name: str
    serial: str
    channels: list[UiChannel]


@dataclass(frozen=True)
class WorkerEvent:
    kind: str
    message: str
    progress: int | None = None
    trace: TraceEvent | None = None
    timestamp: datetime = field(default_factory=datetime.now)
