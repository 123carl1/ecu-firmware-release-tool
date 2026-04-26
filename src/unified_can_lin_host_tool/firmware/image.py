from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError


@dataclass(frozen=True)
class FirmwareImage:
    path: Path
    start_address: int
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def end_address(self) -> int:
        return self.start_address + self.size


def load_bin_image(path: Path, start_address: int, max_size: int) -> FirmwareImage:
    if not path.exists():
        raise HostToolError(ErrorCategory.FILE, f"firmware file not found: {path}")
    data = path.read_bytes()
    if len(data) > max_size:
        raise HostToolError(ErrorCategory.FILE, f"firmware size exceeds limit: {len(data)} > {max_size}")
    return FirmwareImage(path=path, start_address=start_address, data=data)


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        raise ValueError("alignment must be positive")
    return ((value + alignment - 1) // alignment) * alignment


def split_transfer_chunks(data: bytes, max_payload: int) -> Iterator[bytes]:
    if max_payload <= 0:
        raise ValueError("max_payload must be positive")
    for offset in range(0, len(data), max_payload):
        yield data[offset : offset + max_payload]

