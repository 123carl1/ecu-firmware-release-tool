from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep

from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.base import BusAdapter, LinFrame

SleepFunc = Callable[[float], None]
LIN_SINGLE_FRAME_UDS_PAYLOAD_MAX = 6


@dataclass(frozen=True)
class UdsResponse:
    payload: bytes
    raw_frames: tuple[LinFrame, ...]


class LinDiagTransport:
    def __init__(
        self,
        adapter: BusAdapter,
        profile: ToolProfile,
        *,
        sleep_func: SleepFunc = sleep,
        trace_logger: TraceLogger | None = None,
    ) -> None:
        self._adapter = adapter
        self._profile = profile
        self._sleep = sleep_func
        self._trace = trace_logger

    def request(
        self,
        uds_payload: bytes,
        *,
        expect_sid: int | None = None,
        expect_prefix: bytes | None = None,
        timeout_ms: int | None = None,
        allow_response_pending: bool = False,
        cancel_token: CancellationToken | None = None,
    ) -> UdsResponse:
        if not uds_payload:
            raise HostToolError(ErrorCategory.UDS, "UDS request payload must not be empty")

        for frame in self._build_request_frames(uds_payload):
            _throw_if_cancelled(cancel_token)
            self._adapter.send_lin_frame(self._profile.bus.request_id, frame)
            self._write_trace("TX", self._profile.bus.request_id, frame)
            self._sleep(self._profile.uds.frame_gap_ms / 1000.0)
            _throw_if_cancelled(cancel_token)

        return self._poll_response(
            expect_sid=expect_sid,
            expect_prefix=expect_prefix,
            timeout_ms=timeout_ms or self._profile.uds.poll_timeout_ms,
            allow_response_pending=allow_response_pending,
            cancel_token=cancel_token,
        )

    def _build_request_frames(self, uds_payload: bytes) -> list[bytes]:
        if len(uds_payload) <= LIN_SINGLE_FRAME_UDS_PAYLOAD_MAX:
            return [_pad(bytes([self._profile.bus.nad, len(uds_payload)]) + uds_payload)]

        if len(uds_payload) > 0xFFF:
            raise HostToolError(ErrorCategory.UDS, "LIN UDS request is too long")

        frames = [
            _pad(
                bytes(
                    [
                        self._profile.bus.nad,
                        0x10 | ((len(uds_payload) >> 8) & 0x0F),
                        len(uds_payload) & 0xFF,
                    ]
                )
                + uds_payload[:5]
            )
        ]

        sequence_number = 1
        for offset in range(5, len(uds_payload), 6):
            chunk = uds_payload[offset : offset + 6]
            frames.append(_pad(bytes([self._profile.bus.nad, 0x20 | sequence_number]) + chunk))
            sequence_number = (sequence_number + 1) & 0x0F

        return frames

    def _poll_response(
        self,
        *,
        expect_sid: int | None,
        expect_prefix: bytes | None,
        timeout_ms: int,
        allow_response_pending: bool,
        cancel_token: CancellationToken | None,
    ) -> UdsResponse:
        raw_frames: list[LinFrame] = []
        deadline = monotonic() + timeout_ms / 1000.0
        last_unexpected_response: HostToolError | None = None

        while monotonic() <= deadline:
            _throw_if_cancelled(cancel_token)
            remaining_ms = max(1, int((deadline - monotonic()) * 1000))
            poll_timeout = min(remaining_ms, self._profile.uds.poll_gap_ms)
            frame = self._adapter.receive_lin_frame(self._profile.bus.response_id, poll_timeout)
            _throw_if_cancelled(cancel_token)
            if frame is None:
                self._sleep(self._profile.uds.poll_gap_ms / 1000.0)
                _throw_if_cancelled(cancel_token)
                continue

            raw_frames.append(frame)
            self._write_trace("RX", frame.frame_id, frame.data)
            payload = self._parse_single_frame_response(frame)
            if _is_response_pending(payload):
                if allow_response_pending:
                    continue
                raise HostToolError(ErrorCategory.UDS, "received NRC 0x78 response pending")

            if payload.startswith(b"\x7F"):
                raise HostToolError(ErrorCategory.UDS, f"received NRC 0x{payload[-1]:02X}")
            if expect_sid is not None and payload[0] != expect_sid:
                last_unexpected_response = HostToolError(ErrorCategory.UDS, "positive response SID mismatch")
                continue
            if expect_prefix is not None and not payload.startswith(expect_prefix):
                last_unexpected_response = HostToolError(ErrorCategory.UDS, "positive response prefix mismatch")
                continue
            return UdsResponse(payload=payload, raw_frames=tuple(raw_frames))

        if last_unexpected_response is not None:
            raise last_unexpected_response
        raise HostToolError(ErrorCategory.TRANSPORT, "LIN UDS response timeout")

    def _parse_single_frame_response(self, frame: LinFrame) -> bytes:
        if frame.frame_id != self._profile.bus.response_id:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN response ID mismatch")
        if len(frame.data) != 8:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN response frame must be 8 bytes")
        if frame.data[0] != self._profile.bus.nad:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN response NAD mismatch")

        pci = frame.data[1]
        if (pci & 0xF0) != 0:
            raise HostToolError(ErrorCategory.TRANSPORT, "only single-frame LIN responses are supported in M0")
        payload_len = pci & 0x0F
        if payload_len == 0:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN response payload length is zero")
        if payload_len > 6:
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN response payload length is invalid")
        return frame.data[2 : 2 + payload_len]

    def _write_trace(self, direction: str, frame_id: int, data: bytes) -> None:
        if self._trace is not None:
            self._trace.write(TraceEvent(direction=direction, frame_id=frame_id, data=data))


def _pad(data: bytes) -> bytes:
    if len(data) > 8:
        raise HostToolError(ErrorCategory.TRANSPORT, "LIN frame exceeds 8 bytes")
    return data + bytes([0xFF] * (8 - len(data)))


def _is_response_pending(payload: bytes) -> bool:
    return len(payload) >= 3 and payload[0] == 0x7F and payload[2] == 0x78


def _throw_if_cancelled(cancel_token: CancellationToken | None) -> None:
    if cancel_token is not None:
        cancel_token.throw_if_cancelled()
