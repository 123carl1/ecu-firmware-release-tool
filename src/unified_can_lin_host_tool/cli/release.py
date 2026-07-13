"""发布资源检查、只读身份探测和 AS5PR 实机 OTA 的统一命令行。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL, TsmasterAdapter
from unified_can_lin_host_tool.as5pr.ota_state_machine import As5prOtaStateMachine, OtaResultStatus
from unified_can_lin_host_tool.release.development_keys import DEVELOPMENT_KEY_ID, DEVELOPMENT_PACKAGE_PUBLIC_KEY
from unified_can_lin_host_tool.release.ecu_identity import probe_identity
from unified_can_lin_host_tool.release.package import load_verified_release_package
from unified_can_lin_host_tool.release.package_builder import build_as5pr_release_package
from unified_can_lin_host_tool.release.project_config import ProjectCode, get_project_config
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.can_isotp import CanIsoTpTransport


PUBLIC_KEYS = {DEVELOPMENT_KEY_ID: DEVELOPMENT_PACKAGE_PUBLIC_KEY}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecu-release", description="E68 LIN / AS5PR CAN 内部发布工具")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-package", help="从受控三工程输出构建单文件发布资源")
    build.add_argument("--project", choices=["AS5PR"], required=True)
    build.add_argument("--firmware-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="验签并显示发布资源摘要")
    _package_arguments(inspect)
    probe = commands.add_parser("probe", help="仅读取在线 ECU F1A0 身份")
    _hardware_arguments(probe)
    flash = commands.add_parser("flash", help="检查或刷写发布资源")
    _package_arguments(flash)
    modes = flash.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-dry-run", action="store_true")
    modes.add_argument("--read-only-probe", action="store_true")
    modes.add_argument("--real-flash", action="store_true")
    flash.add_argument("--confirm-project")
    flash.add_argument("--yes-i-know-this-erases-app", action="store_true")
    _hardware_arguments(flash, include_project=False)
    return parser


def _package_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package", type=Path)
    parser.add_argument("--project", choices=[item.value for item in ProjectCode], required=True)


def _hardware_arguments(parser: argparse.ArgumentParser, *, include_project: bool = True) -> None:
    if include_project:
        parser.add_argument("--project", choices=["AS5PR"], required=True)
    parser.add_argument("--tsmaster-dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--tsmaster-app", default="EcuRelease_AS5PR")
    parser.add_argument("--tsmaster-channel", type=int, default=0)
    parser.add_argument("--hw-name", default="TC1016")
    parser.add_argument("--hw-subtype", type=int, default=11)
    parser.add_argument("--hw-index", type=int, default=0)
    parser.add_argument("--hw-channel", type=int, default=0)
    parser.add_argument("--log-dir", type=Path, default=Path("artifacts/ota_logs"))


def _summary(package) -> dict:
    return {
        "ok": True,
        "project": package.project.value,
        "releaseSetId": package.release_set_id,
        "buildId": package.build_id.hex(),
        "buildCommit": package.build_commit,
        "resources": [
            {"kind": item.kind.name, "address": item.load_address, "size": len(item.content)}
            for item in package.resources
        ],
    }


def _open_transport(args, config, trace):
    adapter = TsmasterAdapter(
        dll_path=args.tsmaster_dll, app_name=args.tsmaster_app,
        app_channel=args.tsmaster_channel, hw_name=args.hw_name,
        hw_subtype=args.hw_subtype, hw_index=args.hw_index, hw_channel=args.hw_channel,
        baud_kbps=config.bus.baudrate / 1000.0,
    )
    adapter.open_can()
    return adapter, CanIsoTpTransport(adapter, config, trace_logger=trace)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    adapter = None
    trace = None
    try:
        project = ProjectCode(args.project)
        if args.command == "build-package":
            built = build_as5pr_release_package(args.firmware_root, args.output)
            print(json.dumps(_summary(built), ensure_ascii=False))
            return 0
        if args.command == "inspect":
            loaded = load_verified_release_package(args.package, project, PUBLIC_KEYS)
            print(json.dumps(_summary(loaded), ensure_ascii=False))
            return 0

        config = get_project_config(project)
        if args.command == "flash":
            loaded = load_verified_release_package(args.package, project, PUBLIC_KEYS)
            if args.offline_dry_run:
                output = _summary(loaded)
                output["mode"] = "OFFLINE_DRY_RUN"
                print(json.dumps(output, ensure_ascii=False))
                return 0
            if args.real_flash and (
                args.confirm_project != project.value or not args.yes_i_know_this_erases_app
            ):
                raise ValueError("真实刷写需要项目名确认和擦除确认")
        trace = TraceLogger(args.log_dir)
        adapter, transport = _open_transport(args, config, trace)
        if args.command == "probe" or args.read_only_probe:
            identity = probe_identity(transport, config)
            print(json.dumps({"ok": identity.role is not None, "status": identity.status.value,
                              "role": identity.role.name if identity.role else None,
                              "targetId": identity.target_id}, ensure_ascii=False))
            return 0 if identity.role is not None else 3
        result = As5prOtaStateMachine(transport).run(loaded)
        print(json.dumps({"ok": result.status is OtaResultStatus.COMPLETED,
                          "status": result.status.value, "releaseSetId": result.release_set_id,
                          "message": result.message, "log": str(trace.path)}, ensure_ascii=False))
        return 0 if result.status is OtaResultStatus.COMPLETED else 4
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    finally:
        if adapter is not None:
            adapter.close()
        if trace is not None:
            trace.close()


if __name__ == "__main__":
    raise SystemExit(main())
