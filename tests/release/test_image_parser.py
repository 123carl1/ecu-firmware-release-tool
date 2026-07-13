from pathlib import Path

import pytest

from unified_can_lin_host_tool.core.errors import HostToolError
from unified_can_lin_host_tool.release.image_parser import normalize_segments, parse_image
from unified_can_lin_host_tool.release.models import Segment


def _hex(address: int, kind: int, data: bytes = b"") -> str:
    raw = bytes((len(data), address >> 8, address & 0xFF, kind)) + data
    return ":" + (raw + bytes((-sum(raw) & 0xFF,))).hex().upper()


def _srec(kind: str, address: int, data: bytes = b"") -> str:
    address_size = {"0": 2, "1": 2, "2": 3, "3": 4, "5": 2, "6": 3, "7": 4, "8": 3, "9": 2}[kind]
    body = address.to_bytes(address_size, "big") + data
    count = len(body) + 1
    return "S" + kind + bytes((count,)).hex().upper() + body.hex().upper() + f"{(~(count + sum(body))) & 0xFF:02X}"


def test_parse_bin_requires_address_and_rejects_empty(tmp_path: Path) -> None:
    image = tmp_path / "app.bin"
    image.write_bytes(b"\x01\x02")
    with pytest.raises(HostToolError, match="bin_start"):
        parse_image(image)
    image.write_bytes(b"")
    with pytest.raises(HostToolError, match="empty"):
        parse_image(image, bin_start=0x1000)


def test_parse_bin_and_reject_address_overflow(tmp_path: Path) -> None:
    image = tmp_path / "app.bin"
    image.write_bytes(b"\x01\x02")
    assert parse_image(image, bin_start=0x1000) == (Segment(0x1000, b"\x01\x02"),)
    with pytest.raises(HostToolError, match="32-bit"):
        parse_image(image, bin_start=0xFFFFFFFF)


def test_hex_merges_adjacent_and_preserves_sparse_segments(tmp_path: Path) -> None:
    image = tmp_path / "app.hex"
    image.write_text("\n".join((_hex(0, 4, b"\x00\x01"), _hex(0, 0, b"AB"), _hex(2, 0, b"CD"), _hex(8, 0, b"Z"), _hex(0, 5, bytes(4)), _hex(0, 1))), encoding="ascii")
    assert parse_image(image) == (Segment(0x10000, b"ABCD"), Segment(0x10008, b"Z"))


def test_hex_ignores_zero_length_data_record_when_other_data_exists(tmp_path: Path) -> None:
    image = tmp_path / "zero.hex"
    image.write_text("\n".join((_hex(0, 0), _hex(1, 0, b"A"), _hex(0, 1))), encoding="ascii")
    assert parse_image(image) == (Segment(1, b"A"),)


@pytest.mark.parametrize("suffix", [".hex", ".s19"])
def test_text_image_read_and_decode_errors_are_file_errors(tmp_path: Path, suffix: str) -> None:
    missing = tmp_path / f"missing{suffix}"
    with pytest.raises(HostToolError) as missing_error:
        parse_image(missing)
    assert missing_error.value.category.value == "file"

    invalid = tmp_path / f"invalid{suffix}"
    invalid.write_bytes(b"\xFF")
    with pytest.raises(HostToolError) as decode_error:
        parse_image(invalid)
    assert decode_error.value.category.value == "file"


@pytest.mark.parametrize("mutation,match", [("checksum", "checksum"), ("count", "byte count"), ("unknown", "record type"), ("no_eof", "EOF")])
def test_hex_rejects_malformed_records(tmp_path: Path, mutation: str, match: str) -> None:
    data = _hex(0, 0, b"A")
    lines = [data, _hex(0, 1)]
    if mutation == "checksum": lines[0] = data[:-2] + "00"
    elif mutation == "count": lines[0] = ":02" + data[3:]
    elif mutation == "unknown": lines[0] = _hex(0, 6)
    else: lines.pop()
    (tmp_path / "bad.hex").write_text("\n".join(lines), encoding="ascii")
    with pytest.raises(HostToolError, match=match): parse_image(tmp_path / "bad.hex")


def test_hex_rejects_identical_overlap(tmp_path: Path) -> None:
    path = tmp_path / "overlap.hex"
    path.write_text("\n".join((_hex(0, 0, b"AB"), _hex(1, 0, b"B"), _hex(0, 1))), encoding="ascii")
    with pytest.raises(HostToolError, match="overlap"): parse_image(path)


def test_s19_parses_data_and_ignores_non_data_records(tmp_path: Path) -> None:
    path = tmp_path / "app.s19"
    path.write_text("\n".join((_srec("0", 0, b"HDR"), _srec("1", 0x1000, b"AB"), _srec("1", 0x2000, b"C"), _srec("5", 2), _srec("9", 0x1000))), encoding="ascii")
    assert parse_image(path) == (Segment(0x1000, b"AB"), Segment(0x2000, b"C"))


def test_s19_rejects_count_checksum_and_overlap(tmp_path: Path) -> None:
    for name, text, match in (("count", "S10510004100", "count"), ("sum", _srec("1", 0x1000, b"A")[:-2] + "00", "checksum"), ("overlap", "\n".join((_srec("1", 0x1000, b"AB"), _srec("1", 0x1001, b"B"), _srec("9", 0x1000))), "overlap")):
        path = tmp_path / f"{name}.s19"; path.write_text(text, encoding="ascii")
        with pytest.raises(HostToolError, match=match): parse_image(path)


@pytest.mark.parametrize(
    "lines,match",
    [
        (lambda: [_srec("1", 0x1000, b"A")], "termination"),
        (lambda: [_srec("1", 0x1000, b"A"), _srec("9", 0x1000), _srec("1", 0x1001, b"B")], "after"),
        (lambda: [_srec("1", 0x1000, b"A"), _srec("5", 2), _srec("9", 0x1000)], "declared count"),
        (lambda: [_srec("1", 0x1000, b"A"), _srec("8", 0x1000)], "termination type"),
        (lambda: [_srec("1", 0x1000, b"A"), _srec("9", 0x2000)], "entry address"),
    ],
)
def test_srecord_rejects_invalid_control_records(tmp_path: Path, lines, match: str) -> None:
    path = tmp_path / "bad.s19"
    path.write_text("\n".join(lines()), encoding="ascii")
    with pytest.raises(HostToolError, match=match):
        parse_image(path)


def test_normalize_fills_gaps_and_validates_arguments() -> None:
    segments = (Segment(0x10, b"AB"), Segment(0x14, b"Z"))
    assert normalize_segments(segments, start=0x10, end=0x15, gap_fill=0xFF) == b"AB\xFF\xFFZ"
    with pytest.raises(HostToolError, match="range"): normalize_segments(segments, start=0x11, end=0x15, gap_fill=0)
    with pytest.raises(HostToolError, match="gap_fill"): normalize_segments(segments, start=0x10, end=0x15, gap_fill=256)
    with pytest.raises(HostToolError, match="range"): normalize_segments(segments, start=True, end=0x15, gap_fill=0)
    with pytest.raises(HostToolError, match="range"): normalize_segments(segments, start=0x10, end=True, gap_fill=0)
