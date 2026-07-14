import unittest

try:
    from PySide6.QtTest import QSignalSpy
except ModuleNotFoundError:
    raise unittest.SkipTest("PySide6 is not installed")

from unified_can_lin_host_tool.ui.update_worker import UpdateWorker


class UpdateWorkerTests(unittest.TestCase):
    def test_worker_emits_result_without_touching_widgets(self):
        worker = UpdateWorker(lambda progress: "result")
        signal = QSignalSpy(worker.succeeded)

        worker.run()

        self.assertEqual(signal.count(), 1)
        self.assertEqual(signal.at(0), ["result"])

    def test_worker_maps_exception_to_failed_signal(self):
        def fail(_progress):
            raise RuntimeError("network down")

        worker = UpdateWorker(fail)
        signal = QSignalSpy(worker.failed)

        worker.run()

        self.assertEqual(signal.count(), 1)
        self.assertEqual(signal.at(0), ["network down"])
