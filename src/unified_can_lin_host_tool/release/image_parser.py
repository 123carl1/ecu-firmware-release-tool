from pathlib import Path

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError

from .models import Segment


def _error(message: str) -> HostToolError:
    return HostToolError(ErrorCategory.FILE, message)


def _finish(segments: list[Segment]) -> tuple[Segment, ...]:
    if not segments:
        raise _error("image contains no data")
    ordered = sorted(segments, key=lambda item: item.address)
    merged: list[Segment] = []
    for segment in ordered:
        if segment.address < 0 or segment.address + len(segment.data) > 0x100000000:
            raise _error("segment exceeds 32-bit address space")
        if not segment.data:
            raise _error("empty segment")
        if merged:
            previous = merged[-1]
            previous_end = previous.address + len(previous.data)
            if segment.address < previous_end:
                raise _error("segment overlap")
            if segment.address == previous_end:
                merged[-1] = Segment(previous.address, previous.data + segment.data)
                continue
        merged.append(segment)
    return tuple(merged)


def _decode_text(source: bytes, format_name: str) -> list[str]:
    try:
        return source.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise _error(f"{format_name} is not ASCII text") from exc


def _parse_hex(source: bytes) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    base = 0
    eof = False
    for line_no, text in enumerate(_decode_text(source, "Intel HEX"), 1):
        if not text or not text.startswith(":"):
            raise _error(f"Intel HEX format error at line {line_no}")
        try:
            raw = bytes.fromhex(text[1:])
        except ValueError as exc:
            raise _error(f"Intel HEX format error at line {line_no}") from exc
        if len(raw) < 5 or len(raw) != raw[0] + 5:
            raise _error(f"Intel HEX byte count mismatch at line {line_no}")
        if sum(raw) & 0xFF:
            raise _error(f"Intel HEX checksum mismatch at line {line_no}")
        count, address, kind, data = raw[0], int.from_bytes(raw[1:3], "big"), raw[3], raw[4:-1]
        if eof:
            raise _error(f"record after EOF at line {line_no}")
        if kind == 0:
            if data:
                segments.append(Segment(base + address, data))
        elif kind == 1:
            if count or address: raise _error(f"invalid EOF at line {line_no}")
            eof = True
        elif kind == 2:
            if count != 2 or address: raise _error(f"invalid extended segment address at line {line_no}")
            base = int.from_bytes(data, "big") << 4
        elif kind == 4:
            if count != 2 or address: raise _error(f"invalid extended linear address at line {line_no}")
            base = int.from_bytes(data, "big") << 16
        elif kind in (3, 5):
            if count != 4 or address: raise _error(f"invalid entry address at line {line_no}")
        else:
            raise _error(f"unknown Intel HEX record type {kind:02X}")
    if not eof:
        raise _error("Intel HEX missing EOF")
    return _finish(segments)


def _parse_srec(source: bytes) -> tuple[Segment, ...]:
    sizes = {"0": 2, "1": 2, "2": 3, "3": 4, "5": 2, "6": 3, "7": 4, "8": 3, "9": 2}
    segments: list[Segment] = []
    for line_no, text in enumerate(_decode_text(source, "S-record"), 1):
        if len(text) < 4 or text[:1] != "S" or text[1:2] not in sizes:
            raise _error(f"unsupported S-record type at line {line_no}")
        try: raw = bytes.fromhex(text[2:])
        except ValueError as exc: raise _error(f"S-record format error at line {line_no}") from exc
        if not raw or len(raw) != raw[0] + 1:
            raise _error(f"S-record count mismatch at line {line_no}")
        if (sum(raw) & 0xFF) != 0xFF:
            raise _error(f"S-record checksum mismatch at line {line_no}")
        address_size = sizes[text[1]]
        if raw[0] < address_size + 1:
            raise _error(f"S-record count too short at line {line_no}")
        address = int.from_bytes(raw[1:1 + address_size], "big")
        data = raw[1 + address_size:-1]
        if text[1] in "123" and data: segments.append(Segment(address, data))
    return _finish(segments)


def _parse_image_bytes(path: Path, source: bytes, *, bin_start: int | None = None) -> tuple[Segment, ...]:
    suffix = path.suffix.lower()
    if suffix == ".bin":
        if bin_start is None: raise _error("bin_start is required for BIN")
        if not isinstance(bin_start, int) or isinstance(bin_start, bool) or bin_start < 0:
            raise _error("bin_start must be a non-negative address")
        return _finish([Segment(bin_start, source)])
    if suffix in (".hex", ".ihex"): return _parse_hex(source)
    if suffix in (".s19", ".srec", ".s28", ".s37"): return _parse_srec(source)
    raise _error(f"unsupported image format: {suffix}")


def parse_image(path: Path, *, bin_start: int | None = None) -> tuple[Segment, ...]:
    path = Path(path)
    try: source = path.read_bytes()
    except OSError as exc: raise _error(f"cannot read image: {path}") from exc
    return _parse_image_bytes(path, source, bin_start=bin_start)


def normalize_segments(segments, *, start: int, end: int, gap_fill: int) -> bytes:
    if not isinstance(gap_fill, int) or isinstance(gap_fill, bool) or not 0 <= gap_fill <= 0xFF:
        raise _error("gap_fill must be a byte")
    if (not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int)
            or isinstance(end, bool) or start < 0 or end < start or end > 0x100000000):
        raise _error("invalid normalization range")
    canonical = _finish(list(segments))
    if canonical[0].address < start or canonical[-1].address + len(canonical[-1].data) > end:
        raise _error("normalization range does not cover all segments")
    output = bytearray([gap_fill]) * (end - start)
    for segment in canonical:
        offset = segment.address - start
        output[offset:offset + len(segment.data)] = segment.data
    return bytes(output)
