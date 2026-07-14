"""面向台架使用的发布资源、设备扫描和 OTA 工作区。"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import sys
from threading import Event

from PySide6.QtCore import QProcess, QThread, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from unified_can_lin_host_tool.tool_identity import get_tool_identity
from unified_can_lin_host_tool.update.metadata import UpdateInfo
from unified_can_lin_host_tool.update.service import UpdateService, installer_arguments
from unified_can_lin_host_tool.ui.update_worker import UpdateWorker


_NO_UPDATE_RESULT = object()


class ReleaseMainWindow(QMainWindow):
    def __init__(
        self,
        update_service: UpdateService | None = None,
        auto_check: bool = True,
    ) -> None:
        super().__init__()
        self._identity = get_tool_identity()
        self._update_service = update_service
        self._auto_check_enabled = auto_check
        self._auto_check_started = False
        self._update_thread: QThread | None = None
        self._update_worker: UpdateWorker | None = None
        self._update_success: Callable[[object], None] | None = None
        self._update_failure: Callable[[str], None] | None = None
        self._update_result: object = _NO_UPDATE_RESULT
        self._update_error: str | None = None
        self._download_cancel_event: Event | None = None
        self._download_progress_dialog: QProgressDialog | None = None
        self._pending_update_info: UpdateInfo | None = None
        self._update_prompt_suppressed = False
        self._tasks_frozen = False
        self._update_exit_requested = False

        self.setWindowTitle(f"ECU Firmware Release Tool {self._identity.version}")
        self.resize(920, 620)
        self._process: QProcess | None = None
        self._operation = ""
        self._stdout_buffer = ""
        self._terminal_event_received = False

        root = QWidget()
        layout = QVBoxLayout(root)
        form = QFormLayout()

        self.project_combo = QComboBox()
        self.project_combo.addItems(["AS5PR", "E68"])
        form.addRow("项目", self.project_combo)

        self.package_edit = QLineEdit()
        self.package_edit.setPlaceholderText("选择原生 App 镜像（.hex/.s19/.srec）")
        self.browse_button = QPushButton("选择 App...")
        self.browse_button.clicked.connect(self._browse)
        package_row = QHBoxLayout()
        package_row.addWidget(self.package_edit, 1)
        package_row.addWidget(self.browse_button)
        form.addRow("App 镜像", package_row)

        self.device_combo = QComboBox()
        self.device_combo.setPlaceholderText("请先扫描设备")
        self.scan_button = QPushButton("扫描设备")
        self.scan_button.clicked.connect(lambda: self._execute("scan"))
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_combo, 1)
        device_row.addWidget(self.scan_button)
        form.addRow("总线设备", device_row)
        layout.addLayout(form)

        self.status_label = QLabel("就绪：请选择 App 镜像并扫描设备")
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        layout.addWidget(self.progress)

        self.ota_button = QPushButton("开始 OTA")
        self.ota_button.setMinimumHeight(38)
        self.ota_button.clicked.connect(self._confirm_flash)
        layout.addWidget(self.ota_button)

        layout.addWidget(QLabel("运行日志"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.appendPlainText(
            f"版本 {self._identity.version}，提交 {self._identity.short_commit}"
        )
        if not self._supports_official_updates():
            self.log.appendPlainText("开发构建，不自动检查正式更新")
        layout.addWidget(self.log, 1)
        self.setCentralWidget(root)

        help_menu = self.menuBar().addMenu("帮助")
        self.check_update_action = QAction("检查更新", self)
        self.check_update_action.triggered.connect(
            lambda _checked=False: self._check_for_updates(manual=True)
        )
        help_menu.addAction(self.check_update_action)
        self.about_action = QAction("关于", self)
        self.about_action.triggered.connect(self._show_about)
        help_menu.addAction(self.about_action)

        self.project_combo.currentTextChanged.connect(self._project_changed)
        self.package_edit.textChanged.connect(self._update_gate)
        self.device_combo.currentIndexChanged.connect(self._update_gate)
        self._update_gate()

    def _project_changed(self) -> None:
        self.device_combo.clear()
        self.progress.setValue(0)
        self.status_label.setText("就绪：请选择 App 镜像并扫描设备")
        self._update_gate()

    def _update_gate(self) -> None:
        idle = self._process is None and not self._tasks_frozen
        as5pr = self.project_combo.currentText() == "AS5PR"
        app_path = Path(self.package_edit.text())
        package_ready = (app_path.is_file() and app_path.suffix.lower()
                         in {".hex", ".ihex", ".s19", ".srec", ".s28", ".s37"})
        device_ready = self.device_combo.currentIndex() >= 0
        self.scan_button.setEnabled(idle and as5pr)
        self.ota_button.setEnabled(idle and as5pr and package_ready and device_ready)
        self.project_combo.setEnabled(not self._tasks_frozen)
        self.package_edit.setEnabled(not self._tasks_frozen)
        self.browse_button.setEnabled(not self._tasks_frozen)
        self.device_combo.setEnabled(not self._tasks_frozen)
        self.check_update_action.setEnabled(
            not self._tasks_frozen and self._supports_official_updates()
        )

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择原生 App 镜像",
            "",
            "App Image (*.hex *.ihex *.s19 *.srec *.s28 *.s37)",
        )
        if path:
            self.package_edit.setText(path)

    def _confirm_flash(self) -> None:
        device = self.device_combo.currentData()
        if not isinstance(device, dict):
            self.log.appendPlainText("请先扫描并选择总线设备")
            return
        answer = QMessageBox.warning(
            self,
            "确认实机 OTA",
            (
                f"设备：{device.get('name', '未知设备')}\n"
                f"序列号：{device.get('serial', '未知')}\n"
                f"通道：CAN{device.get('displayChannel', device.get('appChannel', 0) + 1)}\n\n"
                "将擦除并重写 AS5PR App。\n"
                "确认 CAN 线束、ECU 电源及恢复条件已就绪？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._execute("flash")

    def _execute(self, operation: str) -> None:
        if self._tasks_frozen:
            self.log.appendPlainText("更新安装已启动，不再接受新任务")
            return
        if self._process is not None:
            self.log.appendPlainText("已有操作正在执行")
            return
        project = self.project_combo.currentText()
        package = Path(self.package_edit.text())
        if operation == "flash" and not package.is_file():
            self.log.appendPlainText("请选择存在的原生 App 镜像（HEX/S19）")
            return

        if operation == "scan":
            arguments = release_scan_arguments(project)
            self.device_combo.clear()
            self.progress.setValue(0)
            self.status_label.setText("正在扫描同星和图莫斯总线设备...")
        else:
            device = self.device_combo.currentData()
            if not isinstance(device, dict):
                self.log.appendPlainText("请先扫描并选择总线设备")
                return
            arguments = release_ota_arguments(package, project, device)
            self.progress.setValue(0)
            self.status_label.setText("正在启动 OTA...")

        process = QProcess(self)
        self._process = process
        self._operation = operation
        self._stdout_buffer = ""
        self._terminal_event_received = False
        program, process_arguments = release_cli_process_command(arguments)
        process.setProgram(program)
        process.setArguments(process_arguments)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self.log.appendPlainText(f"启动：{'设备扫描' if operation == 'scan' else '实机 OTA'}")
        self._update_gate()
        process.start()

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        self._stdout_buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            self._handle_output_line(line.rstrip("\r"))

    def _read_stderr(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if text:
            self.log.appendPlainText(text)

    def _handle_output_line(self, line: str) -> None:
        if not line:
            return
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self.log.appendPlainText(line)
            return

        event = message.get("event")
        if event == "scan_result":
            self.device_combo.clear()
            for device in message.get("devices", []):
                name = device.get("name") or "未知设备"
                product = device.get("product") or "未知型号"
                serial = device.get("serial") or "无序列号"
                for channel in device.get("channels", []):
                    endpoint = dict(device)
                    endpoint.pop("channels", None)
                    endpoint.update(channel)
                    display_channel = endpoint.get("displayChannel", endpoint.get("appChannel", 0) + 1)
                    self.device_combo.addItem(
                        f"{name} / {product} CAN{display_channel}  |  SN: {serial}", endpoint
                    )
            count = self.device_combo.count()
            if count:
                self.device_combo.setCurrentIndex(0)
            self.status_label.setText(f"扫描完成：发现 {count} 个 CAN 通道")
            self.log.appendPlainText(f"扫描完成：发现 {count} 个 CAN 通道")
            for warning in message.get("warnings", []):
                self.log.appendPlainText(f"扫描提示：{warning}")
        elif event == "progress":
            percent = max(0, min(100, int(message.get("percent", 0))))
            stage = str(message.get("stage", "OTA"))
            detail = str(message.get("message", ""))
            self.progress.setValue(percent)
            self.status_label.setText(f"{stage}：{detail}")
            self.log.appendPlainText(f"[{percent:3d}%] {stage} - {detail}")
        elif event == "result":
            self._terminal_event_received = True
            if message.get("ok"):
                self.progress.setValue(100)
                self.status_label.setText("OTA 完成：App 通信验证通过")
                self.log.appendPlainText(f"OTA 成功，日志：{message.get('log', '')}")
            else:
                status = str(message.get("status", "UNKNOWN"))
                detail = str(message.get("message") or "")
                status_text = {
                    "ECU_IN_BOOT": "OTA中断：ECU保留在 Boot，禁止断电",
                    "FAILED_UNKNOWN": "OTA失败：ECU状态未知，禁止断电并先重新探测",
                    "COMPLETED_UNVERIFIED": "OTA传输完成，但复位后未确认 App 通信",
                    "CANCELLED_SAFE": "OTA已在擦除前安全取消",
                    "PACKAGE_REJECTED": "发布资源校验失败，未执行擦除",
                    "IDENTITY_REJECTED": "ECU身份不匹配，未执行擦除",
                }.get(status, f"OTA未完成：{status}")
                self.status_label.setText(status_text)
                self.log.appendPlainText(
                    f"结果：{status}; {detail}; 日志：{message.get('log', '')}"
                )
        elif event == "error" or message.get("ok") is False:
            self._terminal_event_received = True
            error = str(message.get("error") or message.get("message") or "未知错误")
            self.status_label.setText(f"失败：{error}")
            self.log.appendPlainText(f"错误：{error}")
        else:
            self.log.appendPlainText(json.dumps(message, ensure_ascii=False))
        self._update_gate()

    def _process_error(self, error) -> None:
        if self._process is None:
            return
        text = self._process.errorString()
        self._terminal_event_received = True
        if error == QProcess.ProcessError.FailedToStart:
            message = f"进程启动失败：{text}"
        elif self._operation == "flash":
            message = f"OTA进程异常：ECU状态未知，禁止断电并先重新探测；{text}"
        else:
            message = f"设备扫描进程异常：{text}"
        self.status_label.setText(message)
        self.log.appendPlainText(message)
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._release_process()

    def _finished(self, exit_code: int, _status) -> None:
        if self._process is not None:
            self._read_stdout()
            self._read_stderr()
            if self._stdout_buffer.strip():
                self._handle_output_line(self._stdout_buffer.strip())
                self._stdout_buffer = ""
        self.log.appendPlainText(f"结束：exit={exit_code}")
        if exit_code != 0 and not self._terminal_event_received:
            self.status_label.setText(f"操作失败：exit={exit_code}")
        self._release_process()

    def _release_process(self) -> None:
        if self._process is not None:
            self._process.deleteLater()
        self._process = None
        self._operation = ""
        self._update_gate()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._auto_check_started:
            return
        self._auto_check_started = True
        if (
            self._auto_check_enabled
            and self._update_service is not None
            and self._supports_official_updates()
        ):
            QTimer.singleShot(0, lambda: self._check_for_updates(manual=False))

    def _supports_official_updates(self) -> bool:
        return self._identity.official_build and bool(self._identity.repository)

    def _show_about(self) -> None:
        repository = self._identity.repository or "未固化（开发构建）"
        build_time = self._identity.build_time_utc or "开发环境"
        details = (
            f"版本：{self._identity.version}\n"
            f"完整提交：{self._identity.commit}\n"
            f"构建时间：{build_time}\n"
            f"固化仓库：{repository}"
        )
        if not self._supports_official_updates():
            details += "\n\n开发构建，不自动检查正式更新"
        QMessageBox.about(self, "关于 ECU Firmware Release Tool", details)

    def _check_for_updates(self, *, manual: bool) -> None:
        if not self._supports_official_updates() or self._update_service is None:
            message = "开发构建，不自动检查正式更新"
            self.status_label.setText(message)
            self.log.appendPlainText(message)
            return
        if self._update_thread is not None:
            if manual:
                self.status_label.setText("更新操作正在进行")
            return
        if manual:
            self.status_label.setText("正在检查正式更新...")
        self._start_update_worker(
            lambda _progress: self._update_service.check(),
            on_success=lambda result: self._handle_update_check_result(result, manual=manual),
            on_failure=lambda error: self._handle_update_failure("检查", error),
        )

    def _start_update_worker(
        self,
        operation: Callable[[Callable[[int, int], None]], object],
        *,
        on_success: Callable[[object], None],
        on_failure: Callable[[str], None],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bool:
        if self._update_thread is not None or self._tasks_frozen:
            return False

        thread = QThread(self)
        worker = UpdateWorker(operation)
        worker.moveToThread(thread)
        self._update_thread = thread
        self._update_worker = worker
        self._update_success = on_success
        self._update_failure = on_failure
        self._update_result = _NO_UPDATE_RESULT
        self._update_error = None

        thread.started.connect(worker.run)
        worker.succeeded.connect(self._record_update_result)
        worker.failed.connect(self._record_update_error)
        if on_progress is not None:
            worker.progress.connect(on_progress)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._finish_update_worker(thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()
        return True

    def _record_update_result(self, result: object) -> None:
        self._update_result = result

    def _record_update_error(self, error: str) -> None:
        self._update_error = error

    def _finish_update_worker(self, thread: QThread) -> None:
        if thread is not self._update_thread:
            return
        success = self._update_success
        failure = self._update_failure
        result = self._update_result
        error = self._update_error
        self._update_thread = None
        self._update_worker = None
        self._update_success = None
        self._update_failure = None
        self._update_result = _NO_UPDATE_RESULT
        self._update_error = None
        self._close_download_progress()
        self._update_gate()
        if error is not None:
            if failure is not None:
                failure(error)
        elif success is not None and result is not _NO_UPDATE_RESULT:
            success(result)

    def _handle_update_check_result(
        self,
        result: object,
        *,
        manual: bool = True,
    ) -> None:
        if result is None:
            if manual:
                self.status_label.setText("当前已是最新正式版本")
            return
        if not isinstance(result, UpdateInfo):
            self._handle_update_failure("检查", "更新服务返回了无效结果")
            return
        self._pending_update_info = result
        if self._update_prompt_suppressed:
            self.status_label.setText(f"发现正式更新 {result.version}，本次运行已稍后提醒")
            return
        message = self._update_prompt_message(result)
        if self._prompt_update(message):
            self._download_update(result)
        else:
            self._update_prompt_suppressed = True
            self.status_label.setText(f"已稍后提醒更新 {result.version}")

    def _update_prompt_message(self, info: UpdateInfo) -> str:
        return (
            f"当前版本：{self._identity.version}\n"
            f"目标版本：{info.version}\n"
            f"安装包大小：{_format_byte_size(info.installer.size)}\n\n"
            f"更新说明：\n{info.release_notes}"
        )

    def _prompt_update(self, message: str) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("发现正式更新")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(message)
        install_button = dialog.addButton("立即更新", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("稍后提醒", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is install_button

    def _download_update(self, info: UpdateInfo) -> None:
        if not self._can_start_update_install() or self._update_service is None:
            self.status_label.setText("当前有业务或更新任务运行，不能开始更新下载")
            return
        self._pending_update_info = info
        self._download_cancel_event = Event()
        dialog = QProgressDialog("正在下载并校验更新安装包...", "取消下载", 0, 100, self)
        dialog.setWindowTitle(f"下载更新 {info.version}")
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._cancel_update_download)
        dialog.show()
        self._download_progress_dialog = dialog
        started = self._start_update_worker(
            lambda progress: self._update_service.download(
                info,
                progress=progress,
                cancelled=self._download_cancel_event.is_set,
            ),
            on_success=self._handle_download_result,
            on_failure=lambda error: self._handle_update_failure("下载", error),
            on_progress=self._update_download_progress,
        )
        if not started:
            self._close_download_progress()

    def _cancel_update_download(self) -> None:
        if self._download_cancel_event is not None:
            self._download_cancel_event.set()
            self.status_label.setText("正在取消更新下载...")

    def _update_download_progress(self, received: int, total: int) -> None:
        if self._download_progress_dialog is None:
            return
        percent = 0 if total <= 0 else max(0, min(100, int(received * 100 / total)))
        self._download_progress_dialog.setValue(percent)
        self._download_progress_dialog.setLabelText(
            f"正在下载并校验更新安装包：{_format_byte_size(received)} / {_format_byte_size(total)}"
        )

    def _close_download_progress(self) -> None:
        if self._download_progress_dialog is not None:
            self._download_progress_dialog.close()
            self._download_progress_dialog.deleteLater()
        self._download_progress_dialog = None
        self._download_cancel_event = None

    def _handle_update_failure(self, action: str, error: str) -> None:
        if action == "下载" and "取消" in error:
            message = "更新下载已取消；未启动安装器"
        else:
            message = f"更新{action}失败：{error}"
        self.status_label.setText(message)
        self.log.appendPlainText(message)

    def _handle_download_result(self, result: object) -> None:
        installer = Path(result) if isinstance(result, (str, Path)) else None
        if installer is None:
            self._handle_update_failure("下载", "更新服务未返回有效安装包路径")
            return
        if not self._can_start_update_install():
            self.status_label.setText("更新安装包已缓存（校验通过）；业务结束后可重新检查更新")
            return
        self._launch_verified_installer(installer)

    def _can_start_update_install(self) -> bool:
        return (
            self._process is None
            and self._update_thread is None
            and self._update_worker is None
            and not self._tasks_frozen
            and not self._update_exit_requested
        )

    def _launch_verified_installer(self, installer: Path) -> None:
        if not self._can_start_update_install():
            self.status_label.setText("当前有业务正在运行，已保留校验通过的更新安装包")
            return
        self._set_tasks_frozen(True)
        version = (
            str(self._pending_update_info.version)
            if self._pending_update_info is not None
            else self._identity.version
        )
        try:
            started, _pid = QProcess.startDetached(
                str(installer),
                installer_arguments(
                    parent_pid=os.getpid(),
                    log_path=default_installer_log_path(version),
                ),
            )
        except Exception as exc:
            started = False
            error = str(exc)
        else:
            error = "操作系统拒绝启动安装器"

        if not started:
            self._set_tasks_frozen(False)
            self._handle_update_failure("安装器启动", error)
            return

        self._update_exit_requested = True
        self.status_label.setText("更新安装器已启动，正在正常退出工具...")
        self.log.appendPlainText("更新安装器已独立启动，工具将释放资源并退出")
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _set_tasks_frozen(self, frozen: bool) -> None:
        self._tasks_frozen = frozen
        self._update_gate()

    def closeEvent(self, event) -> None:
        if self._update_exit_requested:
            super().closeEvent(event)
            return
        if self._update_thread is not None:
            if self._download_cancel_event is not None:
                self._download_cancel_event.set()
            QMessageBox.warning(
                self,
                "更新操作进行中",
                "更新检查或下载正在结束，请稍后再关闭。",
            )
            event.ignore()
            return
        if self._process is not None and self._operation == "flash":
            QMessageBox.warning(
                self,
                "OTA 进行中",
                "OTA 进行中不可退出。强制关闭可能留下已擦除或部分写入的 ECU。",
            )
            event.ignore()
            return
        if self._process is not None:
            self._process.terminate()
        super().closeEvent(event)


def default_installer_log_path(version: str) -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "EcuReleaseTool" / "updates" / version / "installer.log"


def _format_byte_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def release_cli_process_command(arguments: list[str]) -> tuple[str, list[str]]:
    """开发态调用 Python 模块，PyInstaller 冻结态调用同目录 CLI。"""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).with_name("EcuReleaseCLI.exe")), list(arguments)
    return sys.executable, ["-m", "unified_can_lin_host_tool.cli.release", *arguments]


def release_scan_arguments(project: str) -> list[str]:
    """图形界面扫描同时探测同星和图莫斯设备。"""
    return ["scan", "--project", project, "--adapter", "auto"]


def release_ota_arguments(package: Path, project: str, device: dict) -> list[str]:
    """把界面选中的真实设备端点转换为命令行参数。"""
    adapter = str(device.get("adapter", "tsmaster"))
    arguments = [
        "ota", str(package),
        "--project", project,
        "--confirm-project", project,
        "--yes-i-know-this-erases-app",
        "--adapter", adapter,
        "--hw-serial", str(device["serial"]),
        "--hw-index", str(device["deviceIndex"]),
        "--hw-channel", str(device["hwChannel"]),
    ]
    if adapter == "usb2xxx":
        return arguments
    if adapter != "tsmaster":
        raise ValueError(f"不支持的总线设备提供方：{adapter}")
    arguments.extend([
        "--hw-name", str(device["name"]),
        "--hw-device-type", str(device["deviceType"]),
        "--hw-subtype", str(device["hwSubtype"]),
        "--tsmaster-channel", str(device["appChannel"]),
        "--can-channel-count", str(device["canChannelCount"]),
        "--base-hw-channel", str(device["baseHwChannel"]),
    ])
    return arguments
