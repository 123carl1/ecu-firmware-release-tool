import struct

import pytest

from unified_can_lin_host_tool.release.build_identity import (
    BuildIdentity,
    decode_build_identity,
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
