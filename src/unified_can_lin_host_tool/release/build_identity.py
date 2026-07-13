"""固件镜像内 `BuildIdentityV1` 的严格解析与同构建校验。"""

from dataclasses import dataclass
import struct

from .package import ResourceKind


BUILD_IDENTITY = struct.Struct("<4sHHIHH32s32s20s")


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
