from dataclasses import replace
import struct
from pathlib import Path

import pytest

from unified_can_lin_host_tool.release.package import ResourceKind
from unified_can_lin_host_tool.release.runtime_ota import (
    prepare_as5pr_app,
    validate_runtime_ota_package,
)


def _hex_record(address: int, kind: int, data: bytes = b"") -> str:
    raw = bytes((len(data), address >> 8, address & 0xFF, kind)) + data
    return ":" + (raw + bytes((-sum(raw) & 0xFF,))).hex().upper()


def _write_hex(path: Path, former_identity_area: bytes = b"") -> None:
    vectors = struct.pack("<II", 0x20007FC0, 0x00007129)
    lines = [_hex_record(0x7000, 0, vectors)]
    for base, data in ((0x70C0, former_identity_area), (0x7128, b"\x00\xBF\x00\xBF")):
        if not data:
            continue
        for offset in range(0, len(data), 16):
            lines.append(_hex_record(base + offset, 0, data[offset:offset + 16]))
    lines.append(_hex_record(0, 1))
    path.write_text("\n".join(lines), encoding="ascii")


def _srec(kind: str, address: int, data: bytes = b"") -> str:
    address_size = {"1": 2, "9": 2}[kind]
    body = address.to_bytes(address_size, "big") + data
    count = len(body) + 1
    return "S" + kind + bytes((count,)).hex().upper() + body.hex().upper() + f"{(~(count + sum(body))) & 0xFF:02X}"


def _write_s19(path: Path, former_identity_area: bytes = b"") -> None:
    vectors = struct.pack("<II", 0x20007FC0, 0x00007129)
    lines = [_srec("1", 0x7000, vectors)]
    for base, data in ((0x70C0, former_identity_area), (0x7128, b"\x00\xBF\x00\xBF")):
        if not data:
            continue
        for offset in range(0, len(data), 16):
            lines.append(_srec("1", base + offset, data[offset:offset + 16]))
    lines.append(_srec("9", 0x7129))
    path.write_text("\n".join(lines), encoding="ascii")


@pytest.mark.parametrize("suffix,writer", [(".hex", _write_hex), (".s19", _write_s19)])
def test_native_app_is_checked_signed_in_memory_and_combined_with_internal_driver(
    tmp_path: Path, suffix: str, writer,
) -> None:
    source = tmp_path / f"app{suffix}"
    writer(source)

    prepared = prepare_as5pr_app(source)

    assert prepared.source_path == source.resolve()
    assert tuple(item.kind for item in prepared.resources) == (
        ResourceKind.APP, ResourceKind.FLASH_DRIVER,
    )
    app, driver = prepared.resources
    magic, payload_size, target_id, version = struct.unpack("<IIII", app.content[-48:-32])
    assert (magic, payload_size, target_id, version) == (
        0xA5A5A5A5, len(app.content) - 48, 0x41503541, 1,
    )
    assert app.content[8] == 0xFF
    assert driver.load_address == 0x20001000
    assert len(driver.content) <= 0x2000
    assert list(tmp_path.iterdir()) == [source]


def test_native_app_without_build_identity_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "native.hex"
    _write_hex(source)

    prepared = prepare_as5pr_app(source)

    assert prepared.source_path == source.resolve()


def test_native_app_rejects_invalid_vector_and_out_of_range_segment(tmp_path: Path) -> None:
    source = tmp_path / "bad.hex"
    _write_hex(source)
    text = source.read_text(encoding="ascii")
    bad_vectors = struct.pack("<II", 0x10000000, 0x00007128)
    source.write_text(text.replace(_hex_record(0x7000, 0, struct.pack("<II", 0x20007FC0, 0x7129)),
                                   _hex_record(0x7000, 0, bad_vectors)), encoding="ascii")
    with pytest.raises(ValueError, match="vector"):
        prepare_as5pr_app(source)

    outside = tmp_path / "outside.hex"
    _write_hex(outside)
    lines = outside.read_text(encoding="ascii").splitlines()
    lines.insert(-1, _hex_record(0x2000, 0, b"X"))
    outside.write_text("\n".join(lines), encoding="ascii")
    with pytest.raises(ValueError, match="App range"):
        prepare_as5pr_app(outside)


def test_native_app_rejects_bin(tmp_path: Path) -> None:
    binary = tmp_path / "app.bin"
    binary.write_bytes(b"raw")
    with pytest.raises(ValueError, match="HEX or S-record"):
        prepare_as5pr_app(binary)


def test_runtime_package_is_revalidated_before_bus_use(tmp_path: Path) -> None:
    source = tmp_path / "app.hex"
    _write_hex(source)
    prepared = prepare_as5pr_app(source)
    validate_runtime_ota_package(prepared)

    app, driver = prepared.resources
    corrupt = replace(app, content=app.content[:-1] + bytes([app.content[-1] ^ 1]))
    with pytest.raises(ValueError, match="HMAC"):
        validate_runtime_ota_package(replace(prepared, resources=(corrupt, driver)))
    with pytest.raises(ValueError, match="release identity"):
        validate_runtime_ota_package(replace(prepared, release_set_id="0" * 64))
