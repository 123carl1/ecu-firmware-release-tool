"""把用户选择的原生 App 镜像转换为只存在于内存中的 AS5PR OTA 输入。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import struct

from .development_keys import DEVELOPMENT_BOOT_HMAC_KEY, DEVELOPMENT_KEY_ID
from .image_parser import _parse_image_bytes, normalize_segments
from .internal_resources import load_as5pr_flash_driver
from .package import ReleaseResource, ResourceKind
from .package_builder import authenticate_image
from .project_config import ProjectCode, compute_config_digest, get_project_config


AUTH_BLOCK_SIZE = 48
_AUTH_HEADER = struct.Struct("<IIII")
_RELEASE_ID_DOMAIN = b"AS5PR_RUNTIME_OTA_V1\0"
_APP_STACK_TOP = 0x20007FC0


@dataclass(frozen=True)
class RuntimeOtaPackage:
    source_path: Path
    release_set_id: str
    project: ProjectCode
    project_code: int
    config_version: int
    config_digest: bytes
    key_id: int
    source_file_sha256: bytes
    resources: tuple[ReleaseResource, ReleaseResource]


def verify_authenticated_image(content: bytes, *, target_id: int, version: int) -> bytes:
    if len(content) <= AUTH_BLOCK_SIZE:
        raise ValueError("authenticated image is too short")
    payload = content[:-AUTH_BLOCK_SIZE]
    header = content[-AUTH_BLOCK_SIZE:-32]
    magic, payload_size, actual_target, actual_version = _AUTH_HEADER.unpack(header)
    if (magic, payload_size, actual_target, actual_version) != (
        0xA5A5A5A5, len(payload), target_id, version,
    ):
        raise ValueError("authenticated image header mismatch")
    expected = hmac.new(DEVELOPMENT_BOOT_HMAC_KEY, payload + header, hashlib.sha256).digest()
    if not hmac.compare_digest(content[-32:], expected):
        raise ValueError("authenticated image HMAC mismatch")
    return payload


def _validate_vectors(payload: bytes, segments) -> None:
    if len(payload) < 8:
        raise ValueError("App vector table is truncated")
    stack_pointer, reset_vector = struct.unpack_from("<II", payload)
    reset_address = reset_vector & ~1
    reset_is_loaded = any(
        segment.address <= reset_address < segment.address + len(segment.data)
        for segment in segments
    )
    if stack_pointer != _APP_STACK_TOP:
        raise ValueError("App vector stack pointer differs from the AS5PR stack contract")
    if (reset_vector & 1) == 0 or not reset_is_loaded:
        raise ValueError("App reset vector is not a loaded Thumb address")


def prepare_as5pr_app(path: Path) -> RuntimeOtaPackage:
    source = Path(path).resolve()
    if source.suffix.lower() not in (".hex", ".ihex", ".s19", ".srec", ".s28", ".s37"):
        raise ValueError("App input must be Intel HEX or S-record")
    config = get_project_config(ProjectCode.AS5PR)
    try:
        source_bytes = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read App image: {source}") from exc
    segments = _parse_image_bytes(source, source_bytes)
    first = segments[0].address
    end = segments[-1].address + len(segments[-1].data)
    if first != config.memory.app_start or end > config.memory.app_end - AUTH_BLOCK_SIZE:
        raise ValueError("App image segments are outside the configured App range")
    payload = normalize_segments(
        segments,
        start=config.memory.app_start,
        end=end,
        gap_fill=0xFF,
    )
    _validate_vectors(payload, segments)

    if len(payload) >= AUTH_BLOCK_SIZE:
        possible_header = payload[-AUTH_BLOCK_SIZE:-32]
        if len(possible_header) == _AUTH_HEADER.size:
            magic, payload_size, _, _ = _AUTH_HEADER.unpack(possible_header)
            if magic == 0xA5A5A5A5 and payload_size == len(payload) - AUTH_BLOCK_SIZE:
                raise ValueError("App input is already authenticated; select the native unsigned image")

    signed_app = authenticate_image(
        payload,
        config.authentication.app_target_id,
        config.authentication.app_version,
        DEVELOPMENT_BOOT_HMAC_KEY,
    )
    verify_authenticated_image(
        signed_app,
        target_id=config.authentication.app_target_id,
        version=config.authentication.app_version,
    )

    driver = load_as5pr_flash_driver()
    verify_authenticated_image(
        driver,
        target_id=config.authentication.flash_driver_target_id,
        version=config.authentication.flash_driver_version,
    )
    if len(driver) > config.memory.flash_driver_max_size:
        raise ValueError("internal FlashDriver exceeds configured RAM range")

    resources = (
        ReleaseResource(
            ResourceKind.APP,
            config.authentication.app_target_id,
            config.memory.app_start,
            config.authentication.app_version,
            signed_app,
        ),
        ReleaseResource(
            ResourceKind.FLASH_DRIVER,
            config.authentication.flash_driver_target_id,
            config.memory.flash_driver_ram,
            config.authentication.flash_driver_version,
            driver,
        ),
    )
    source_hash = hashlib.sha256(source_bytes).digest()
    release_id = _compute_release_id(
        source_hash,
        signed_app,
        driver,
        compute_config_digest(config),
    )
    return RuntimeOtaPackage(
        source,
        release_id,
        ProjectCode.AS5PR,
        config.project_code,
        config.config_version,
        compute_config_digest(config),
        DEVELOPMENT_KEY_ID,
        source_hash,
        resources,
    )


def _compute_release_id(source_hash: bytes, signed_app: bytes, driver: bytes,
                        config_digest: bytes) -> str:
    return hashlib.sha256(
        _RELEASE_ID_DOMAIN
        + source_hash
        + hashlib.sha256(signed_app).digest()
        + hashlib.sha256(driver).digest()
        + config_digest
    ).hexdigest()


def validate_runtime_ota_package(package: RuntimeOtaPackage) -> None:
    if not isinstance(package, RuntimeOtaPackage):
        raise TypeError("package must be RuntimeOtaPackage")
    config = get_project_config(ProjectCode.AS5PR)
    if (package.project is not ProjectCode.AS5PR
            or package.project_code != config.project_code
            or package.config_version != config.config_version
            or package.config_digest != compute_config_digest(config)):
        raise ValueError("runtime OTA project configuration mismatch")
    if len(package.source_file_sha256) != 32:
        raise ValueError("runtime OTA source hash length mismatch")
    if tuple(item.kind for item in package.resources) != (
        ResourceKind.APP, ResourceKind.FLASH_DRIVER,
    ):
        raise ValueError("runtime OTA resources must be App and FlashDriver")
    app, driver = package.resources
    if (app.target_id, app.auth_version, app.load_address) != (
        config.authentication.app_target_id,
        config.authentication.app_version,
        config.memory.app_start,
    ) or len(app.content) > config.memory.app_end - config.memory.app_start:
        raise ValueError("runtime OTA App metadata mismatch")
    if (driver.target_id, driver.auth_version, driver.load_address) != (
        config.authentication.flash_driver_target_id,
        config.authentication.flash_driver_version,
        config.memory.flash_driver_ram,
    ) or len(driver.content) > config.memory.flash_driver_max_size:
        raise ValueError("runtime OTA FlashDriver metadata mismatch")
    verify_authenticated_image(
        app.content,
        target_id=config.authentication.app_target_id,
        version=config.authentication.app_version,
    )
    verify_authenticated_image(
        driver.content,
        target_id=config.authentication.flash_driver_target_id,
        version=config.authentication.flash_driver_version,
    )
    expected_id = _compute_release_id(
        package.source_file_sha256,
        app.content,
        driver.content,
        package.config_digest,
    )
    if not hmac.compare_digest(package.release_set_id, expected_id):
        raise ValueError("runtime OTA release identity mismatch")
