from __future__ import annotations

import argparse
from pathlib import Path

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL, TsmasterAdapter
from unified_can_lin_host_tool.core.errors import HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_firmware_image
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="E68 LIN Bootloader M0 刷写 CLI。")
    parser.add_argument("--adapter", choices=["fake", "tsmaster"], required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--flash-driver", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument("--tsmaster-dll", "--dll", dest="tsmaster_dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--tsmaster-app", default="Codex_UnifiedHostTool")
    parser.add_argument("--tsmaster-project-dir", type=Path, default=None)
    parser.add_argument("--tsmaster-app-channel", "--tsmaster-channel", dest="tsmaster_app_channel", type=int, default=0)
    parser.add_argument("--tsmaster-hw-name", default="TC1016")
    parser.add_argument("--tsmaster-hw-subtype", type=int, default=11)
    parser.add_argument("--tsmaster-hw-index", type=int, default=0)
    parser.add_argument("--tsmaster-hw-channel", type=int, default=0)
    parser.add_argument(
        "--tsmaster-close-mode",
        choices=["normal", "skip"],
        default="skip",
        help="TSMaster 收口策略。默认 skip，避免 TC1016 在 OTA 后显式 stop/disconnect 造成后续 LIN 会话首帧无响应。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    trace_logger: TraceLogger | None = None
    adapter = None
    try:
        profile = load_profile(args.profile)
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
        if args.adapter == "tsmaster":
            _print_tsmaster_mapping(args)

        if args.adapter == "tsmaster" and args.dry_run:
            print("DRY RUN: TSMaster 参数检查完成，未发送任何硬件帧。")
            return 0

        trace_logger = TraceLogger(args.log_dir)
        if args.adapter == "fake":
            adapter = FakeLinAdapter.for_e68_flash_success(
                profile,
                flash_driver_data=flash_driver.data,
                app_data=app.data,
            )
            sleep_func = lambda _: None
        else:
            if input("真实刷写会擦除并重写 App，输入 YES 继续: ") != "YES":
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
                baud_kbps=profile.bus.baudrate / 1000.0,
            )
            adapter.open_lin()
            sleep_func = None

        transport = LinDiagTransport(
            adapter,
            profile,
            trace_logger=trace_logger,
            **({"sleep_func": sleep_func} if sleep_func is not None else {}),
        )
        workflow = FlashWorkflow(profile, transport, BusSession())
        workflow.run(flash_driver=flash_driver, app=app)
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
    print(f"tsmaster_hw_name={args.tsmaster_hw_name}")
    print(f"tsmaster_hw_subtype={args.tsmaster_hw_subtype}")
    print(f"tsmaster_hw_index={args.tsmaster_hw_index}")
    print(f"tsmaster_hw_channel={args.tsmaster_hw_channel}")
    print(f"tsmaster_close_mode={args.tsmaster_close_mode}")


if __name__ == "__main__":
    raise SystemExit(main())
