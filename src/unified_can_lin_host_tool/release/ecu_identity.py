"""通过只读 DID 0xF1A0 探测在线 ECU 项目和运行角色。"""

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError

from .project_config import ProjectReleaseConfig


class EcuRole(IntEnum):
    APP = 1
    BOOT = 2


class IdentityProbeStatus(str, Enum):
    APP_CONFIRMED = "APP_CONFIRMED"
    BOOT_CONFIRMED = "BOOT_CONFIRMED"
    WRONG_TARGET = "WRONG_TARGET"
    AMBIGUOUS = "AMBIGUOUS"
    NO_RESPONSE = "NO_RESPONSE"


@dataclass(frozen=True)
class IdentityProbeResult:
    status: IdentityProbeStatus
    role: EcuRole | None = None
    target_id: int | None = None
    config_version: int | None = None


def probe_identity(transport: Any, config: ProjectReleaseConfig) -> IdentityProbeResult:
    request = b"\x22" + config.identity_did.did.to_bytes(2, "big")
    try:
        response = transport.request(
            request,
            expect_prefix=b"\x62" + request[1:],
            timeout_ms=config.timing.poll_timeout_ms,
            ignore_invalid_responses=True,
        )
    except HostToolError as exc:
        if exc.category is ErrorCategory.TRANSPORT:
            return IdentityProbeResult(IdentityProbeStatus.NO_RESPONSE)
        if exc.category is ErrorCategory.UDS:
            return IdentityProbeResult(IdentityProbeStatus.AMBIGUOUS)
        raise
    payload = response.payload
    if len(payload) != 3 + config.identity_did.response_length or payload[:3] != b"\x62\xF1\xA0":
        return IdentityProbeResult(IdentityProbeStatus.AMBIGUOUS)
    data = payload[3:]
    target_id = int.from_bytes(data[0:4], "little")
    config_version = int.from_bytes(data[6:8], "little")
    if target_id != config.ecu_target_id or config_version != config.config_version or data[5] != config.identity_did.protocol:
        return IdentityProbeResult(
            IdentityProbeStatus.WRONG_TARGET,
            target_id=target_id,
            config_version=config_version,
        )
    try:
        role = EcuRole(data[4])
    except ValueError:
        return IdentityProbeResult(IdentityProbeStatus.AMBIGUOUS)
    status = (
        IdentityProbeStatus.APP_CONFIRMED
        if role is EcuRole.APP else IdentityProbeStatus.BOOT_CONFIRMED
    )
    return IdentityProbeResult(status, role, target_id, config_version)
