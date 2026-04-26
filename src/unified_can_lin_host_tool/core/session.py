from __future__ import annotations

from threading import Lock


class BusSession:
    def __init__(self) -> None:
        self._lock = Lock()
        self._diag_owner: str | None = None

    @property
    def is_diag_exclusive(self) -> bool:
        with self._lock:
            return self._diag_owner is not None

    def enter_diag_exclusive(self, owner: str) -> bool:
        with self._lock:
            if self._diag_owner is not None:
                return False
            self._diag_owner = owner
            return True

    def release_diag_exclusive(self, owner: str) -> None:
        with self._lock:
            if self._diag_owner != owner:
                raise RuntimeError(f"owner mismatch: current={self._diag_owner!r}, release={owner!r}")
            self._diag_owner = None

