import struct
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


def write_identity_fixture(tmp_path: Path, identity: bytes) -> tuple[Path, Path]:
    assert len(identity) == 100
    elf_header = struct.Struct("<16sHHIIIIIHHHHHH")
    program_header = struct.Struct("<IIIIIIII")
    section_header = struct.Struct("<IIIIIIIIII")
    names = b"\0.shstrtab\0.fw_identity\0.text\0"
    identity_name = names.index(b".fw_identity")
    text_name = names.index(b".text")
    shstr_name = names.index(b".shstrtab")
    phoff, data_offset, names_offset, shoff = elf_header.size, 0x100, 0x180, 0x200
    image = bytearray(shoff + 4 * section_header.size)
    ident = b"\x7fELF" + bytes([1, 1, 1]) + b"\0" * 9
    elf_header.pack_into(
        image, 0, ident, 2, 40, 1, 0x1064, phoff, shoff, 0,
        elf_header.size, program_header.size, 1, section_header.size, 4, 1,
    )
    program_header.pack_into(
        image, phoff, 1, data_offset, 0x1000, 0x1000, 101, 101, 5, 4,
    )
    image[data_offset:data_offset + 100] = identity
    image[data_offset + 100] = 0
    image[names_offset:names_offset + len(names)] = names
    sections = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (shstr_name, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0),
        (identity_name, 1, 2, 0x1000, data_offset, 100, 0, 0, 4, 0),
        (text_name, 1, 6, 0x1064, data_offset + 100, 1, 0, 0, 1, 0),
    ]
    for index, values in enumerate(sections):
        section_header.pack_into(image, shoff + index * section_header.size, *values)
    elf_path = tmp_path / "identity.elf"
    bin_path = tmp_path / "identity.bin"
    elf_path.write_bytes(image)
    bin_path.write_bytes(identity + b"\0")
    assert len(image) == shoff + 4 * section_header.size
    assert bin_path.read_bytes()[:100] == identity
    return elf_path, bin_path


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
    elf, binary = write_identity_fixture(tmp_path, identity)

    assert read_build_identity(elf, binary) == decode_build_identity(identity)


def test_rejects_bin_that_differs_from_elf_identity(tmp_path: Path) -> None:
    identity = _raw(ResourceKind.BOOT)
    elf, binary = write_identity_fixture(tmp_path, identity)
    damaged = bytearray(binary.read_bytes())
    damaged[10] ^= 1
    binary.write_bytes(damaged)

    with pytest.raises(ValueError, match="differ"):
        read_build_identity(elf, binary)
