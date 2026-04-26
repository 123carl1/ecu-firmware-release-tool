from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice, WorkerEvent

EventCallback = Callable[[WorkerEvent], None]


@runtime_checkable
class HostSession(Protocol):
    profile: ToolProfile

    def request_uds(
        self,
        payload: bytes,
        *,
        log_dir: Path | None = None,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> bytes:
        ...

    def flash_e68(
        self,
        *,
        flash_driver_path: Path,
        app_path: Path,
        log_dir: Path,
        dry_run: bool = True,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> list[WorkerEvent]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class HostBackend(Protocol):
    name: str

    def scan(self) -> list[UiDevice]:
        ...

    def connect(self, channel: UiChannel, profile: ToolProfile) -> HostSession:
        ...
