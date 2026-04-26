from __future__ import annotations

import argparse
from pathlib import Path

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL
from unified_can_lin_host_tool.adapters.tsmaster_virtual import E68FlashResponsePlan, TsmasterLinFifoSimAdapter
from unified_can_lin_host_tool.core.errors import HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_bin_image
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TSMaster LIN FIFO sim E68 完整刷写流程测试；不是物理 LIN 总线验证。")
    parser.add_argument("--dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--app-name", default="Codex_TsmasterLinFifoSimFlashE68")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--flash-driver", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    adapter = None
    trace_logger: TraceLogger | None = None
    try:
        profile = load_profile(args.profile)
        flash_driver = load_bin_image(
            args.flash_driver,
            start_address=profile.memory.flash_driver_ram,
            max_size=profile.memory.flash_driver_max_size,
        )
        app = load_bin_image(args.app, start_address=profile.memory.app_start, max_size=profile.memory.app_size)
        response_plan = E68FlashResponsePlan(profile, flash_driver_data=flash_driver.data, app_data=app.data)
        adapter = TsmasterLinFifoSimAdapter(
            profile,
            response_plan.responses_for,
            dll_path=args.dll,
            app_name=args.app_name,
        )
        adapter.open()
        trace_logger = TraceLogger(args.log_dir)
        transport = LinDiagTransport(adapter, profile, trace_logger=trace_logger)
        workflow = FlashWorkflow(profile, transport, BusSession())
        workflow.run(flash_driver=flash_driver, app=app)
    except HostToolError as exc:
        print(str(exc))
        return 2
    finally:
        if adapter is not None:
            adapter.close()
        if trace_logger is not None:
            trace_logger.close()

    print(f"dll={args.dll}")
    print("mode=LIN_FIFO_SIM_NOT_PHYSICAL_BUS")
    print("FLASH SUCCESS")
    print(f"log={trace_logger.path if trace_logger is not None else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
