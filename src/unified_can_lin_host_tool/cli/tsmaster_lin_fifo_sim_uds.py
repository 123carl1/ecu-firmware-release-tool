from __future__ import annotations

import argparse
from pathlib import Path

from unified_can_lin_host_tool.adapters.tsmaster import DEFAULT_TSMASTER_DLL
from unified_can_lin_host_tool.adapters.tsmaster_virtual import TsmasterLinFifoSimAdapter
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TSMaster LIN FIFO sim 单帧 UDS 验证；不是物理 LIN 总线验证。")
    parser.add_argument("--dll", default=DEFAULT_TSMASTER_DLL)
    parser.add_argument("--app-name", default="Codex_TsmasterLinFifoSimUds")
    parser.add_argument("--profile", type=Path, default=Path("profiles/e68_lin_bootloader.yaml"))
    parser.add_argument("--request", default="10 01", help="UDS request payload, hex bytes")
    parser.add_argument("--response", default="50 01", help="Simulated positive response payload, hex bytes")
    parser.add_argument("--expect-prefix", default=None, help="Expected response prefix, defaults to --response")
    parser.add_argument("--timeout-ms", type=int, default=500)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    adapter = None
    try:
        profile = load_profile(args.profile)
        request = _parse_hex_bytes(args.request)
        response = _parse_hex_bytes(args.response)
        expect_prefix = _parse_hex_bytes(args.expect_prefix) if args.expect_prefix else response

        adapter = TsmasterLinFifoSimAdapter(
            profile,
            lambda payload: [response] if payload == request else _raise_unexpected(payload),
            dll_path=args.dll,
            app_name=args.app_name,
        )
        adapter.open()
        transport = LinDiagTransport(adapter, profile)
        uds_response = transport.request(request, expect_prefix=expect_prefix, timeout_ms=args.timeout_ms)
    except HostToolError as exc:
        print(str(exc))
        return 2
    finally:
        if adapter is not None:
            adapter.close()

    print(f"dll={args.dll}")
    print("mode=LIN_FIFO_SIM_NOT_PHYSICAL_BUS")
    print(f"request={request.hex(' ').upper()}")
    print(f"response={uds_response.payload.hex(' ').upper()}")
    print("LIN_FIFO_SIM_UDS_OK")
    return 0


def _parse_hex_bytes(value: str) -> bytes:
    try:
        return bytes(int(item, 16) for item in value.replace(",", " ").split())
    except ValueError as exc:
        raise HostToolError(ErrorCategory.PROFILE, f"invalid hex bytes: {value}") from exc


def _raise_unexpected(payload: bytes) -> list[bytes]:
    raise HostToolError(ErrorCategory.UDS, f"unexpected LIN FIFO sim request: {payload.hex(' ')}")


if __name__ == "__main__":
    raise SystemExit(main())
