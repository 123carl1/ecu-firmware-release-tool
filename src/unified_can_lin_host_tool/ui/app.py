from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from unified_can_lin_host_tool.ui.release_workspace import ReleaseMainWindow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified CAN/LIN Host Tool UI")
    parser.add_argument("--smoke", action="store_true", help="构造主窗口后立即退出。")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = ReleaseMainWindow()
    if args.smoke:
        app.processEvents()
        print("UI SMOKE OK")
        return 0

    window.show()
    return app.exec()
