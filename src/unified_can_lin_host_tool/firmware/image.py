from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError

SREC_SUFFIXES = {".s19", ".srec", ".mot"}


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


def load_firmware_image(path: Path, start_address: int, max_size: int) -> FirmwareImage:
    suffix = path.suffix.lower()
    if suffix in SREC_SUFFIXES:
        return load_srec_image(path, start_address, max_size)
    return load_bin_image(path, start_address, max_size)


def load_srec_image(path: Path, start_address: int, max_size: int) -> FirmwareImage:
    if not path.exists():
        raise HostToolError(ErrorCategory.FILE, f"firmware file not found: {path}")

    target_end = start_address + max_size
    segments: list[tuple[int, bytes]] = []

    for line_no, raw_line in enumerate(path.read_text(encoding="ascii").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        record_type, address, data = _parse_srec_line(line, line_no)
        if record_type not in {"S1", "S2", "S3"}:
            continue
        record_end = address + len(data)
        if record_end <= start_address or address >= target_end:
            continue
        if record_end > target_end:
            raise HostToolError(ErrorCategory.FILE, f"S19 target data exceeds limit at line {line_no}")

        slice_start = max(start_address, address)
        slice_end = min(target_end, record_end)
        data_start = slice_start - address
        data_end = slice_end - address
        segments.append((slice_start - start_address, data[data_start:data_end]))

    if not segments:
        raise HostToolError(ErrorCategory.FILE, f"S19 has no data at target address 0x{start_address:08X}")

    data = _join_contiguous_segments(segments, path)
    if len(data) > max_size:
        raise HostToolError(ErrorCategory.FILE, f"firmware size exceeds limit: {len(data)} > {max_size}")
    return FirmwareImage(path=path, start_address=start_address, data=data)


def _parse_srec_line(line: str, line_no: int) -> tuple[str, int, bytes]:
    if len(line) < 4 or line[0] != "S":
        raise HostToolError(ErrorCategory.FILE, f"invalid S-record at line {line_no}")

    record_type = line[:2]
    address_lengths = {
        "S0": 2,
        "S1": 2,
        "S2": 3,
        "S3": 4,
        "S5": 2,
        "S7": 4,
        "S8": 3,
        "S9": 2,
    }
    if record_type not in address_lengths:
        raise HostToolError(ErrorCategory.FILE, f"unsupported S-record type {record_type} at line {line_no}")

    try:
        count = int(line[2:4], 16)
        body = bytes.fromhex(line[4:])
    except ValueError as exc:
        raise HostToolError(ErrorCategory.FILE, f"invalid S-record hex at line {line_no}") from exc

    if len(body) != count:
        raise HostToolError(ErrorCategory.FILE, f"S-record byte count mismatch at line {line_no}")
    if ((count + sum(body)) & 0xFF) != 0xFF:
        raise HostToolError(ErrorCategory.FILE, f"S-record checksum mismatch at line {line_no}")

    address_len = address_lengths[record_type]
    if count < (address_len + 1):
        raise HostToolError(ErrorCategory.FILE, f"S-record length too short at line {line_no}")

    address = int.from_bytes(body[:address_len], "big")
    data = body[address_len:-1]
    return record_type, address, data


def _join_contiguous_segments(segments: list[tuple[int, bytes]], path: Path) -> bytes:
    data = bytearray()
    for offset, chunk in sorted(segments, key=lambda item: item[0]):
        if offset != len(data):
            raise HostToolError(ErrorCategory.FILE, f"S19 target data is not contiguous: {path}")
        data.extend(chunk)
    return bytes(data)


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        raise ValueError("alignment must be positive")
    return ((value + alignment - 1) // alignment) * alignment


def split_transfer_chunks(data: bytes, max_payload: int) -> Iterator[bytes]:
    if max_payload <= 0:
        raise ValueError("max_payload must be positive")
    for offset in range(0, len(data), max_payload):
        yield data[offset : offset + max_payload]
