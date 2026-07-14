"""设备扫描和 AS5PR 原生 App 实机 OTA 命令行。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL, TS_USB_DEVICE, TsmasterAdapter
from unified_can_lin_host_tool.adapters.usb2xxx import DEFAULT_USB2XXX_DLL, Usb2xxxAdapter
from unified_can_lin_host_tool.as5pr.ota_state_machine import (
    As5prOtaStateMachine,
    OtaProgress,
    OtaResultStatus,
)
from unified_can_lin_host_tool.release.project_config import ProjectCode, get_project_config
from unified_can_lin_host_tool.release.runtime_ota import prepare_as5pr_app
from unified_can_lin_host_tool.trace import TraceLogger, default_log_dir
from unified_can_lin_host_tool.transport.can_isotp import CanIsoTpTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecu-ota", description="E68 LIN / AS5PR CAN OTA 工具")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="扫描本机已连接的同星和图莫斯总线设备")
    _hardware_arguments(scan)
    ota = commands.add_parser("ota", help="检查原生 App 镜像并执行 OTA")
    ota.add_argument("app", type=Path)
    ota.add_argument("--project", choices=["AS5PR"], required=True)
    ota.add_argument("--confirm-project")
    ota.add_argument("--yes-i-know-this-erases-app", action="store_true")
    _hardware_arguments(ota, include_project=False)
    return parser


def _hardware_arguments(parser: argparse.ArgumentParser, *, include_project: bool = True) -> None:
    if include_project:
        parser.add_argument("--project", choices=["AS5PR"], required=True)
    parser.add_argument("--adapter", choices=["auto", "tsmaster", "usb2xxx"], default="tsmaster")
    parser.add_argument("--tsmaster-dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--tsmaster-app", default="EcuRelease_AS5PR")
    parser.add_argument("--tsmaster-channel", type=int, default=0)
    parser.add_argument("--can-channel-count", type=int, default=None)
    parser.add_argument("--base-hw-channel", type=int, default=None)
    parser.add_argument("--hw-name")
    parser.add_argument("--hw-device-type", type=int)
    parser.add_argument("--hw-serial")
    parser.add_argument("--hw-subtype", type=int)
    parser.add_argument("--hw-index", type=int, default=0)
    parser.add_argument("--hw-channel", type=int, default=0)
    parser.add_argument("--usb2xxx-dll", default=DEFAULT_USB2XXX_DLL)
    parser.add_argument("--log-dir", type=Path, default=default_log_dir())


def _open_transport(args, config, trace):
    adapter_kind = getattr(args, "adapter", "tsmaster")
    if adapter_kind == "usb2xxx":
        if args.hw_serial is None:
            raise ValueError("必须先扫描并选择图莫斯 CAN 设备通道")
        devices = Usb2xxxAdapter.probe_can_devices(dll_path=args.usb2xxx_dll)
        selected = next((item for item in devices if item.serial == args.hw_serial), None)
        if selected is None:
            raise ValueError(f"所选图莫斯设备已离线或发生变化：SN {args.hw_serial}")
        adapter = Usb2xxxAdapter(
            dll_path=args.usb2xxx_dll,
            device_serial=selected.serial,
            device_index=selected.device_index,
            channel=args.hw_channel,
            baudrate=config.bus.baudrate,
            receive_ids=(config.bus.response_id,),
        )
        adapter.open_can()
        return adapter, CanIsoTpTransport(adapter, config, trace_logger=trace)
    if adapter_kind not in {"auto", "tsmaster"}:
        raise ValueError(f"不支持的总线设备提供方：{adapter_kind}")
    if (args.hw_name is None or args.hw_serial is None
            or args.hw_device_type is None or args.hw_subtype is None
            or args.can_channel_count is None):
        raise ValueError("必须先扫描并选择 SDK 枚举出的 CAN 设备通道")
    current_devices = TsmasterAdapter.probe(
        dll_path=args.tsmaster_dll,
        app_name=args.tsmaster_app + "_IdentityCheck",
    )
    selected = next(
        (item for item in current_devices if item.serial == args.hw_serial),
        None,
    )
    if selected is None:
        raise ValueError(f"所选设备已离线或发生变化：SN {args.hw_serial}")
    if selected.device_type != args.hw_device_type:
        raise ValueError(f"所选设备类型已发生变化：SN {args.hw_serial}")
    adapter = TsmasterAdapter(
        dll_path=args.tsmaster_dll, app_name=args.tsmaster_app,
        app_channel=args.tsmaster_channel, hw_name=selected.name,
        hw_device_type=selected.device_type, hw_subtype=args.hw_subtype,
        hw_index=selected.device_index, hw_channel=args.hw_channel,
        can_channel_count=args.can_channel_count, base_hw_channel=args.base_hw_channel,
        baud_kbps=config.bus.baudrate / 1000.0,
    )
    adapter.open_can()
    return adapter, CanIsoTpTransport(adapter, config, trace_logger=trace)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def emit_progress_json(progress: OtaProgress) -> None:
    _print_json({
        "event": "progress",
        "percent": progress.percent,
        "stage": progress.stage,
        "message": progress.message,
        "current": progress.current,
        "total": progress.total,
    })


def _scan_tsmaster(args) -> list[dict]:
    devices = TsmasterAdapter.probe_can_devices(
        dll_path=args.tsmaster_dll,
        app_name=args.tsmaster_app + "_Scan",
    )
    return [
        {
            "adapter": "tsmaster",
            "name": item.device_name,
            "product": item.product,
            "serial": item.serial,
            "vendor": item.manufacturer,
            "deviceType": TS_USB_DEVICE,
            "deviceIndex": item.device_index,
            "hwSubtype": item.hw_subtype,
            "isCanFd": item.is_can_fd,
            "channels": [
                {
                    "displayChannel": channel + 1,
                    "hwChannel": channel,
                    "appChannel": channel,
                    "canChannelCount": item.can_channel_count,
                    "baseHwChannel": 0,
                }
                for channel in range(item.can_channel_count)
            ],
        }
        for item in devices
    ]


def _scan_usb2xxx(args) -> list[dict]:
    devices = Usb2xxxAdapter.probe_can_devices(dll_path=args.usb2xxx_dll)
    return [
        {
            "adapter": "usb2xxx",
            "name": item.device_name,
            "product": item.product,
            "serial": item.serial,
            "vendor": item.manufacturer,
            "deviceIndex": item.device_index,
            "isCanFd": item.is_can_fd,
            "channels": [
                {
                    "displayChannel": channel + 1,
                    "hwChannel": channel,
                    "canChannelCount": item.can_channel_count,
                }
                for channel in range(item.can_channel_count)
            ],
        }
        for item in devices
    ]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    adapter = None
    trace = None
    try:
        project = ProjectCode(args.project)
        if args.command == "scan":
            devices: list[dict] = []
            scan_errors: list[str] = []
            scanners = []
            if args.adapter in {"auto", "tsmaster"}:
                scanners.append(("同星", _scan_tsmaster))
            if args.adapter in {"auto", "usb2xxx"}:
                scanners.append(("图莫斯", _scan_usb2xxx))
            for label, scanner in scanners:
                try:
                    devices.extend(scanner(args))
                except Exception as exc:
                    if args.adapter != "auto":
                        raise
                    scan_errors.append(f"{label}：{exc}")
            if not devices and scan_errors:
                raise RuntimeError(f"设备扫描失败：{'；'.join(scan_errors)}")
            _print_json({
                "event": "scan_result",
                "ok": bool(devices),
                "devices": devices,
                "warnings": scan_errors,
            })
            return 0 if devices else 3

        config = get_project_config(project)
        if args.command == "ota":
            if (args.confirm_project != project.value
                    or not args.yes_i_know_this_erases_app):
                raise ValueError("真实刷写需要项目名确认和擦除确认")
            emit_progress_json(OtaProgress(1, "检查 App", "解析并校验原生 App 镜像"))
            loaded = prepare_as5pr_app(args.app)
        selected_channel = args.hw_channel if args.adapter == "usb2xxx" else args.tsmaster_channel
        trace = TraceLogger(args.log_dir, channel=selected_channel + 1)
        adapter, transport = _open_transport(args, config, trace)
        result = As5prOtaStateMachine(transport, progress=emit_progress_json).run(loaded)
        _print_json({"event": "result", "ok": result.status is OtaResultStatus.COMPLETED,
                     "status": result.status.value, "releaseSetId": result.release_set_id,
                     "message": result.message, "log": str(trace.path)})
        return 0 if result.status is OtaResultStatus.COMPLETED else 4
    except Exception as exc:
        _print_json({"event": "error", "ok": False, "error": str(exc)})
        return 2
    finally:
        if adapter is not None:
            adapter.close()
        if trace is not None:
            trace.close()


if __name__ == "__main__":
    raise SystemExit(main())
