from dataclasses import dataclass

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.release.ecu_identity import (
    EcuRole,
    IdentityProbeStatus,
    probe_identity,
)
from unified_can_lin_host_tool.release.project_config import ProjectCode, get_project_config


@dataclass(frozen=True)
class Response:
    payload: bytes


class Transport:
    def __init__(self, payload: bytes | Exception):
        self.payload = payload
        self.requests: list[bytes] = []

    def request(self, payload: bytes, **kwargs):
        self.requests.append(payload)
        if isinstance(self.payload, Exception):
            raise self.payload
        return Response(self.payload)


def _response(role: int, *, target: int = 0x41503541, version: int = 1) -> bytes:
    return b"\x62\xF1\xA0" + target.to_bytes(4, "little") + bytes((role, 1)) + version.to_bytes(2, "little")


def test_probe_confirms_app_identity_with_one_read_only_request() -> None:
    transport = Transport(_response(1))
    result = probe_identity(transport, get_project_config(ProjectCode.AS5PR))
    assert result.status is IdentityProbeStatus.APP_CONFIRMED
    assert result.role is EcuRole.APP
    assert transport.requests == [b"\x22\xF1\xA0"]


def test_probe_confirms_boot_identity() -> None:
    result = probe_identity(Transport(_response(2)), get_project_config(ProjectCode.AS5PR))
    assert result.status is IdentityProbeStatus.BOOT_CONFIRMED
    assert result.role is EcuRole.BOOT


def test_probe_rejects_target_or_version_mismatch() -> None:
    config = get_project_config(ProjectCode.AS5PR)
    assert probe_identity(Transport(_response(1, target=0)), config).status is IdentityProbeStatus.WRONG_TARGET
    assert probe_identity(Transport(_response(1, version=2)), config).status is IdentityProbeStatus.WRONG_TARGET


def test_probe_marks_invalid_role_or_length_ambiguous() -> None:
    config = get_project_config(ProjectCode.AS5PR)
    assert probe_identity(Transport(_response(3)), config).status is IdentityProbeStatus.AMBIGUOUS
    assert probe_identity(Transport(b"\x62\xF1\xA0\x00"), config).status is IdentityProbeStatus.AMBIGUOUS


def test_probe_maps_transport_timeout_to_no_response() -> None:
    error = HostToolError(ErrorCategory.TRANSPORT, "timeout")
    result = probe_identity(Transport(error), get_project_config(ProjectCode.AS5PR))
    assert result.status is IdentityProbeStatus.NO_RESPONSE
