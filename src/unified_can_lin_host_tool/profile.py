from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError


@dataclass(frozen=True)
class BusProfile:
    type: str
    baudrate: int
    request_id: int
    response_id: int
    nad: int | None
    functional_request_id: int | None = None
    padding: int = 0xFF


@dataclass(frozen=True)
class MemoryProfile:
    app_start: int
    app_size: int
    app_end: int
    flash_driver_ram: int
    flash_driver_max_size: int
    page_size: int


@dataclass(frozen=True)
class UdsProfile:
    p2_ms: int
    p2_star_ms: int
    max_transfer_payload: int
    request_download_format: int
    frame_gap_ms: int
    poll_timeout_ms: int
    poll_gap_ms: int


@dataclass(frozen=True)
class SeedKeyProfile:
    app_level1: str
    boot_fbl: str


@dataclass(frozen=True)
class WorkflowProfile:
    name: str


@dataclass(frozen=True)
class ToolProfile:
    name: str
    bus: BusProfile
    memory: MemoryProfile
    uds: UdsProfile
    seedkey: SeedKeyProfile
    workflow: WorkflowProfile


def load_profile(source: str | Path | Mapping[str, Any]) -> ToolProfile:
    raw = _load_raw_profile(source)
    bus_section = _required_section(raw, "bus")
    profile = ToolProfile(
        name=str(_required(raw, "name")),
        bus=BusProfile(
            type=str(_required(bus_section, "type")),
            baudrate=_required_int(raw, "bus", "baudrate"),
            request_id=_required_int(raw, "bus", "request_id"),
            response_id=_required_int(raw, "bus", "response_id"),
            nad=_optional_int(raw, "bus", "nad"),
            functional_request_id=_optional_int(raw, "bus", "functional_request_id"),
            padding=_optional_int(raw, "bus", "padding", default=0xFF) or 0xFF,
        ),
        memory=MemoryProfile(
            app_start=_required_int(raw, "memory", "app_start"),
            app_size=_required_int(raw, "memory", "app_size"),
            app_end=_required_int(raw, "memory", "app_end"),
            flash_driver_ram=_required_int(raw, "memory", "flash_driver_ram"),
            flash_driver_max_size=_required_int(raw, "memory", "flash_driver_max_size"),
            page_size=_required_int(raw, "memory", "page_size"),
        ),
        uds=UdsProfile(
            p2_ms=_required_int(raw, "uds", "p2_ms"),
            p2_star_ms=_required_int(raw, "uds", "p2_star_ms"),
            max_transfer_payload=_required_int(raw, "uds", "max_transfer_payload"),
            request_download_format=_required_int(raw, "uds", "request_download_format"),
            frame_gap_ms=_required_int(raw, "uds", "frame_gap_ms"),
            poll_timeout_ms=_required_int(raw, "uds", "poll_timeout_ms"),
            poll_gap_ms=_required_int(raw, "uds", "poll_gap_ms"),
        ),
        seedkey=SeedKeyProfile(
            app_level1=str(_required_section(raw, "seedkey")["app_level1"]),
            boot_fbl=str(_required_section(raw, "seedkey")["boot_fbl"]),
        ),
        workflow=WorkflowProfile(name=str(_required_section(raw, "workflow")["name"])),
    )
    _validate_profile(profile)
    return profile


