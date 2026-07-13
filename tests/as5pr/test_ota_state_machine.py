from dataclasses import replace
from pathlib import Path

from unified_can_lin_host_tool.as5pr.ota_state_machine import (
    As5prOtaStateMachine,
    OtaResultStatus,
)
from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.release.package import (
    ReleaseResource,
    ResourceKind,
    VerifiedReleasePackage,
)
from unified_can_lin_host_tool.release.project_config import ProjectCode, compute_config_digest, get_project_config


class Response:
    def __init__(self, payload: bytes):
        self.payload = payload


class ScriptedTransport:
    def __init__(self, initial_role: int, *, cancel_on_erase=False, boot_security_needs_session=False,
                 boot_probes_after_reset=0):
        self.role = initial_role
        self.requests: list[bytes] = []
        self.cancel_on_erase = cancel_on_erase
        self.cancel_token = None
        self.boot_security_needs_session = boot_security_needs_session
        self.boot_probes_after_reset = boot_probes_after_reset
        self.reset_seen = False

    def request(self, payload: bytes, **_kwargs):
        self.requests.append(payload)
        if payload == bytes.fromhex("22 F1 A0"):
            if self.reset_seen and self.boot_probes_after_reset > 0:
                self.boot_probes_after_reset -= 1
                self.role = 2
            elif self.reset_seen:
                self.role = 1
            data = (0x41503541).to_bytes(4, "little") + bytes([self.role, 1]) + (1).to_bytes(2, "little")
            return Response(bytes.fromhex("62 F1 A0") + data)
        if payload == bytes.fromhex("10 02"):
            self.role = 2
            return Response(bytes.fromhex("50 02 00 32 13 88"))
        if payload == bytes.fromhex("27 09"):
            if self.boot_security_needs_session:
                self.boot_security_needs_session = False
                raise HostToolError(ErrorCategory.UDS, "received NRC 0x7F")
            return Response(bytes.fromhex("67 09 12 34 56 78"))
        if payload.startswith(bytes.fromhex("27 0A")):
            return Response(bytes.fromhex("67 0A"))
        if payload == bytes.fromhex("27 01"):
            return Response(bytes.fromhex("67 01 12 34 56 78"))
        if payload.startswith(bytes.fromhex("27 02")):
            return Response(bytes.fromhex("67 02"))
        if payload.startswith(bytes.fromhex("34")):
            return Response(bytes.fromhex("74 20 00 40"))
        if payload.startswith(bytes.fromhex("36")):
            return Response(bytes([0x76, payload[1]]))
        if payload.startswith(bytes.fromhex("37")):
            return Response(bytes([0x77]) + payload[1:])
        if payload.startswith(bytes.fromhex("31 01 FF 00")):
            if self.cancel_on_erase:
                self.cancel_token.cancel()
            return Response(bytes.fromhex("71 01 FF 00 00"))
        if payload == bytes.fromhex("11 01"):
            self.reset_seen = True
            return Response(bytes.fromhex("51 01"))
        positive = {
            bytes.fromhex("10 01"): bytes.fromhex("50 01"),
            bytes.fromhex("10 03"): bytes.fromhex("50 03"),
            bytes.fromhex("31 01 02 03"): bytes.fromhex("71 01 02 03 00"),
            bytes.fromhex("31 01 02 02"): bytes.fromhex("71 01 02 02 00"),
            bytes.fromhex("31 01 FF 01"): bytes.fromhex("71 01 FF 01 00"),
            bytes.fromhex("22 30 00"): bytes.fromhex("62 30 00 01"),
        }
        return Response(positive[payload])


def package() -> VerifiedReleasePackage:
    cfg = get_project_config(ProjectCode.AS5PR)
    resources = (
        ReleaseResource(ResourceKind.BOOT, cfg.project_code, 0, 0, b"boot"),
        ReleaseResource(ResourceKind.APP, cfg.authentication.app_target_id, cfg.memory.app_start, 1, b"app-data"),
        ReleaseResource(ResourceKind.FLASH_DRIVER, cfg.authentication.flash_driver_target_id, cfg.memory.flash_driver_ram, 1, b"driver"),
    )
    return VerifiedReleasePackage(Path("x.erel"), "a" * 64, ProjectCode.AS5PR, cfg.project_code, 1,
                                  compute_config_digest(cfg), b"b" * 32, "1" * 40, 0, 1, resources)


def test_app_entry_probes_before_and_after_handoff_and_completes():
    transport = ScriptedTransport(1)
    result = As5prOtaStateMachine(transport).run(package())
    assert result.status is OtaResultStatus.COMPLETED
    assert transport.requests[:7] == [
        bytes.fromhex("22 F1 A0"), bytes.fromhex("10 01"), bytes.fromhex("10 03"),
        bytes.fromhex("27 01"), bytes.fromhex("27 02 70 10 00 B2"),
        bytes.fromhex("31 01 02 03"), bytes.fromhex("10 02"),
    ]
    assert transport.requests[7] == bytes.fromhex("22 F1 A0")


def test_legacy_package_without_boot_resource_is_rejected_before_bus_access():
    release_package = package()
    runtime_resources = tuple(
        item for item in release_package.resources
        if item.kind in (ResourceKind.APP, ResourceKind.FLASH_DRIVER)
    )
    transport = ScriptedTransport(1)

    result = As5prOtaStateMachine(transport).run(
        replace(release_package, resources=runtime_resources)
    )

    assert result.status is OtaResultStatus.PACKAGE_REJECTED
    assert transport.requests == []


