"""单文件发布资源的 GUI 工作区；业务执行统一转交 ecu-release 命令入口。"""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget,
)


class ReleaseMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("E68 LIN / AS5PR CAN 内部发布工具")
        self.resize(920, 620)
        self._process: QProcess | None = None
        root = QWidget()
        layout = QVBoxLayout(root)
        form = QFormLayout()
        self.project_combo = QComboBox()
        self.project_combo.addItems(["AS5PR", "E68"])
        self.package_edit = QLineEdit()
        browse = QPushButton("选择 .erel...")
        browse.clicked.connect(self._browse)
        package_row = QHBoxLayout()
        package_row.addWidget(self.package_edit, 1)
        package_row.addWidget(browse)
        form.addRow("项目", self.project_combo)
        form.addRow("发布资源", package_row)
        layout.addLayout(form)
        self.identity_label = QLabel("ECU 身份：未探测")
        layout.addWidget(self.identity_label)
        buttons = QHBoxLayout()
        self.inspect_button = QPushButton("离线检查")
        self.dry_run_button = QPushButton("离线演练")
        self.probe_button = QPushButton("只读身份探测")
        self.flash_button = QPushButton("实机 OTA")
        for button in (self.inspect_button, self.dry_run_button, self.probe_button, self.flash_button):
            buttons.addWidget(button)
        self.inspect_button.clicked.connect(lambda: self._execute("inspect"))
        self.dry_run_button.clicked.connect(lambda: self._execute("dry"))
        self.probe_button.clicked.connect(lambda: self._execute("probe"))
        self.flash_button.clicked.connect(self._confirm_flash)
        layout.addLayout(buttons)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        self.setCentralWidget(root)
        self._update_gate()
        self.project_combo.currentTextChanged.connect(self._update_gate)

    def _update_gate(self) -> None:
        enabled = self.project_combo.currentText() == "AS5PR"
        self.probe_button.setEnabled(enabled)
        self.flash_button.setEnabled(enabled)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择发布资源", "", "Release Set (*.erel)")
        if path:
            self.package_edit.setText(path)

    def _confirm_flash(self) -> None:
        answer = QMessageBox.warning(
            self, "确认实机 OTA", "将擦除并重写 AS5PR App。确认线束、电源及恢复条件已就绪？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            self._execute("flash")

    def _execute(self, operation: str) -> None:
        if self._process is not None:
            self.log.appendPlainText("已有操作正在执行")
            return
        project = self.project_combo.currentText()
        package = Path(self.package_edit.text())
        if operation != "probe" and not package.is_file():
            self.log.appendPlainText("请选择存在的 .erel 文件")
            return
        if operation == "inspect":
            arguments = ["-m", "unified_can_lin_host_tool.cli.release", "inspect", str(package), "--project", project]
        elif operation == "probe":
            arguments = ["-m", "unified_can_lin_host_tool.cli.release", "probe", "--project", project]
        else:
            mode = "--offline-dry-run" if operation == "dry" else "--real-flash"
            arguments = ["-m", "unified_can_lin_host_tool.cli.release", "flash", str(package),
                         "--project", project, mode]
            if operation == "flash":
                arguments += ["--confirm-project", project, "--yes-i-know-this-erases-app"]
        process = QProcess(self)
        self._process = process
        process.setProgram(sys.executable)
        process.setArguments(arguments)
        process.readyReadStandardOutput.connect(
            lambda: self.log.appendPlainText(bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace").rstrip())
        )
        process.readyReadStandardError.connect(
            lambda: self.log.appendPlainText(bytes(process.readAllStandardError()).decode("utf-8", errors="replace").rstrip())
        )
        process.finished.connect(self._finished)
        self.log.appendPlainText(f"启动：{operation}")
        process.start()

    def _finished(self, exit_code: int, _status) -> None:
        self.log.appendPlainText(f"结束：exit={exit_code}")
        if self._process is not None:
            self._process.deleteLater()
        self._process = None

    def closeEvent(self, event) -> None:
        if self._process is not None:
            self._process.terminate()
        super().closeEvent(event)
