"""`.erel` V1 单文件发布资源包编解码。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import hashlib
import os
from pathlib import Path
import re
import struct
import tempfile
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .project_config import ProjectCode, ProjectReleaseConfig, compute_config_digest, get_project_config


HEADER = struct.Struct("<4sHHIIHH32s32s40sQ")
ENTRY = struct.Struct("<HHIIIII32s")
SIGNATURE_PREFIX = struct.Struct("<4sI")
HEADER_SIZE = 300
SIGNATURE_SIZE = 72
MAX_U32 = 0xFFFFFFFF


class ResourceKind(IntEnum):
    BOOT = 1
    APP = 2
    FLASH_DRIVER = 3


@dataclass(frozen=True)
class ReleaseResource:
    kind: ResourceKind
    target_id: int
    load_address: int
    auth_version: int
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ResourceKind):
            raise TypeError("kind must be ResourceKind")
        for field in ("target_id", "load_address", "auth_version"):
            value = getattr(self, field)
            if type(value) is not int or not 0 <= value <= MAX_U32:
                raise ValueError(f"{field} must be a u32")
        if type(self.content) is not bytes or not self.content:
            raise ValueError("resource content must be non-empty bytes")


@dataclass(frozen=True)
class VerifiedReleasePackage:
    source_path: Path
    release_set_id: str
    project: ProjectCode
    project_code: int
    config_version: int
    config_digest: bytes
    build_id: bytes
    build_commit: str
    build_timestamp: int
    key_id: int
    resources: tuple[ReleaseResource, ReleaseResource, ReleaseResource]


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _validate_resource_roles(
    resources: tuple[ReleaseResource, ReleaseResource, ReleaseResource],
    config: ProjectReleaseConfig,
) -> None:
    if tuple(item.kind for item in resources) != tuple(ResourceKind):
        raise ValueError("resources must be ordered Boot, App, FlashDriver")
    expected = (
        (config.project_code, 0),
        (config.authentication.app_target_id, config.authentication.app_version),
        (
            config.authentication.flash_driver_target_id,
            config.authentication.flash_driver_version,
        ),
    )
    for resource, (target_id, auth_version) in zip(resources, expected, strict=True):
        if (resource.target_id, resource.auth_version) != (target_id, auth_version):
            raise ValueError(f"{resource.kind.name} identity differs from project configuration")


def encode_release_package(
    resources: tuple[ReleaseResource, ReleaseResource, ReleaseResource],
    config: ProjectReleaseConfig,
    *,
    build_id: bytes,
    build_commit: str,
    build_timestamp: int,
    key_id: int,
    private_key: Ed25519PrivateKey,
) -> bytes:
    if not isinstance(config, ProjectReleaseConfig):
        raise TypeError("config must be ProjectReleaseConfig")
    if type(build_id) is not bytes or len(build_id) != 32:
        raise ValueError("build_id must be 32 bytes")
    if not re.fullmatch(r"[0-9a-f]{40}", build_commit):
        raise ValueError("build_commit must be 40 lowercase hexadecimal characters")
    for value, field in ((build_timestamp, "build_timestamp"), (key_id, "key_id")):
        if type(value) is not int or not 0 <= value <= MAX_U32:
            raise ValueError(f"{field} must be a u32")
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("private_key must be Ed25519PrivateKey")
    _validate_resource_roles(resources, config)

    offsets: list[int] = []
    cursor = HEADER_SIZE
    for resource in resources:
        offsets.append(cursor)
        cursor = _align4(cursor + len(resource.content))
    package_size = cursor + SIGNATURE_SIZE
    if package_size > MAX_U32:
        raise ValueError("package exceeds u32 size")

    header = HEADER.pack(
        b"EREL", 1, HEADER_SIZE, package_size, config.project_code,
        config.config_version, 3, compute_config_digest(config), build_id,
        build_commit.encode("ascii"), build_timestamp,
    )
    table = b"".join(
        ENTRY.pack(
            resource.kind, 0, resource.target_id, resource.load_address,
            offset, len(resource.content), resource.auth_version,
            hashlib.sha256(resource.content).digest(),
        )
        for resource, offset in zip(resources, offsets, strict=True)
    )
    body = bytearray(header + table)
    for resource, offset in zip(resources, offsets, strict=True):
        if len(body) < offset:
            body.extend(b"\x00" * (offset - len(body)))
        body.extend(resource.content)
        body.extend(b"\x00" * (_align4(len(body)) - len(body)))
    prefix = SIGNATURE_PREFIX.pack(b"SIG1", key_id)
    body.extend(prefix)
    body.extend(private_key.sign(bytes(body)))
    if len(body) != package_size:
        raise AssertionError("encoded package length differs from header")
    return bytes(body)


def _public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if type(value) is not bytes or len(value) != 32:
        raise ValueError("Ed25519 public key must be 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(value)


def load_verified_release_package(
    path: Path,
    selected_project: ProjectCode,
    public_keys: Mapping[int, bytes | Ed25519PublicKey],
) -> VerifiedReleasePackage:
    source = Path(path)
    raw = source.read_bytes()
    if len(raw) < HEADER_SIZE + SIGNATURE_SIZE or len(raw) > MAX_U32:
        raise ValueError("package length is invalid")
    try:
        (magic, schema, header_size, package_size, project_code, config_version,
         entry_count, config_digest, build_id, commit_raw, build_timestamp) = HEADER.unpack_from(raw)
    except struct.error as exc:
        raise ValueError("package header is truncated") from exc
    if (magic, schema, header_size, entry_count) != (b"EREL", 1, HEADER_SIZE, 3):
        raise ValueError("unsupported package header")
    if package_size != len(raw):
        raise ValueError("actual package length differs from packageSize")
    signature_offset = package_size - SIGNATURE_SIZE
    signature_magic, key_id = SIGNATURE_PREFIX.unpack_from(raw, signature_offset)
    if signature_magic != b"SIG1":
        raise ValueError("signature trailer magic is invalid")
    try:
        public_key = _public_key(public_keys[key_id])
    except KeyError as exc:
        raise ValueError("unknown signing key") from exc
    try:
        public_key.verify(raw[-64:], raw[:-64])
    except InvalidSignature as exc:
        raise ValueError("signature verification failed") from exc

    config = get_project_config(selected_project)
    if project_code != config.project_code:
        raise ValueError("selected project differs from package")
    if config_version != config.config_version or config_digest != compute_config_digest(config):
        raise ValueError("package project configuration differs from program")
    try:
        build_commit = commit_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("build commit is not ASCII") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", build_commit):
        raise ValueError("build commit is not canonical")

    resources: list[ReleaseResource] = []
    cursor = HEADER_SIZE
    for index, expected_kind in enumerate(ResourceKind):
        try:
            (kind_raw, flags, target_id, load_address, content_offset,
             content_length, auth_version, content_hash) = ENTRY.unpack_from(
                raw, HEADER.size + index * ENTRY.size
            )
        except struct.error as exc:
            raise ValueError("resource table is truncated") from exc
        if kind_raw != expected_kind or flags != 0 or content_offset != cursor:
            raise ValueError("resource table is not canonical")
        if content_length == 0 or content_offset > signature_offset:
            raise ValueError("resource bounds are invalid")
        end = content_offset + content_length
        if end > MAX_U32 or content_length > signature_offset - content_offset:
            raise ValueError("resource bounds overflow package")
        content = raw[content_offset:end]
        if hashlib.sha256(content).digest() != content_hash:
            raise ValueError("resource hash verification failed")
        aligned_end = _align4(end)
        if aligned_end > signature_offset or raw[end:aligned_end] != b"\x00" * (aligned_end - end):
            raise ValueError("resource padding is not canonical")
        resources.append(ReleaseResource(expected_kind, target_id, load_address, auth_version, content))
        cursor = aligned_end
    if cursor != signature_offset:
        raise ValueError("package contains a noncanonical gap")
    typed_resources = tuple(resources)
    _validate_resource_roles(typed_resources, config)  # type: ignore[arg-type]
    return VerifiedReleasePackage(
        source.resolve(), hashlib.sha256(raw).hexdigest(), selected_project,
        project_code, config_version, config_digest, build_id, build_commit,
        build_timestamp, key_id, typed_resources,  # type: ignore[arg-type]
    )


def write_release_package(
    path: Path,
    payload: bytes,
    selected_project: ProjectCode,
    public_keys: Mapping[int, bytes | Ed25519PublicKey],
) -> VerifiedReleasePackage:
    """回读验证临时文件后在同卷原子替换目标。"""
    if type(payload) is not bytes:
        raise TypeError("payload must be bytes")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        loaded = load_verified_release_package(temporary, selected_project, public_keys)
        os.replace(temporary, target)
        temporary = None
        return VerifiedReleasePackage(
            target.resolve(), loaded.release_set_id, loaded.project,
            loaded.project_code, loaded.config_version, loaded.config_digest,
            loaded.build_id, loaded.build_commit, loaded.build_timestamp,
            loaded.key_id, loaded.resources,
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