def _load_raw_profile(source: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source

    path = Path(source)
    if not path.exists():
        raise HostToolError(ErrorCategory.PROFILE, f"profile file not found: {path}")

    with path.open("r", encoding="utf-8") as profile_file:
        loaded = yaml.safe_load(profile_file)

    if not isinstance(loaded, Mapping):
        raise HostToolError(ErrorCategory.PROFILE, "profile root must be a mapping")
    return loaded


def _required(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw:
        raise HostToolError(ErrorCategory.PROFILE, f"missing profile field: {key}")
    return raw[key]


def _required_section(raw: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    value = _required(raw, section)
    if not isinstance(value, Mapping):
        raise HostToolError(ErrorCategory.PROFILE, f"profile section must be a mapping: {section}")
    return value


def _required_int(raw: Mapping[str, Any], section: str, key: str) -> int:
    section_value = _required_section(raw, section)
    if key not in section_value:
        raise HostToolError(ErrorCategory.PROFILE, f"missing profile field: {section}.{key}")
    value = section_value[key]
    if not isinstance(value, int):
        raise HostToolError(ErrorCategory.PROFILE, f"profile field must be int: {section}.{key}")
    return value


def _optional_int(raw: Mapping[str, Any], section: str, key: str, *, default: int | None = None) -> int | None:
    section_value = _required_section(raw, section)
    if key not in section_value:
        return default
    value = section_value[key]
    if not isinstance(value, int):
        raise HostToolError(ErrorCategory.PROFILE, f"profile field must be int: {section}.{key}")
    return value


def _validate_profile(profile: ToolProfile) -> None:
    if profile.bus.type == "LIN":
        _validate_byte("bus.request_id", profile.bus.request_id)
        _validate_byte("bus.response_id", profile.bus.response_id)
        if profile.bus.nad is None:
            raise HostToolError(ErrorCategory.PROFILE, "missing profile field: bus.nad")
        _validate_byte("bus.nad", profile.bus.nad)
    elif profile.bus.type == "CAN":
        _validate_can_id("bus.request_id", profile.bus.request_id)
        _validate_can_id("bus.response_id", profile.bus.response_id)
        if profile.bus.functional_request_id is not None:
            _validate_can_id("bus.functional_request_id", profile.bus.functional_request_id)
        _validate_byte("bus.padding", profile.bus.padding)
    else:
        raise HostToolError(ErrorCategory.PROFILE, "bus.type must be LIN or CAN")

    if profile.memory.app_start + profile.memory.app_size != profile.memory.app_end:
        raise HostToolError(ErrorCategory.PROFILE, "memory app range is inconsistent")
    if profile.memory.page_size <= 0:
        raise HostToolError(ErrorCategory.PROFILE, "memory.page_size must be positive")
    if not 1 <= profile.uds.max_transfer_payload <= (0xFFF - 2):
        raise HostToolError(
            ErrorCategory.PROFILE,
            "uds.max_transfer_payload must fit ISO-TP 12-bit length after SID and block sequence",
        )
    if profile.bus.type == "LIN" and profile.workflow.name != "e68_lin_bootloader_v1":
        raise HostToolError(ErrorCategory.PROFILE, "workflow.name must be e68_lin_bootloader_v1")
    if profile.bus.type == "CAN" and profile.workflow.name != "as5pr_can_bootloader_v1":
        raise HostToolError(ErrorCategory.PROFILE, "workflow.name must be as5pr_can_bootloader_v1")
    if profile.workflow.name == "e68_lin_bootloader_v1":
        if profile.seedkey.app_level1 != "e68_level1":
            raise HostToolError(ErrorCategory.PROFILE, "seedkey.app_level1 must be e68_level1")
        if profile.seedkey.boot_fbl != "e68_fbl":
            raise HostToolError(ErrorCategory.PROFILE, "seedkey.boot_fbl must be e68_fbl")
    if profile.workflow.name == "as5pr_can_bootloader_v1":
        if profile.seedkey.app_level1 != "as5pr_level1":
            raise HostToolError(ErrorCategory.PROFILE, "seedkey.app_level1 must be as5pr_level1")
        if profile.seedkey.boot_fbl != "as5pr_fbl":
            raise HostToolError(ErrorCategory.PROFILE, "seedkey.boot_fbl must be as5pr_fbl")


def _validate_byte(name: str, value: int) -> None:
    if not 0 <= value <= 0xFF:
        raise HostToolError(ErrorCategory.PROFILE, f"{name} must be in 0x00..0xFF")


def _validate_can_id(name: str, value: int) -> None:
    if not 0 <= value <= 0x7FF:
        raise HostToolError(ErrorCategory.PROFILE, f"{name} must be an 11-bit CAN ID")