def test_malformed_request_download_response_is_rejected_before_transfer_data():
    transport = ScriptedTransport(2)
    original = transport.request

    def malformed(payload, **kwargs):
        if payload.startswith(bytes.fromhex("34")):
            return Response(bytes.fromhex("74 10 40 00"))
        return original(payload, **kwargs)

    transport.request = malformed
    result = As5prOtaStateMachine(transport).run(package())
    assert result.status is OtaResultStatus.CANCELLED_SAFE
    assert not any(item.startswith(bytes.fromhex("36")) for item in transport.requests)


def test_ota_reports_stage_and_transfer_progress():
    release_package = package()
    transport = ScriptedTransport(1)
    events = []

    result = As5prOtaStateMachine(transport, progress=events.append, sleep_func=lambda _: None).run(release_package)

    assert result.status is OtaResultStatus.COMPLETED
    assert events[0].percent == 5
    assert events[-1].percent == 100
    assert any(event.stage == "下载 App" and event.total for event in events)
    assert any(event.stage == "复位 ECU" and event.percent == 96 for event in events)
    assert all(0 <= event.percent <= 100 for event in events)


def test_progress_sink_failure_after_erase_does_not_interrupt_programming():
    transport = ScriptedTransport(1)

    def broken_progress(event):
        if event.stage == "下载 App":
            raise BrokenPipeError("GUI pipe closed")

    result = As5prOtaStateMachine(
        transport,
        progress=broken_progress,
        sleep_func=lambda _: None,
    ).run(package())

    assert result.status is OtaResultStatus.COMPLETED
    assert any(item.startswith(bytes.fromhex("37")) for item in transport.requests)
    assert bytes.fromhex("31 01 FF 01") in transport.requests
    assert bytes.fromhex("11 01") in transport.requests


def test_boot_entry_skips_app_preprogramming_and_has_no_manual_flag():
    transport = ScriptedTransport(2)
    result = As5prOtaStateMachine(transport).run(package())
    assert result.status is OtaResultStatus.COMPLETED
    assert transport.requests[:3] == [bytes.fromhex("22 F1 A0"), bytes.fromhex("27 09"), bytes.fromhex("27 0A 21 57 00 0F")]
    assert bytes.fromhex("31 01 02 03") not in transport.requests


def test_boot_cold_entry_switches_to_programming_only_for_nrc_7f_then_reprobes():
    transport = ScriptedTransport(2, boot_security_needs_session=True)
    result = As5prOtaStateMachine(transport).run(package())
    assert result.status is OtaResultStatus.COMPLETED
    assert transport.requests[:6] == [
        bytes.fromhex("22 F1 A0"), bytes.fromhex("27 09"), bytes.fromhex("10 02"),
        bytes.fromhex("22 F1 A0"), bytes.fromhex("27 09"), bytes.fromhex("27 0A 21 57 00 0F"),
    ]


def test_wrong_online_target_is_rejected_before_any_erase():
    transport = ScriptedTransport(1)
    original = transport.request
    def wrong(payload, **kwargs):
        response = original(payload, **kwargs)
        if payload == bytes.fromhex("22 F1 A0"):
            response.payload = bytes.fromhex("62 F1 A0") + (0x12345678).to_bytes(4, "little") + bytes.fromhex("01 01 01 00")
        return response
    transport.request = wrong
    result = As5prOtaStateMachine(transport).run(package())
    assert result.status is OtaResultStatus.IDENTITY_REJECTED
    assert not any(item.startswith(bytes.fromhex("31 01 FF 00")) for item in transport.requests)


def test_cancel_after_erase_does_not_send_transfer_exit():
    transport = ScriptedTransport(2, cancel_on_erase=True)
    token = CancellationToken()
    transport.cancel_token = token
    result = As5prOtaStateMachine(transport).run(package(), token)
    assert result.status is OtaResultStatus.ECU_IN_BOOT
    erase_index = next(i for i, item in enumerate(transport.requests) if item.startswith(bytes.fromhex("31 01 FF 00")))
    assert not any(item.startswith(bytes.fromhex("37")) for item in transport.requests[erase_index + 1:])


def test_erase_response_timeout_is_destructive_unknown_not_safe_cancel():
    transport = ScriptedTransport(2)
    original = transport.request

    def timeout(payload, **kwargs):
        if payload.startswith(bytes.fromhex("31 01 FF 00")):
            transport.requests.append(payload)
            raise TimeoutError("erase response timeout")
        return original(payload, **kwargs)

    transport.request = timeout
    result = As5prOtaStateMachine(transport).run(package())
    assert result.status is OtaResultStatus.FAILED_UNKNOWN


def test_reset_verification_polls_while_boot_is_still_online_then_confirms_app():
    transport = ScriptedTransport(1, boot_probes_after_reset=2)
    result = As5prOtaStateMachine(transport, sleep_func=lambda _: None).run(package())
    assert result.status is OtaResultStatus.COMPLETED
    reset_index = transport.requests.index(bytes.fromhex("11 01"))
    assert transport.requests[reset_index + 1:reset_index + 4] == [bytes.fromhex("22 F1 A0")] * 3


def test_mixed_project_package_is_rejected_before_bus_access():
    transport = ScriptedTransport(1)
    result = As5prOtaStateMachine(transport).run(replace(package(), project=ProjectCode.E68))
    assert result.status is OtaResultStatus.PACKAGE_REJECTED
    assert transport.requests == []
