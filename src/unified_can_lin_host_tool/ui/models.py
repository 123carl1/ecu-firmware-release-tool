from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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


@dataclass(frozen=True)
class UiDevice:
    vendor: str
    name: str
    serial: str
    channels: list[UiChannel]
