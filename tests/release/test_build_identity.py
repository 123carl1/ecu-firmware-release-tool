import struct
import subprocess
from pathlib import Path

import pytest

from unified_can_lin_host_tool.release.build_identity import (
    BuildIdentity,
    decode_build_identity,
    read_build_identity,
    validate_release_build,
)
from unified_can_lin_host_tool.release.package import ResourceKind


STRUCT = struct.Struct("<4sHHIHH32s32s20s")


def _raw(kind: ResourceKind, *, build_id: bytes = b"B" * 32) -> bytes:
    return STRUCT.pack(
        b"RBID", 1, kind, 0x41503541, 1, 0,
        b"C" * 32, build_id, bytes.fromhex("01" * 20),
    )


def test_build_identity_v1_decodes_exact_100_bytes() -> None:
    identity = decode_build_identity(_raw(ResourceKind.APP))

    assert identity.resource_kind is ResourceKind.APP
    assert identity.project_code == 0x41503541
    assert identity.build_commit == bytes.fromhex("01" * 20)


def test_build_identity_rejects_reserved_or_trailing_bytes() -> None:
    with pytest.raises(ValueError):
        decode_build_identity(_raw(ResourceKind.BOOT) + b"\x00")
    damaged = bytearray(_raw(ResourceKind.BOOT))
    damaged[14] = 1
    with pytest.raises(ValueError, match="reserved"):
        decode_build_identity(bytes(damaged))


def test_release_build_requires_three_roles_with_one_shared_identity() -> None:
    identities = tuple(
        decode_build_identity(_raw(kind)) for kind in ResourceKind
    )

    validate_release_build(identities)


def test_release_build_rejects_mixed_build_ids() -> None:
    identities = (
        decode_build_identity(_raw(ResourceKind.BOOT)),
        decode_build_identity(_raw(ResourceKind.APP, build_id=b"X" * 32)),
        decode_build_identity(_raw(ResourceKind.FLASH_DRIVER)),
    )

    with pytest.raises(ValueError, match="one controlled build"):
        validate_release_build(identities)


def test_reads_allocated_identity_section_and_matches_bin(tmp_path: Path) -> None:
    identity = _raw(ResourceKind.APP)
    source = tmp_path / "identity.S"
    linker = tmp_path / "identity.ld"
    elf = tmp_path / "identity.elf"
    binary = tmp_path / "identity.bin"
    source.write_text(
        '.section .fw_identity,"a",%progbits\n.global fw_identity\n'
        'fw_identity:\n.incbin "identity.raw"\n'
        '.section .text,"ax"\n.global _start\n_start:\n nop\n',
        encoding="ascii",
    )
    (tmp_path / "identity.raw").write_bytes(identity)
    linker.write_text(
        "SECTIONS { . = 0x1000; .fw_identity : { *(.fw_identity) } "
        ".text : { *(.text) } }\n",
        encoding="ascii",
    )
    subprocess.run(
        ["arm-none-eabi-gcc", "-nostdlib", "-Wl,-T," + str(linker), str(source), "-o", str(elf)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["arm-none-eabi-objcopy", "-O", "binary", str(elf), str(binary)],
        check=True,
        capture_output=True,
    )

    assert read_build_identity(elf, binary) == decode_build_identity(identity)


def test_rejects_bin_that_differs_from_elf_identity(tmp_path: Path) -> None:
    elf = tmp_path / "missing.elf"
    binary = tmp_path / "image.bin"
    elf.write_bytes(b"not-elf")
    binary.write_bytes(_raw(ResourceKind.BOOT))

    with pytest.raises(ValueError, match="ELF"):
        read_build_identity(elf, binary)
