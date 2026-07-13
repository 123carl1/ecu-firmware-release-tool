from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LinFrame:
    frame_id: int
    data: bytes


@dataclass(frozen=True)
class CanFrame:
    can_id: int
    data: bytes
    timestamp_us: int | None = None


class BusAdapter(Protocol):
    def send_lin_frame(self, frame_id: int, data: bytes) -> None:
        ...

    def receive_lin_frame(self, frame_id: int, timeout_ms: int) -> LinFrame | None:
        ...

    def send_can_frame(self, can_id: int, data: bytes) -> None:
        ...

    def receive_can_frame(self, can_id: int, timeout_ms: int) -> CanFrame | None:
        ...

