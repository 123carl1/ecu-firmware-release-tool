from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from unified_can_lin_host_tool.ui.release_workspace import ReleaseMainWindow
from unified_can_lin_host_tool.tool_identity import ToolIdentity, get_tool_identity
from unified_can_lin_host_tool.update.github_release import GitHubReleaseSource
from unified_can_lin_host_tool.update.https_client import SafeHttpsClient
from unified_can_lin_host_tool.update.release_keys import load_release_public_keys
from unified_can_lin_host_tool.update.runtime_mutex import product_run_mutex
from unified_can_lin_host_tool.update.service import UpdateService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified CAN/LIN Host Tool UI")
    parser.add_argument("--smoke", action="store_true", help="构造主窗口后立即退出。")
    return parser


def build_default_update_service(identity: ToolIdentity) -> UpdateService | None:
    if not identity.official_build or not identity.repository:
        return None
    http = SafeHttpsClient()
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return UpdateService(
        identity,
        GitHubReleaseSource(identity.repository, http),
        http,
        root / "EcuReleaseTool" / "updates",
        load_release_public_keys(),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with product_run_mutex():
        app = QApplication.instance() or QApplication(sys.argv[:1])
        identity = get_tool_identity()
        window = ReleaseMainWindow(
            update_service=build_default_update_service(identity),
        )
        if args.smoke:
            app.processEvents()
            print("UI SMOKE OK")
            return 0

        window.show()
        return app.exec()
