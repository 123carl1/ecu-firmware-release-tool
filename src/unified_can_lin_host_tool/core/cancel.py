from __future__ import annotations

from threading import Event


class OperationCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise OperationCancelled("operation cancelled")
