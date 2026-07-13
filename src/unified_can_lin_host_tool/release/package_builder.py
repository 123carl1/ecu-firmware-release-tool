"""从固定三工程输出生成同构建、已认证的 AS5PR 单文件发布资源集合。"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import struct
import time

from .build_identity import read_build_identity, validate_release_build
from .development_keys import (
    DEVELOPMENT_BOOT_HMAC_KEY,
    DEVELOPMENT_KEY_ID,
    DEVELOPMENT_PACKAGE_PUBLIC_KEY,
    development_package_private_key,
)
from .package import ReleaseResource, ResourceKind, encode_release_package, write_release_package
from .project_config import ProjectCode, compute_config_digest, get_project_config


AUTH_BLOCK_SIZE = 48


def authenticate_image(payload: bytes, target_id: int, version: int, key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("HMAC key must be exactly 32 bytes")
    header = struct.pack("<IIII", 0xA5A5A5A5, len(payload), target_id, version)
    return payload + header + hmac.new(key, payload + header, hashlib.sha256).digest()


def build_as5pr_release_package(firmware_root: Path, output: Path):
    root = Path(firmware_root).resolve()
    cfg = get_project_config(ProjectCode.AS5PR)
    paths = (
        (root / "Bootloader/build" / cfg.resource_files.boot_elf,
         root / "Bootloader/build" / cfg.resource_files.boot_bin),
        (root / "build/AS5PR" / cfg.resource_files.app_elf,
         root / "build/AS5PR" / cfg.resource_files.app_bin),
        (root / "FlashDriver/build" / cfg.resource_files.flash_driver_elf,
         root / "FlashDriver/build" / cfg.resource_files.flash_driver_bin),
    )
    for elf_path, bin_path in paths:
        if not elf_path.is_file() or not bin_path.is_file():
            raise FileNotFoundError(f"missing controlled build output: {elf_path} / {bin_path}")
    identities = tuple(read_build_identity(elf_path, bin_path) for elf_path, bin_path in paths)
    validate_release_build(identities)
    for identity in identities:
        if (identity.project_code != cfg.project_code
                or identity.config_version != cfg.config_version
                or identity.config_digest != compute_config_digest(cfg)):
            raise ValueError("build identity differs from internal AS5PR configuration")

    boot = paths[0][1].read_bytes()
    app = authenticate_image(paths[1][1].read_bytes(), cfg.authentication.app_target_id,
                             cfg.authentication.app_version, DEVELOPMENT_BOOT_HMAC_KEY)
    driver = authenticate_image(paths[2][1].read_bytes(), cfg.authentication.flash_driver_target_id,
                                cfg.authentication.flash_driver_version, DEVELOPMENT_BOOT_HMAC_KEY)
    if len(app) > cfg.memory.app_end - cfg.memory.app_start:
        raise ValueError("authenticated App exceeds configured range")
    if len(driver) > cfg.memory.flash_driver_max_size:
        raise ValueError("authenticated FlashDriver exceeds configured RAM range")
    resources = (
        ReleaseResource(ResourceKind.BOOT, cfg.project_code, cfg.memory.boot_start, 0, boot),
        ReleaseResource(ResourceKind.APP, cfg.authentication.app_target_id, cfg.memory.app_start,
                        cfg.authentication.app_version, app),
        ReleaseResource(ResourceKind.FLASH_DRIVER, cfg.authentication.flash_driver_target_id,
                        cfg.memory.flash_driver_ram, cfg.authentication.flash_driver_version, driver),
    )
    payload = encode_release_package(
        resources, cfg, build_id=identities[0].build_id,
        build_commit=identities[0].build_commit.hex(), build_timestamp=int(time.time()),
        key_id=DEVELOPMENT_KEY_ID, private_key=development_package_private_key(),
    )
    return write_release_package(
        Path(output), payload, ProjectCode.AS5PR,
        {DEVELOPMENT_KEY_ID: DEVELOPMENT_PACKAGE_PUBLIC_KEY},
    )
