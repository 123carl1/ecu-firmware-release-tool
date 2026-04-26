import unittest


class UiThreadingTest(unittest.TestCase):
    def test_main_window_runs_worker_outside_ui_thread(self):
        try:
            from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal, Slot
            from PySide6.QtWidgets import QApplication
        except ModuleNotFoundError:
            self.skipTest("PySide6 is not installed")

        from unified_can_lin_host_tool.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        result = {}

        class DummyWorker(QObject):
            finished = Signal()

            @Slot()
            def run(self):
                result["ran_in_ui_thread"] = QThread.currentThread() == app.thread()
                self.finished.emit()

        window = MainWindow()
        worker = DummyWorker()
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        QTimer.singleShot(2000, loop.quit)

        window._start_worker(worker)
        loop.exec()
        window.close()

        self.assertIn("ran_in_ui_thread", result)
        self.assertFalse(result["ran_in_ui_thread"])
