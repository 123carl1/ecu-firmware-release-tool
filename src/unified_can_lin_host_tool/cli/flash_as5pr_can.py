from __future__ import annotations

import argparse
from pathlib import Path

from unified_can_lin_host_tool.adapters.fake import FakeCanAdapter
from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL, TsmasterAdapter
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.as5pr.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_firmware_image
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.can_isotp import CanIsoTpTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AS5PR CAN Bootloader 刷写 CLI。")
    parser.add_argument("--adapter", choices=["fake", "tsmaster"], required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--flash-driver", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument(
        "--start-in-bootloader",
        action="store_true",
        help="目标已停在 Bootloader 时启用；仍按协议执行完整预编程流程。",
    )
    parser.add_argument("--tsmaster-dll", "--dll", dest="tsmaster_dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--tsmaster-app", default="Codex_AS5PR_CAN_OTA")
    parser.add_argument("--tsmaster-project-dir", type=Path, default=None)
    parser.add_argument("--tsmaster-app-channel", "--tsmaster-channel", dest="tsmaster_app_channel", type=int, default=0)
    parser.add_argument("--tsmaster-can-channel-count", type=int, default=None)
    parser.add_argument("--tsmaster-base-hw-channel", type=int, default=None)
    parser.add_argument("--tsmaster-hw-name", default="TC1016")
    parser.add_argument("--tsmaster-hw-subtype", type=int, default=11)
    parser.add_argument("--tsmaster-hw-index", type=int, default=0)
    parser.add_argument("--tsmaster-hw-channel", type=int, default=0)
    parser.add_argument(
        "--tsmaster-close-mode",
        choices=["normal", "skip"],
        default="normal",
        help="TSMaster 收口策略。CAN 默认 normal，若需复用 GUI 会话可显式改为 skip。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    trace_logger: TraceLogger | None = None
    adapter = None
    try:
        profile = load_profile(args.profile)
        if profile.bus.type != "CAN":
            raise HostToolError(ErrorCategory.PROFILE, "CAN flash CLI requires a CAN profile")
        flash_driver = load_firmware_image(
            args.flash_driver,
            start_address=profile.memory.flash_driver_ram,
            max_size=profile.memory.flash_driver_max_size,
        )
        app = load_firmware_image(args.app, start_address=profile.memory.app_start, max_size=profile.memory.app_size)

        print(f"profile={profile.name}")
        print(f"adapter={args.adapter}")
        print(f"flash_driver={flash_driver.path} start=0x{flash_driver.start_address:08X} size={flash_driver.size}")
        print(f"app={app.path} start=0x{app.start_address:08X} size={app.size}")
        print(f"start_in_bootloader={args.start_in_bootloader}")
        print(f"can_request_id=0x{profile.bus.request_id:X}")
        print(f"can_response_id=0x{profile.bus.response_id:X}")
        if profile.bus.functional_request_id is not None:
            print(f"can_functional_id=0x{profile.bus.functional_request_id:X}")
        print(f"can_baudrate={profile.bus.baudrate}")
        print(f"can_padding=0x{profile.bus.padding:02X}")
        print(f"max_transfer_payload={profile.uds.max_transfer_payload}")
        if args.adapter == "tsmaster":
            _print_tsmaster_mapping(args)

        if args.adapter == "tsmaster" and args.dry_run:
            print("DRY RUN: TSMaster CAN 参数检查完成，未发送任何硬件帧。")
            return 0

        trace_logger = TraceLogger(args.log_dir)
        if args.adapter == "fake":
            adapter = FakeCanAdapter.for_as5pr_flash_success(
                profile,
                flash_driver_data=flash_driver.data,
                app_data=app.data,
                start_in_bootloader=args.start_in_bootloader,
            )
            sleep_func = lambda _: None
        else:
            if input("真实 CAN 刷写会擦除并重写 App，输入 YES 继续: ") != "YES":
                print("CANCELLED")
                return 1
            adapter = TsmasterAdapter(
                dll_path=args.tsmaster_dll,
                app_name=args.tsmaster_app,
                project_dir=args.tsmaster_project_dir,
                app_channel=args.tsmaster_app_channel,
                hw_name=args.tsmaster_hw_name,
                hw_subtype=args.tsmaster_hw_subtype,
                hw_index=args.tsmaster_hw_index,
                hw_channel=args.tsmaster_hw_channel,
                can_channel_count=args.tsmaster_can_channel_count,
                base_hw_channel=args.tsmaster_base_hw_channel,
                baud_kbps=profile.bus.baudrate / 1000.0,
            )
            adapter.open_can()
            sleep_func = None

        transport = CanIsoTpTransport(
            adapter,
            profile,
            trace_logger=trace_logger,
            **({"sleep_func": sleep_func} if sleep_func is not None else {}),
        )
        workflow = FlashWorkflow(profile, transport, BusSession())
        workflow.run(flash_driver=flash_driver, app=app, start_in_bootloader=args.start_in_bootloader)
        print("FLASH SUCCESS")
        print(f"log={trace_logger.path}")
        return 0
    except HostToolError as exc:
        print(str(exc))
        return 2
    finally:
        if adapter is not None and hasattr(adapter, "close"):
            if args.adapter == "tsmaster" and args.tsmaster_close_mode == "skip":
                print("TSMaster close skipped")
            else:
                adapter.close()
        if trace_logger is not None:
            trace_logger.close()


def _print_tsmaster_mapping(args: argparse.Namespace) -> None:
    print(f"tsmaster_dll={args.tsmaster_dll}")
    print(f"tsmaster_app={args.tsmaster_app}")
    if args.tsmaster_project_dir is not None:
        print(f"tsmaster_project_dir={args.tsmaster_project_dir}")
    print(f"tsmaster_app_channel={args.tsmaster_app_channel}")
    if args.tsmaster_can_channel_count is not None:
        print(f"tsmaster_can_channel_count={args.tsmaster_can_channel_count}")
    if args.tsmaster_base_hw_channel is not None:
        print(f"tsmaster_base_hw_channel={args.tsmaster_base_hw_channel}")
    print(f"tsmaster_hw_name={args.tsmaster_hw_name}")
    print(f"tsmaster_hw_subtype={args.tsmaster_hw_subtype}")
    print(f"tsmaster_hw_index={args.tsmaster_hw_index}")
    print(f"tsmaster_hw_channel={args.tsmaster_hw_channel}")
    print(f"tsmaster_close_mode={args.tsmaster_close_mode}")


if __name__ == "__main__":
    raise SystemExit(main())
