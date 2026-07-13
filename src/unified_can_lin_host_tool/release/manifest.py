"""Signed release bundle manifest loading and resource verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError


def _frozen(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_frozen(item) for item in value)
    return value


@dataclass(frozen=True)
class ResourceDescriptor:
    resource_id: str
    path: str
    size: int
    sha256: str
    kind: str
    bundle_id: str
    target_id: str


@dataclass(frozen=True)
class ReleaseManifest:
    bundle_root: Path
    manifest_bytes: bytes
    manifest_sha256: str
    schema_version: int
    bundle_id: str
    target_id: str
    project_id: str
    version: str
    source: Mapping[str, Any]
    memory: Mapping[str, Any]
    normalization: Mapping[str, Any]
    authentication: Mapping[str, Any]
    workflow: Mapping[str, Any]
    resources: Mapping[str, ResourceDescriptor]

    @property
    def profile(self) -> ResourceDescriptor:
        return self.resources["profile"]

    @property
    def abi(self) -> Mapping[str, Any]:
        return self.memory


_VERIFIED_MANIFEST_TOKEN = object()


@dataclass(frozen=True, init=False)
class VerifiedReleaseManifest:
    """只能由完成签名、schema 和资源校验的加载器创建。"""

    _manifest: ReleaseManifest

    def __init__(self, manifest: ReleaseManifest, *, _token: object) -> None:
        if _token is not _VERIFIED_MANIFEST_TOKEN:
            raise TypeError("VerifiedReleaseManifest can only be created by the verified loader")
        object.__setattr__(self, "_manifest", manifest)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manifest, name)


_TOP_FIELDS = {
    "schemaVersion": int, "bundleId": str, "targetId": str, "projectId": str,
    "version": str, "source": dict, "memory": dict, "normalization": dict,
    "authentication": dict, "workflow": dict, "resources": dict,
}
_NESTED_FIELDS = {
    "source": {"commit": str, "dirty": bool, "toolchain": str, "configHash": str, "builtAt": str},
    "memory": {"appStart": int, "appEnd": int, "pageSize": int, "flashDriverRam": int, "flashDriverMaxSize": int},
    "normalization": {"start": int, "end": int, "gapFill": int},
    "authentication": {"formatVersion": int, "signPolicyId": str, "keyId": str},
    "workflow": {"name": str, "version": int},
}
_REQUIRED_RESOURCES = {"profile", "boot", "flash_driver"}


def _require_fields(container: dict[str, Any], fields: Mapping[str, type], context: str) -> None:
    for name, expected in fields.items():
        if name not in container or type(container[name]) is not expected:
            raise ValueError(f"manifest {context}.{name} is missing or has wrong type")
        if expected is str and not container[name]:
            raise ValueError(f"manifest {context}.{name} must not be empty")


def _safe_resource_path(root: Path, relative: str) -> Path:
    windows = PureWindowsPath(relative)
    posix = PurePosixPath(relative)
    if (not relative or windows.is_absolute() or windows.drive or posix.is_absolute()
            or "\\" in relative or any(part in ("", ".", "..") for part in posix.parts)):
        raise ValueError(f"unsafe resource path: {relative!r}")
    candidate = root.joinpath(*posix.parts)
    current = root
    for part in posix.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"resource path contains symbolic link: {relative!r}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"resource path escapes bundle or does not exist: {relative!r}") from exc
    if not candidate.is_file():
        raise ValueError(f"resource path is not a regular file: {relative!r}")
    return candidate


def _public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError("signature public key must be 32 raw bytes")
    return Ed25519PublicKey.from_public_bytes(value)


def load_verified_manifest(bundle_root: Path, public_key: bytes | Ed25519PublicKey) -> VerifiedReleaseManifest:
    root = Path(bundle_root)
    raw = (root / "manifest.yaml").read_bytes()
    signature = (root / "manifest.sig").read_bytes()
    if len(signature) != 64:
        raise ValueError("signature must be exactly 64 raw bytes")
    verified_key = _public_key(public_key)
    try:
        verified_key.verify(signature, raw)
    except (InvalidSignature, TypeError) as exc:
        raise ValueError("signature verification failed") from exc

    try:
        manifest_text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HostToolError(ErrorCategory.FILE, "manifest must be strict UTF-8") from exc
    try:
        document = yaml.safe_load(manifest_text)
    except yaml.YAMLError as exc:
        raise ValueError("manifest YAML is malformed") from exc
    if not isinstance(document, dict):
        raise ValueError("manifest YAML root must be a mapping")
    _require_fields(document, _TOP_FIELDS, "root")
    for section, fields in _NESTED_FIELDS.items():
        _require_fields(document[section], fields, section)

    resource_data = document["resources"]
    if not _REQUIRED_RESOURCES.issubset(resource_data):
        raise ValueError("manifest lacks required profile, boot, or flash_driver resource")
    descriptors: dict[str, ResourceDescriptor] = {}
    for resource_id, item in resource_data.items():
        if not isinstance(resource_id, str) or not isinstance(item, dict):
            raise ValueError("resource entries must be named mappings")
        _require_fields(item, {"path": str, "size": int, "sha256": str, "kind": str, "bundleId": str, "targetId": str}, f"resources.{resource_id}")
        resource_bundle = item["bundleId"]
        resource_target = item["targetId"]
        if resource_bundle != document["bundleId"] or resource_target != document["targetId"]:
            raise ValueError(f"resource {resource_id} references another bundle or target")
        if resource_id in _REQUIRED_RESOURCES and item["kind"] != resource_id:
            raise ValueError(f"resource {resource_id} has inconsistent kind")
        if item["size"] < 0 or len(item["sha256"]) != 64:
            raise ValueError(f"resource {resource_id} has invalid integrity metadata")
        try:
            int(item["sha256"], 16)
        except ValueError as exc:
            raise ValueError(f"resource {resource_id} has invalid SHA-256") from exc
        path = _safe_resource_path(root, item["path"])
        payload = path.read_bytes()
        if len(payload) != item["size"] or hashlib.sha256(payload).hexdigest() != item["sha256"].lower():
            raise ValueError(f"resource {resource_id} integrity verification failed")
        descriptors[resource_id] = ResourceDescriptor(resource_id, item["path"], item["size"], item["sha256"].lower(), item["kind"], resource_bundle, resource_target)

    manifest = ReleaseManifest(
        root.resolve(), raw, hashlib.sha256(raw).hexdigest(), document["schemaVersion"],
        document["bundleId"], document["targetId"], document["projectId"], document["version"],
        _frozen(document["source"]), _frozen(document["memory"]), _frozen(document["normalization"]),
        _frozen(document["authentication"]), _frozen(document["workflow"]), MappingProxyType(descriptors),
    )
    return VerifiedReleaseManifest(manifest, _token=_VERIFIED_MANIFEST_TOKEN)


def resolve_bundle_resource(manifest: ReleaseManifest | VerifiedReleaseManifest, resource_id: str) -> Path:
    try:
        descriptor = manifest.resources[resource_id]
    except KeyError:
        raise KeyError(f"unknown resource id: {resource_id}") from None
    return _safe_resource_path(manifest.bundle_root, descriptor.path)
