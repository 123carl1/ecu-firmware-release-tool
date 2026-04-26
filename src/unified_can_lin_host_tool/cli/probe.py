from __future__ import annotations

import argparse

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL, TsmasterAdapter
from unified_can_lin_host_tool.core.errors import HostToolError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="扫描 M0 支持的 CAN/LIN 硬件工具。")
    parser.add_argument("--adapter", choices=["tsmaster"], required=True)
    parser.add_argument("--dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--app", default="Codex_UnifiedHostTool_Probe")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        devices = TsmasterAdapter.probe(dll_path=args.dll, app_name=args.app)
    except HostToolError as exc:
        print(str(exc))
        return 2

    print(f"adapter={args.adapter}")
    print(f"dll={args.dll}")
    if not devices:
        print("devices=0")
        return 0

    for device in devices:
        print(
            f"[{device.index}] name={device.name} vendor={device.vendor} "
            f"serial={device.serial} device_type={device.device_type} device_index={device.device_index}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
