"""固件镜像内 `BuildIdentityV1` 的严格解析与同构建校验。"""

from dataclasses import dataclass
from pathlib import Path
import struct

from .package import ResourceKind


BUILD_IDENTITY = struct.Struct("<4sHHIHH32s32s20s")
_ELF32_HEADER = struct.Struct("<16sHHIIIIIHHHHHH")
_ELF32_PROGRAM = struct.Struct("<IIIIIIII")
_ELF32_SECTION = struct.Struct("<IIIIIIIIII")


@dataclass(frozen=True)
class BuildIdentity:
    resource_kind: ResourceKind
    project_code: int
    config_version: int
    config_digest: bytes
    build_id: bytes
    build_commit: bytes


def decode_build_identity(payload: bytes) -> BuildIdentity:
    if type(payload) is not bytes or len(payload) != BUILD_IDENTITY.size:
        raise ValueError("BuildIdentityV1 must be exactly 100 bytes")
    magic, schema, kind_raw, project_code, config_version, reserved, config_digest, build_id, commit = (
        BUILD_IDENTITY.unpack(payload)
    )
    if magic != b"RBID" or schema != 1:
        raise ValueError("unsupported BuildIdentity header")
    if reserved != 0:
        raise ValueError("BuildIdentity reserved field must be zero")
    try:
        kind = ResourceKind(kind_raw)
    except ValueError as exc:
        raise ValueError("unknown BuildIdentity resource kind") from exc
    return BuildIdentity(
        kind, project_code, config_version, config_digest, build_id, commit
    )


def validate_release_build(identities: tuple[BuildIdentity, ...]) -> None:
    if len(identities) != 3:
        raise ValueError("release build must contain exactly three identities")
    if tuple(item.resource_kind for item in identities) != tuple(ResourceKind):
        raise ValueError("release identities must be ordered Boot, App, FlashDriver")
    shared = {
        (
            item.project_code,
            item.config_version,
            item.config_digest,
            item.build_id,
            item.build_commit,
        )
        for item in identities
    }
    if len(shared) != 1:
        raise ValueError("release resources do not come from one controlled build")


def _slice(data: bytes, offset: int, size: int, field: str) -> bytes:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError(f"ELF {field} is out of bounds")
    return data[offset:offset + size]


def read_build_identity(elf_path: Path, bin_path: Path) -> BuildIdentity:
    """从 ELF 装载节定位身份，并与 objcopy BIN 的对应字节交叉验证。"""
    elf = Path(elf_path).read_bytes()
    if len(elf) < _ELF32_HEADER.size:
        raise ValueError("ELF header is truncated")
    header = _ELF32_HEADER.unpack_from(elf)
    ident = header[0]
    if ident[:4] != b"\x7fELF" or ident[4] != 1 or ident[5] != 1:
        raise ValueError("ELF must be 32-bit little-endian")
    phoff, shoff = header[5], header[6]
    phentsize, phnum = header[9], header[10]
    shentsize, shnum, shstrndx = header[11], header[12], header[13]
    if phentsize != _ELF32_PROGRAM.size or shentsize != _ELF32_SECTION.size:
        raise ValueError("ELF table entry size is unsupported")
    if shnum == 0 or shstrndx >= shnum:
        raise ValueError("ELF section table is invalid")

    sections = [
        _ELF32_SECTION.unpack(_slice(elf, shoff + index * shentsize, shentsize, "section header"))
        for index in range(shnum)
    ]
    names_header = sections[shstrndx]
    names = _slice(elf, names_header[4], names_header[5], "section name table")

    def section_name(offset: int) -> bytes:
        if offset >= len(names):
            raise ValueError("ELF section name is out of bounds")
        end = names.find(b"\0", offset)
        if end < 0:
            raise ValueError("ELF section name is unterminated")
        return names[offset:end]

    matches = [item for item in sections if section_name(item[0]) == b".fw_identity"]
    if len(matches) != 1:
        raise ValueError("ELF must contain exactly one .fw_identity section")
    section = matches[0]
    _, section_type, flags, address, file_offset, size, *_ = section
    if section_type != 1 or flags & 0x2 == 0 or size != BUILD_IDENTITY.size:
        raise ValueError("ELF release identity section attributes are invalid")
    identity_raw = _slice(elf, file_offset, size, "release identity section")

    programs = [
        _ELF32_PROGRAM.unpack(_slice(elf, phoff + index * phentsize, phentsize, "program header"))
        for index in range(phnum)
    ]
    loadable = [item for item in programs if item[0] == 1 and item[4] > 0]
    containing = [
        item for item in loadable
        if item[2] <= address and size <= item[4] - (address - item[2])
    ]
    if len(containing) != 1 or not loadable:
        raise ValueError("ELF release identity is not in one loadable segment")
    segment = containing[0]
    identity_lma = segment[3] + (address - segment[2])
    allocated_sections = [
        item for item in sections
        if item[1] == 1 and item[2] & 0x2 != 0 and item[5] > 0
    ]
    section_lmas: list[int] = []
    for allocated in allocated_sections:
        allocated_address, allocated_size = allocated[3], allocated[5]
        owners = [
            item for item in loadable
            if item[2] <= allocated_address
            and allocated_size <= item[4] - (allocated_address - item[2])
        ]
        if len(owners) != 1:
            raise ValueError("ELF allocated section has ambiguous load address")
        owner = owners[0]
        section_lmas.append(owner[3] + (allocated_address - owner[2]))
    if not section_lmas:
        raise ValueError("ELF has no allocated file-backed sections")
    image_base = min(section_lmas)
    bin_offset = identity_lma - image_base
    binary = Path(bin_path).read_bytes()
    if _slice(binary, bin_offset, size, "BIN identity") != identity_raw:
        raise ValueError("ELF and BIN release identity differ")
    return decode_build_identity(identity_raw)
