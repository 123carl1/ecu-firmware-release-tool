"""经过认证门禁的 Boot、AppValid 与 Signed App 完整镜像合并。"""

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping

from .as5pr_signer import As5prSignPolicy, SignedArtifact, verify_as5pr
from .image_parser import parse_image
from .manifest import VerifiedReleaseManifest, resolve_bundle_resource
from .models import Segment


class ComposePolicy(Enum):
    VALID_APP = "valid-app"
    ERASED_APP_VALID = "erased-app-valid"


@dataclass(frozen=True)
class ComposeResult:
    hex_path: Path
    s19_path: Path
    output_sha256: Mapping[str, str]
    app_valid_state: str


def _u32(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must be a u32")
    return value


def _app_valid_page(manifest: VerifiedReleaseManifest,
                    compose_policy: ComposePolicy) -> tuple[int, bytes, str]:
    config = manifest.memory.get("appValid")
    if not isinstance(config, Mapping):
        raise ValueError("verified manifest lacks memory.appValid contract")
    required = {"start", "size", "fieldOffset", "validValue", "byteOrder",
                "reservedFill", "erasedFill", "allowOfflinePreset"}
    if set(config) != required:
        raise ValueError("memory.appValid contract fields are incomplete or unknown")
    start = _u32(config["start"], "appValid.start")
    size = _u32(config["size"], "appValid.size")
    offset = _u32(config["fieldOffset"], "appValid.fieldOffset")
    reserved = _u32(config["reservedFill"], "appValid.reservedFill")
    erased = _u32(config["erasedFill"], "appValid.erasedFill")
    if size == 0 or offset + 4 > size or reserved > 0xFF or erased > 0xFF:
        raise ValueError("invalid AppValid page geometry or fill")
    if compose_policy is ComposePolicy.ERASED_APP_VALID:
        return start, bytes([erased]) * size, "erased"
    if config["allowOfflinePreset"] is not True:
        raise ValueError("offline AppValid preset is forbidden by verified manifest")
    byte_order = config["byteOrder"]
    if byte_order not in ("little", "big"):
        raise ValueError("AppValid byteOrder must be little or big")
    page = bytearray([reserved]) * size
    page[offset:offset + 4] = _u32(config["validValue"], "appValid.validValue").to_bytes(
        4, byte_order)
    return start, bytes(page), "offline-prevalidated-image"


def _check_segments(segments: tuple[Segment, ...]) -> None:
    ordered = sorted(segments, key=lambda item: item.address)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.address + len(previous.data) > current.address:
            raise ValueError("Boot, AppValid, and Signed App regions overlap")


def _hex_record(kind: int, address: int, data: bytes) -> str:
    raw = bytes((len(data),)) + address.to_bytes(2, "big") + bytes((kind,)) + data
    return ":" + (raw + bytes((-sum(raw) & 0xFF,))).hex().upper()


def _encode_hex(segments: tuple[Segment, ...]) -> bytes:
    lines: list[str] = []
    upper: int | None = None
    for segment in segments:
        for offset in range(0, len(segment.data), 16):
            address = segment.address + offset
            next_upper = address >> 16
            if next_upper != upper:
                lines.append(_hex_record(4, 0, next_upper.to_bytes(2, "big")))
                upper = next_upper
            lines.append(_hex_record(0, address & 0xFFFF, segment.data[offset:offset + 16]))
    lines.append(_hex_record(1, 0, b""))
    return ("\n".join(lines) + "\n").encode("ascii")


def _srec_record(kind: str, address: int, address_size: int, data: bytes) -> str:
    body = address.to_bytes(address_size, "big") + data
    count = len(body) + 1
    checksum = ~(count + sum(body)) & 0xFF
    return "S" + kind + (bytes((count,)) + body + bytes((checksum,))).hex().upper()


def _encode_s19(segments: tuple[Segment, ...]) -> bytes:
    lines = [_srec_record("3", segment.address + offset, 4,
                          segment.data[offset:offset + 16])
             for segment in segments for offset in range(0, len(segment.data), 16)]
    lines.append(_srec_record("7", 0, 4, b""))
    return ("\n".join(lines) + "\n").encode("ascii")


def _atomic_verified_write(target: Path, payload: bytes,
                           expected: tuple[Segment, ...]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=target.suffix,
                                            dir=target.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if parse_image(temporary) != expected:
            raise ValueError(f"{target.suffix} reread differs from composed bytes")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def compose_full_image(output_dir: Path, signed: SignedArtifact,
                       manifest: VerifiedReleaseManifest, sign_policy: As5prSignPolicy,
                       verification_key: bytes, compose_policy: ComposePolicy) -> ComposeResult:
    if not isinstance(manifest, VerifiedReleaseManifest):
        raise TypeError("composer requires a verified release manifest")
    if not isinstance(compose_policy, ComposePolicy):
        raise ValueError("unknown compose policy")
    if signed.artifact.identity.bundle_id != manifest.bundle_id:
        raise ValueError("SignedArtifact and manifest bundle differ")
    boot_start = _u32(manifest.memory.get("bootStart"), "memory.bootStart")
    app_start = _u32(manifest.memory.get("appStart"), "memory.appStart")
    app_end = _u32(manifest.memory.get("appEnd"), "memory.appEnd")
    if signed.artifact.identity.normalization_start != app_start:
        raise ValueError("SignedArtifact normalization start differs from manifest App start")
    try:
        verify_as5pr(signed, sign_policy, verification_key)
    except ValueError:
        if compose_policy is ComposePolicy.VALID_APP:
            raise
    boot = resolve_bundle_resource(manifest, "boot").read_bytes()
    if app_start + len(signed.signed_bytes) > app_end:
        raise ValueError("Signed App exceeds manifest App region")
    valid_start, valid_page, valid_state = _app_valid_page(manifest, compose_policy)
    segments = tuple(sorted((Segment(boot_start, boot), Segment(valid_start, valid_page),
                             Segment(app_start, signed.signed_bytes)), key=lambda item: item.address))
    _check_segments(segments)
    directory = Path(output_dir)
    hex_path = directory / "full_image.hex"
    s19_path = directory / "full_image.s19"
    _atomic_verified_write(hex_path, _encode_hex(segments), segments)
    try:
        _atomic_verified_write(s19_path, _encode_s19(segments), segments)
    except Exception:
        hex_path.unlink(missing_ok=True)
        raise
    hashes = MappingProxyType({hex_path.suffix: hashlib.sha256(hex_path.read_bytes()).hexdigest(),
                               s19_path.suffix: hashlib.sha256(s19_path.read_bytes()).hexdigest()})
    return ComposeResult(hex_path, s19_path, hashes, valid_state)
