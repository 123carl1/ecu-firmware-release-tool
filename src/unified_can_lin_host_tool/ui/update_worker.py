"""在独立 Qt 线程中执行更新检查或安装包下载。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot


class UpdateWorker(QObject):
    """把更新操作结果转换为只由界面线程处理的 Qt 信号。"""

    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(
        self,
        operation: Callable[[Callable[[int, int], None]], object],
    ) -> None:
        super().__init__()
        self._operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation(self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
