"""程序内只读项目发布配置及其规范摘要。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata


PROJECT_RELEASE_CONFIG_DOMAIN = b"PROJECT_RELEASE_CONFIG_V1\0"


class ProjectCode(str, Enum):
    E68 = "E68"
    AS5PR = "AS5PR"


def _u32(value: object, field: str) -> None:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{field} must be a u32")


def _u16(value: object, field: str) -> None:
    if type(value) is not int or not 0 <= value <= 0xFFFF:
        raise ValueError(f"{field} must be a u16")


def _byte(value: object, field: str) -> None:
    if type(value) is not int or not 0 <= value <= 0xFF:
        raise ValueError(f"{field} must be a byte")


@dataclass(frozen=True)
class BusReleaseConfig:
    kind: str
    baudrate: int
    request_id: int
    response_id: int
    functional_request_id: int
    padding: int
    nad: int

    def __post_init__(self) -> None:
        if self.kind not in ("CAN", "LIN"):
            raise ValueError("bus.kind must be CAN or LIN")
        for field in ("baudrate", "request_id", "response_id", "functional_request_id"):
            _u32(getattr(self, field), f"bus.{field}")
        _byte(self.padding, "bus.padding")
        _byte(self.nad, "bus.nad")


@dataclass(frozen=True)
class MemoryReleaseConfig:
    boot_start: int
    boot_end: int
    app_valid_start: int
    app_valid_size: int
    app_start: int
    app_end: int
    flash_driver_ram: int
    flash_driver_max_size: int
    page_size: int

    def __post_init__(self) -> None:
        for field, value in asdict(self).items():
            _u32(value, f"memory.{field}")
        if not self.boot_start < self.boot_end <= self.app_valid_start < self.app_start < self.app_end:
            raise ValueError("memory ranges are inconsistent")
        if self.app_valid_size == 0 or self.page_size == 0:
            raise ValueError("memory page sizes must be positive")


@dataclass(frozen=True)
class AuthenticationReleaseConfig:
    magic: int
    app_target_id: int
    app_version: int
    flash_driver_target_id: int
    flash_driver_version: int
    algorithm_id: int

    def __post_init__(self) -> None:
        for field, value in asdict(self).items():
            _u32(value, f"authentication.{field}")


@dataclass(frozen=True)
class IdentityDidReleaseConfig:
    did: int
    response_length: int
    protocol: int

    def __post_init__(self) -> None:
        _u16(self.did, "identity_did.did")
        _u16(self.response_length, "identity_did.response_length")
        _byte(self.protocol, "identity_did.protocol")


@dataclass(frozen=True)
class TimingReleaseConfig:
    p2_ms: int
    p2_star_ms: int
    max_transfer_payload: int
    request_download_format: int
    frame_gap_ms: int
    poll_timeout_ms: int
    poll_gap_ms: int

    def __post_init__(self) -> None:
        for field, value in asdict(self).items():
            _u32(value, f"timing.{field}")


@dataclass(frozen=True)
class WorkflowReleaseConfig:
    workflow_id: str
    app_seed_key_id: str
    boot_seed_key_id: str


@dataclass(frozen=True)
class CommunicationCheckConfig:
    version_did: int
    freshness_window_ms: int

    def __post_init__(self) -> None:
        _u16(self.version_did, "communication_check.version_did")
        _u32(self.freshness_window_ms, "communication_check.freshness_window_ms")


@dataclass(frozen=True)
class ResourceFileConfig:
    boot_elf: str
    boot_bin: str
    app_elf: str
    app_bin: str
    flash_driver_elf: str
    flash_driver_bin: str


@dataclass(frozen=True)
class ProjectReleaseConfig:
    selection: ProjectCode
    name: str
    project_code: int
    config_version: int
    ecu_target_id: int
    bus: BusReleaseConfig
    memory: MemoryReleaseConfig
    authentication: AuthenticationReleaseConfig
    identity_did: IdentityDidReleaseConfig
    timing: TimingReleaseConfig
    workflow: WorkflowReleaseConfig
    communication_check: CommunicationCheckConfig
    resource_files: ResourceFileConfig
    real_flash_enabled: bool

    def __post_init__(self) -> None:
        if not isinstance(self.selection, ProjectCode):
            raise ValueError("selection must be a ProjectCode")
        _u32(self.project_code, "project_code")
        _u16(self.config_version, "config_version")
        _u32(self.ecu_target_id, "ecu_target_id")
        if type(self.real_flash_enabled) is not bool:
            raise ValueError("real_flash_enabled must be bool")


_COMMON_MEMORY = MemoryReleaseConfig(
    boot_start=0x00000000,
    boot_end=0x00006800,
    app_valid_start=0x00006A00,
    app_valid_size=0x200,
    app_start=0x00007000,
    app_end=0x00020000,
    flash_driver_ram=0x20001000,
    flash_driver_max_size=0x2000,
    page_size=512,
)

_CONFIGS: Mapping[ProjectCode, ProjectReleaseConfig] = MappingProxyType({
    ProjectCode.AS5PR: ProjectReleaseConfig(
        selection=ProjectCode.AS5PR,
        name="AS5PR CAN Bootloader",
        project_code=0x41503541,
        config_version=1,
        ecu_target_id=0x41503541,
        bus=BusReleaseConfig("CAN", 500000, 0x701, 0x709, 0x7DF, 0xAA, 0),
        memory=_COMMON_MEMORY,
        authentication=AuthenticationReleaseConfig(
            0xA5A5A5A5, 0x41503541, 1, 0x46503541, 1, 1
        ),
        identity_did=IdentityDidReleaseConfig(0xF1A0, 8, 1),
        timing=TimingReleaseConfig(50, 5000, 62, 0x44, 0, 300, 5),
        workflow=WorkflowReleaseConfig("as5pr_can_bootloader_v1", "as5pr_level1", "as5pr_fbl"),
        communication_check=CommunicationCheckConfig(0x3000, 5000),
        resource_files=ResourceFileConfig(
            "as5pr_can_boot.elf", "as5pr_can_boot.bin",
            "dau_fm33_ht_as5pr.elf", "dau_fm33_ht_as5pr.bin",
            "as5pr_flash_driver.elf", "as5pr_flash_driver.bin",
        ),
        real_flash_enabled=True,
    ),
    ProjectCode.E68: ProjectReleaseConfig(
        selection=ProjectCode.E68,
        name="E68 LIN Bootloader",
        project_code=0,
        config_version=0,
        ecu_target_id=0,
        bus=BusReleaseConfig("LIN", 19200, 0x3C, 0x3D, 0, 0xFF, 0x11),
        memory=_COMMON_MEMORY,
        authentication=AuthenticationReleaseConfig(0xA5A5A5A5, 0, 0, 0, 0, 0),
        identity_did=IdentityDidReleaseConfig(0xF1A0, 8, 0),
        timing=TimingReleaseConfig(50, 5000, 1024, 0x44, 12, 300, 20),
        workflow=WorkflowReleaseConfig("e68_lin_bootloader_v1", "e68_level1", "e68_fbl"),
        communication_check=CommunicationCheckConfig(0x3000, 5000),
        resource_files=ResourceFileConfig("", "", "", "", "", ""),
        real_flash_enabled=False,
    ),
})


def get_project_config(project: ProjectCode) -> ProjectReleaseConfig:
    try:
        return _CONFIGS[project]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported project: {project!r}") from exc


def _normalize(value: Any) -> Any:
    if isinstance(value, ProjectCode):
        return value.value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or not key or not key.isascii():
                raise ValueError("configuration keys must be non-empty ASCII strings")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, tuple):
        return tuple(_normalize(item) for item in value)
    if type(value) is str:
        text = unicodedata.normalize("NFC", value)
        if not text.isascii():
            raise ValueError("configuration strings must be ASCII")
        return text
    if type(value) is bool:
        return value
    if type(value) is int:
        _u32(value, "configuration integer")
        return value
    raise TypeError(f"unsupported configuration value: {type(value).__name__}")


def canonical_config_bytes(config: ProjectReleaseConfig) -> bytes:
    if not isinstance(config, ProjectReleaseConfig):
        raise TypeError("config must be ProjectReleaseConfig")
    normalized = _normalize(asdict(config))
    payload = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return PROJECT_RELEASE_CONFIG_DOMAIN + payload


def compute_config_digest(config: ProjectReleaseConfig) -> bytes:
    return hashlib.sha256(canonical_config_bytes(config)).digest()
