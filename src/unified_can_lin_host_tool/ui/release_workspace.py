"""面向台架使用的发布资源、设备扫描和 OTA 工作区。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ReleaseMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("E68 LIN / AS5PR CAN OTA 工具")
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
        browse = QPushButton("选择 App...")
        browse.clicked.connect(self._browse)
        package_row = QHBoxLayout()
        package_row.addWidget(self.package_edit, 1)
        package_row.addWidget(browse)
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
        layout.addWidget(self.log, 1)
        self.setCentralWidget(root)

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
        idle = self._process is None
        as5pr = self.project_combo.currentText() == "AS5PR"
        app_path = Path(self.package_edit.text())
        package_ready = (app_path.is_file() and app_path.suffix.lower()
                         in {".hex", ".ihex", ".s19", ".srec", ".s28", ".s37"})
        device_ready = self.device_combo.currentIndex() >= 0
        self.scan_button.setEnabled(idle and as5pr)
        self.ota_button.setEnabled(idle and as5pr and package_ready and device_ready)

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

    def closeEvent(self, event) -> None:
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
