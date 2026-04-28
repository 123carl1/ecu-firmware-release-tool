from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from unified_can_lin_host_tool.backends.base import HostBackend, HostSession
from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
from unified_can_lin_host_tool.backends.settings import BackendSettings, default_backend_settings
from unified_can_lin_host_tool.backends.tsmaster_backend import TsmasterHostBackend
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice, WorkerEvent
from unified_can_lin_host_tool.ui.workers import ConnectWorker, DeviceScanWorker, FlashWorker, UdsWorker

DEFAULT_FLASH_DRIVER_PATH = Path(
    "D:/01_WorkProgram/Company_Program/10_AI_Adapted_Seat/DAU_FM33_HT/artifacts/release/e68_flash_driver_auth.s19"
)
DEFAULT_APP_PATH = Path("D:/01_WorkProgram/Company_Program/10_AI_Adapted_Seat/DAU_FM33_HT/artifacts/release/dau_fm33_auth.s19")
FIRMWARE_FILE_FILTER = "S19 Files (*.s19 *.srec *.mot);;Binary Files (*.bin);;All Files (*)"


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        backends: dict[str, HostBackend] | None = None,
        backend_settings: BackendSettings | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Unified CAN/LIN Host Tool - M1 Alpha")
        self.resize(1180, 760)

        self._backend_settings = backend_settings or default_backend_settings()
        if backends is None:
            self._backends = {
                "TSMaster": TsmasterHostBackend(settings=self._backend_settings.tsmaster),
                "Fake": FakeHostBackend(),
            }
        else:
            self._backends = backends
        if not self._backends:
            raise ValueError("at least one backend must be registered")
        self._backend_name = _default_backend_name(self._backends)
        self._backend = self._backends[self._backend_name]
        self._profile_path = Path("profiles/e68_lin_bootloader.yaml")
        self._profile = load_profile(self._profile_path)
        self._session: HostSession | None = None
        self._selected_channel: UiChannel | None = None
        self._active_threads: list[QThread] = []
        self._active_workers: list[QObject] = []

        self._build_ui()
        self._apply_style()
        self._set_connected(False)
        self._append_log("INFO", "UI ready")

    def _build_ui(self) -> None:
        root_splitter = QSplitter(Qt.Orientation.Horizontal)
        root_splitter.addWidget(self._build_left_panel())
        root_splitter.addWidget(self._build_right_panel())
        root_splitter.setStretchFactor(0, 0)
        root_splitter.setStretchFactor(1, 1)
        root_splitter.setSizes([280, 900])
        self.setCentralWidget(root_splitter)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.profile_combo = QComboBox()
        self.profile_combo.addItem("E68 LIN Bootloader", str(self._profile_path))
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(list(self._backends.keys()))
        self.backend_combo.setCurrentText(self._backend_name)
        self.backend_combo.currentTextChanged.connect(self._on_backend_changed)

        form = QFormLayout()
        form.addRow("Profile", self.profile_combo)
        form.addRow("后端", self.backend_combo)
        layout.addLayout(form)

        self.scan_button = QPushButton("扫描")
        self.scan_button.clicked.connect(self._on_scan_clicked)
        layout.addWidget(self.scan_button)

        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderLabels(["设备/通道"])
        self.device_tree.itemSelectionChanged.connect(self._on_device_selection_changed)
        layout.addWidget(self.device_tree, 1)

        self.connect_button = QPushButton("连接")
        self.connect_button.clicked.connect(self._on_connect_clicked)
        layout.addWidget(self.connect_button)

        self.status_label = QLabel("未扫描")
        layout.addWidget(self.status_label)
        return panel

    def _build_right_panel(self) -> QWidget:
        vertical = QSplitter(Qt.Orientation.Vertical)
        vertical.addWidget(self._build_tabs())
        vertical.addWidget(self._build_trace_log())
        vertical.setStretchFactor(0, 1)
        vertical.setStretchFactor(1, 0)
        vertical.setSizes([520, 220])
        return vertical

    def _build_tabs(self) -> QTabWidget:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_bus_tab(), "总线收发")
        self.tabs.addTab(self._build_uds_tab(), "UDS 诊断")
        self.tabs.addTab(self._build_flash_tab(), "E68 刷写")
        self.tabs.addTab(self._build_summary_tab(), "配置摘要")
        return self.tabs

    def _build_bus_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        self.lin_id_edit = QLineEdit("0x3C")
        self.lin_data_edit = QLineEdit("02 02 10 01 FF FF FF FF")
        self.bus_send_button = QPushButton("发送")
        self.bus_send_button.setEnabled(False)
        layout.addRow("总线", QLabel("LIN"))
        layout.addRow("ID", self.lin_id_edit)
        layout.addRow("数据", self.lin_data_edit)
        layout.addRow("", self.bus_send_button)
        return tab

    def _build_uds_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.uds_payload_edit = QLineEdit("10 01")
        self.uds_send_button = QPushButton("发送")
        self.uds_send_button.clicked.connect(self._on_uds_send_clicked)
        row.addWidget(QLabel("Payload"))
        row.addWidget(self.uds_payload_edit, 1)
        row.addWidget(self.uds_send_button)
        layout.addLayout(row)
        self.uds_response = QPlainTextEdit()
        self.uds_response.setReadOnly(True)
        layout.addWidget(self.uds_response, 1)
        return tab

    def _build_flash_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.flash_driver_edit = QLineEdit(str(DEFAULT_FLASH_DRIVER_PATH))
        self.app_edit = QLineEdit(str(DEFAULT_APP_PATH))
        self.firmware_file_filter = FIRMWARE_FILE_FILTER
        self.flash_driver_browse_button = QPushButton("浏览...")
        self.flash_driver_browse_button.clicked.connect(self._on_browse_flash_driver_clicked)
        self.app_browse_button = QPushButton("浏览...")
        self.app_browse_button.clicked.connect(self._on_browse_app_clicked)
        self.use_fixture_button = QPushButton("使用测试 fixture")
        self.use_fixture_button.clicked.connect(self._on_use_fixture_clicked)
        self.flash_start_button = QPushButton("开始刷写")
        self.flash_start_button.clicked.connect(self._on_flash_start_clicked)
        self.flash_progress = QProgressBar()
        self.flash_progress.setRange(0, 100)
        self.flash_progress.setTextVisible(True)
        self.flash_progress.setFormat("%p%")
        self.flash_status_label = QLabel("等待刷写")
        self.flash_status_label.setObjectName("flashStatusLabel")

        form = QFormLayout()
        form.addRow("FlashDriver", _path_row(self.flash_driver_edit, self.flash_driver_browse_button))
        form.addRow("App", _path_row(self.app_edit, self.app_browse_button))
        layout.addLayout(form)
        layout.addWidget(self.use_fixture_button)
        layout.addWidget(self.flash_start_button)
        layout.addWidget(self.flash_status_label)
        layout.addWidget(self.flash_progress)
        self.flash_stage_log = QPlainTextEdit()
        self.flash_stage_log.setReadOnly(True)
        self.flash_stage_log.setFont(QFont("Cascadia Mono", 10))
        layout.addWidget(self.flash_stage_log, 1)
        return tab

    def _build_summary_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.addRow("name", QLabel(self._profile.name))
        layout.addRow("baudrate", QLabel(str(self._profile.bus.baudrate)))
        layout.addRow("NAD", QLabel(f"0x{self._profile.bus.nad:02X}"))
        layout.addRow("request_id", QLabel(f"0x{self._profile.bus.request_id:02X}"))
        layout.addRow("response_id", QLabel(f"0x{self._profile.bus.response_id:02X}"))
        layout.addRow("app_start", QLabel(f"0x{self._profile.memory.app_start:08X}"))
        self.config_summary_text = QPlainTextEdit()
        self.config_summary_text.setReadOnly(True)
        self.config_summary_text.setPlainText("\n".join(self._backend_settings.summary_lines()))
        layout.addRow("backend", self.config_summary_text)
        return tab

    def _build_trace_log(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        button_row = QHBoxLayout()
        clear_button = QPushButton("清空")
        clear_button.clicked.connect(lambda: self.trace_log.clear())
        button_row.addWidget(QLabel("Trace Log"))
        button_row.addStretch(1)
        button_row.addWidget(clear_button)
        layout.addLayout(button_row)
        self.trace_log = QPlainTextEdit()
        self.trace_log.setReadOnly(True)
        self.trace_log.setFont(QFont("Cascadia Mono", 10))
        layout.addWidget(self.trace_log)
        return panel

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #F6F8FB;
            }
            QLineEdit, QComboBox, QTreeWidget, QPlainTextEdit {
                border: 1px solid #D7DDE6;
                border-radius: 4px;
                background: #FFFFFF;
                selection-background-color: #2F80ED;
            }
            QPushButton {
                min-height: 24px;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                background: #FFFFFF;
                padding: 3px 10px;
            }
            QPushButton:hover {
                background: #F1F5F9;
            }
            QPushButton:disabled {
                color: #94A3B8;
                background: #F8FAFC;
            }
            QTabWidget::pane {
                border: 1px solid #E2E8F0;
                background: #FFFFFF;
            }
            QProgressBar {
                height: 16px;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                background: #EEF2F7;
                text-align: center;
                font-weight: 600;
            }
            QProgressBar::chunk {
                border-radius: 3px;
                background-color: #2F80ED;
            }
            QLabel#flashStatusLabel {
                min-height: 26px;
                color: #0F172A;
                font-weight: 600;
                padding: 2px 0;
            }
            """
        )

    def _start_worker(self, worker: QObject) -> None:
        self._set_operation_controls_enabled(False)
        thread = QThread(self)
        self._active_threads.append(thread)
        self._active_workers.append(worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda worker=worker: self._remove_worker(worker))
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda thread=thread: self._remove_thread(thread))
        thread.finished.connect(self._refresh_operation_controls)
        thread.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._stop_active_threads():
            self.status_label.setText("正在取消后台任务")
            event.ignore()
            return
        super().closeEvent(event)

    def _stop_active_threads(self, wait_ms: int = 2000) -> bool:
        for worker in list(self._active_workers):
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except RuntimeError:
                    continue

        threads = list(self._active_threads)
        running_threads: list[QThread] = []
        for thread in threads:
            try:
                if thread.isRunning():
                    thread.requestInterruption()
                    thread.quit()
                    running_threads.append(thread)
            except RuntimeError:
                continue
        for thread in running_threads:
            try:
                thread.wait(wait_ms)
            except RuntimeError:
                continue
        self._active_threads = [thread for thread in self._active_threads if _is_thread_running(thread)]
        if not self._active_threads:
            self._active_workers.clear()
        self._refresh_operation_controls()
        return not self._active_threads

    def _remove_worker(self, worker: QObject) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def _remove_thread(self, thread: QThread) -> None:
        if thread in self._active_threads:
            self._active_threads.remove(thread)

    def _refresh_operation_controls(self) -> None:
        self._set_operation_controls_enabled(not self._active_threads)

    def _set_operation_controls_enabled(self, enabled: bool) -> None:
        self.profile_combo.setEnabled(enabled)
        self.backend_combo.setEnabled(enabled)
        self.scan_button.setEnabled(enabled)
        self.connect_button.setEnabled(enabled)

    def _on_backend_changed(self, name: str) -> None:
        if name not in self._backends:
            return
        if self._session is not None:
            self._session.close()
        self._backend_name = name
        self._backend = self._backends[name]
        self._session = None
        self._set_connected(False)
        self.device_tree.clear()
        self.status_label.setText(f"后端: {name}")

    def _on_scan_clicked(self) -> None:
        self.scan_button.setEnabled(False)
        self.status_label.setText("扫描中")
        worker = DeviceScanWorker(self._backend)
        worker.result.connect(self._populate_devices)
        worker.failed.connect(self._show_error)
        worker.finished.connect(lambda: self.scan_button.setEnabled(True))
        self._start_worker(worker)

    def _populate_devices(self, devices: list[UiDevice]) -> None:
        self.device_tree.clear()
        for device in devices:
            device_item = QTreeWidgetItem([f"{device.vendor} - {device.name}"])
            for channel in device.channels:
                channel_item = QTreeWidgetItem([f"{channel.channel_name} ({channel.bus})"])
                channel_item.setData(0, Qt.ItemDataRole.UserRole, channel)
                device_item.addChild(channel_item)
            self.device_tree.addTopLevelItem(device_item)
            device_item.setExpanded(True)
        self.status_label.setText("已扫描")
        self._append_log("INFO", f"扫描到 {len(devices)} 个工具")

    def _on_device_selection_changed(self) -> None:
        item = self.device_tree.currentItem()
        channel = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        self._selected_channel = channel if isinstance(channel, UiChannel) else None

    def _on_connect_clicked(self) -> None:
        if self._selected_channel is None:
            self._show_error("请先选择 LIN 通道")
            return
        self.connect_button.setEnabled(False)
        self.status_label.setText("连接中")
        worker = ConnectWorker(self._backend, self._selected_channel, self._profile)
        worker.result.connect(self._on_connected)
        worker.failed.connect(self._show_error)
        worker.finished.connect(lambda: self.connect_button.setEnabled(True))
        self._start_worker(worker)

    def _on_connected(self, session: HostSession) -> None:
        self._session = session
        self._set_connected(True)
        self.status_label.setText("已连接")
        self._append_log("INFO", f"{self._backend_name} LIN connected")

    def _on_uds_send_clicked(self) -> None:
        if self._session is None:
            self._show_error("请先连接通道")
            return
        try:
            payload = bytes.fromhex(self.uds_payload_edit.text())
        except ValueError:
            self._show_error("UDS Payload 格式错误")
            return
        self.uds_send_button.setEnabled(False)
        self.flash_start_button.setEnabled(False)
        worker = UdsWorker(self._session, payload, log_dir=Path("logs"))
        worker.event.connect(self._on_worker_event)
        worker.result.connect(self._on_uds_response)
        worker.failed.connect(self._show_error)
        worker.finished.connect(lambda: self.uds_send_button.setEnabled(True))
        worker.finished.connect(lambda: self.flash_start_button.setEnabled(self._session is not None))
        self._start_worker(worker)

    def _on_uds_response(self, payload: bytes) -> None:
        text = payload.hex(" ").upper()
        self.uds_response.appendPlainText(text)
        self._append_log("UDS", f"RX {text}")

    def _on_use_fixture_clicked(self) -> None:
        self.flash_driver_edit.setText("tests/fixtures/flash_driver_18b.bin")
        self.app_edit.setText("tests/fixtures/app_20b.bin")

    def _on_browse_flash_driver_clicked(self) -> None:
        self._select_firmware_file(self.flash_driver_edit, "选择 FlashDriver 镜像")

    def _on_browse_app_clicked(self) -> None:
        self._select_firmware_file(self.app_edit, "选择 App 镜像")

    def _select_firmware_file(self, edit: QLineEdit, title: str) -> None:
        current = Path(edit.text())
        start_dir = current.parent if current.parent.exists() else Path.cwd()
        selected, _ = QFileDialog.getOpenFileName(self, title, str(start_dir), self.firmware_file_filter)
        if selected:
            edit.setText(selected)

    def _on_flash_start_clicked(self) -> None:
        if self._session is None:
            self._show_error("请先连接通道")
            return
        self.flash_progress.setValue(0)
        self.flash_status_label.setText("准备刷写")
        self.flash_stage_log.clear()
        self.flash_start_button.setEnabled(False)
        self.uds_send_button.setEnabled(False)
        worker = FlashWorker(
            self._session,
            flash_driver_path=Path(self.flash_driver_edit.text()),
            app_path=Path(self.app_edit.text()),
            log_dir=Path("logs"),
            dry_run=self._backend_name == "Fake",
        )
        worker.event.connect(self._on_worker_event)
        worker.failed.connect(self._show_error)
        worker.finished.connect(lambda: self.flash_start_button.setEnabled(True))
        worker.finished.connect(lambda: self.uds_send_button.setEnabled(True))
        self._start_worker(worker)

    def _on_worker_event(self, event: WorkerEvent) -> None:
        if event.kind == "cancelled":
            self.status_label.setText("已取消")
            self._append_stage_event(event)
            self._append_log("CANCELLED", event.message)
            return
        if event.progress is not None:
            self.flash_progress.setValue(event.progress)
        if event.kind == "trace" and event.trace is not None:
            self._append_trace_event(event.trace)
            return
        self._append_stage_event(event)
        self._append_log(event.kind.upper(), event.message)

    def _set_connected(self, connected: bool) -> None:
        self.uds_send_button.setEnabled(connected)
        self.flash_start_button.setEnabled(connected)
        self.bus_send_button.setEnabled(False)

    def _show_error(self, message: str) -> None:
        self.status_label.setText("错误")
        self._append_log("ERROR", message)

    def _append_log(self, level: str, message: str) -> None:
        self.trace_log.appendPlainText(f"{_time_text()} {level:<8} {message}")

    def _append_stage_event(self, event: WorkerEvent) -> None:
        self.flash_status_label.setText(event.message)
        if event.progress is None:
            self.flash_stage_log.appendPlainText(f"{event.timestamp:%H:%M:%S}        {event.message}")
            return
        self.flash_stage_log.appendPlainText(f"{event.timestamp:%H:%M:%S} [{event.progress:3d}%] {event.message}")

    def _append_trace_event(self, event) -> None:
        data = event.data.hex(" ").upper()
        note = _describe_lin_uds(event.data)
        suffix = f"  {note}" if note else ""
        self.trace_log.appendPlainText(
            f"{event.timestamp:%H:%M:%S.%f}"[:12]
            + f" {event.direction:<2}  {event.bus:<3} 0x{event.frame_id:02X}  {data:<23}{suffix}"
        )


def _is_thread_running(thread: QThread) -> bool:
    try:
        return thread.isRunning()
    except RuntimeError:
        return False


def _default_backend_name(backends: dict[str, HostBackend]) -> str:
    if "TSMaster" in backends:
        return "TSMaster"
    if "Fake" in backends:
        return "Fake"
    return next(iter(backends))


def _time_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S")


def _path_row(edit: QLineEdit, button: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    row.addWidget(button)
    return row


def _describe_lin_uds(data: bytes) -> str:
    if len(data) < 3:
        return ""
    pci = data[1]
    if (pci & 0xF0) == 0x00:
        payload_len = pci & 0x0F
        if payload_len == 0 or len(data) < 2 + payload_len:
            return ""
        payload = data[2 : 2 + payload_len]
    elif (pci & 0xF0) == 0x10 and len(data) >= 4:
        payload = data[3:8]
    else:
        return "UDS: ConsecutiveFrame"
    if not payload:
        return ""
    sid = payload[0]
    names = {
        0x10: "DiagnosticSessionControl",
        0x11: "ECUReset",
        0x22: "ReadDataByIdentifier",
        0x27: "SecurityAccess",
        0x31: "RoutineControl",
        0x34: "RequestDownload",
        0x36: "TransferData",
        0x37: "RequestTransferExit",
        0x50: "Positive DiagnosticSessionControl",
        0x51: "Positive ECUReset",
        0x62: "Positive ReadDataByIdentifier",
        0x67: "Positive SecurityAccess",
        0x71: "Positive RoutineControl",
        0x74: "Positive RequestDownload",
        0x76: "Positive TransferData",
        0x77: "Positive RequestTransferExit",
        0x7F: "NegativeResponse",
    }
    return f"UDS: {names.get(sid, f'0x{sid:02X}')}"
