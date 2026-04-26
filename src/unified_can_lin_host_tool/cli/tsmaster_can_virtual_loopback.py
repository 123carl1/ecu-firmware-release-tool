from __future__ import annotations

import argparse

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL
from unified_can_lin_host_tool.adapters.tsmaster_virtual import run_vector_can_virtual_loopback
from unified_can_lin_host_tool.core.errors import HostToolError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证 TSMaster Vector VIRTUAL 双 CAN 通道 loopback。")
    parser.add_argument("--dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--app", default="Codex_TsmasterCanVirtualLoopback")
    parser.add_argument("--device", choices=["vector"], default="vector")
    parser.add_argument("--baud", type=float, default=500.0, help="CAN baudrate in kbps")
    parser.add_argument("--timeout-ms", type=int, default=500)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_vector_can_virtual_loopback(
            dll_path=args.dll,
            app_name=args.app,
            baud_kbps=args.baud,
            timeout_ms=args.timeout_ms,
        )
    except HostToolError as exc:
        print(str(exc))
        return 2

    print(f"dll={args.dll}")
    print("device=Vector VIRTUAL")
    for check in result.checks:
        data = check.data.hex(" ").upper()
        status = "PASS" if check.passed else "FAIL"
        print(f"{status} ch{check.tx_channel}->ch{check.rx_channel} id=0x{check.frame_id:X} data={data}")
    print(f"VECTOR_VIRTUAL_CAN_LOOPBACK_PASS={sum(1 for check in result.checks if check.passed)}/{len(result.checks)}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
