from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep

from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.trace import TraceLogger
from unified_can_lin_host_tool.transport.base import BusAdapter, CanFrame

SleepFunc = Callable[[float], None]
CAN_CLASSIC_DLC = 8
CAN_ISOTP_SF_PAYLOAD_MAX = 7
CAN_ISOTP_FF_PAYLOAD_LEN = 6
CAN_ISOTP_CF_PAYLOAD_LEN = 7
CAN_ISOTP_MAX_PAYLOAD_LEN = 0xFFF
CAN_ISOTP_DEFAULT_FC_BLOCK_SIZE = 0
CAN_ISOTP_DEFAULT_STMIN = 0
CAN_ISOTP_MAX_WAIT_FRAMES = 2


@dataclass(frozen=True)
class UdsResponse:
    payload: bytes
    raw_frames: tuple[CanFrame, ...]


class CanIsoTpTransport:
    def __init__(
        self,
        adapter: BusAdapter,
        profile: ToolProfile,
        *,
        sleep_func: SleepFunc = sleep,
        trace_logger: TraceLogger | None = None,
    ) -> None:
        if profile.bus.type != "CAN":
            raise HostToolError(ErrorCategory.PROFILE, "CanIsoTpTransport requires a CAN profile")
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
        ignore_invalid_responses: bool = False,
        cancel_token: CancellationToken | None = None,
    ) -> UdsResponse:
        if not uds_payload:
            raise HostToolError(ErrorCategory.UDS, "UDS request payload must not be empty")

        self._send_request_payload(uds_payload, cancel_token=cancel_token)
        return self._poll_response(
            expect_sid=expect_sid,
            expect_prefix=expect_prefix,
            timeout_ms=timeout_ms or self._profile.uds.poll_timeout_ms,
            allow_response_pending=allow_response_pending,
            ignore_invalid_responses=ignore_invalid_responses,
            cancel_token=cancel_token,
        )

    def _send_request_payload(
        self,
        uds_payload: bytes,
        *,
        cancel_token: CancellationToken | None,
    ) -> None:
        if len(uds_payload) <= CAN_ISOTP_SF_PAYLOAD_MAX:
            self._send_request_frame(bytes([len(uds_payload)]) + uds_payload, cancel_token=cancel_token)
            return
        if len(uds_payload) > CAN_ISOTP_MAX_PAYLOAD_LEN:
            raise HostToolError(ErrorCategory.UDS, "CAN ISO-TP request is too long")

        first_frame = bytes([0x10 | ((len(uds_payload) >> 8) & 0x0F), len(uds_payload) & 0xFF])
        first_frame += uds_payload[:CAN_ISOTP_FF_PAYLOAD_LEN]
        self._send_request_frame(first_frame, cancel_token=cancel_token)

        block_size, stmin_seconds = self._receive_flow_control(cancel_token=cancel_token)
        sequence_number = 1
        sent_in_block = 0
        for offset in range(CAN_ISOTP_FF_PAYLOAD_LEN, len(uds_payload), CAN_ISOTP_CF_PAYLOAD_LEN):
            chunk = uds_payload[offset : offset + CAN_ISOTP_CF_PAYLOAD_LEN]
            if stmin_seconds > 0.0:
                self._sleep(stmin_seconds)
            _throw_if_cancelled(cancel_token)
            self._send_request_frame(bytes([0x20 | sequence_number]) + chunk, cancel_token=cancel_token)
            sequence_number = (sequence_number + 1) & 0x0F
            sent_in_block += 1
            if block_size != 0 and sent_in_block >= block_size and (offset + CAN_ISOTP_CF_PAYLOAD_LEN) < len(uds_payload):
                block_size, stmin_seconds = self._receive_flow_control(cancel_token=cancel_token)
                sent_in_block = 0

    def _receive_flow_control(self, *, cancel_token: CancellationToken | None) -> tuple[int, float]:
        wait_count = 0
        deadline = monotonic() + self._profile.uds.p2_ms / 1000.0

        while monotonic() <= deadline:
            _throw_if_cancelled(cancel_token)
            frame = self._adapter.receive_can_frame(self._profile.bus.response_id, self._poll_slice_ms(deadline))
            if frame is None:
                continue
            self._write_trace("RX", frame.can_id, frame.data)
            if frame.can_id != self._profile.bus.response_id or len(frame.data) != CAN_CLASSIC_DLC:
                continue
            pci = frame.data[0]
            if (pci & 0xF0) != 0x30:
                continue
            flow_status = pci & 0x0F
            if flow_status == 0x00:
                return frame.data[1], _decode_stmin(frame.data[2])
            if flow_status == 0x01:
                wait_count += 1
                if wait_count > CAN_ISOTP_MAX_WAIT_FRAMES:
                    raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP FC WAIT exceeds limit")
                deadline = monotonic() + self._profile.uds.p2_ms / 1000.0
                continue
            if flow_status == 0x02:
                raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP receiver reported overflow")
            raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP FC flow status is invalid")

        raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP flow control timeout")

    def _poll_response(
        self,
        *,
        expect_sid: int | None,
        expect_prefix: bytes | None,
        timeout_ms: int,
        allow_response_pending: bool,
        ignore_invalid_responses: bool,
        cancel_token: CancellationToken | None,
    ) -> UdsResponse:
        raw_frames: list[CanFrame] = []
        deadline = monotonic() + timeout_ms / 1000.0
        last_unexpected_response: HostToolError | None = None

        while monotonic() <= deadline:
            _throw_if_cancelled(cancel_token)
            frame = self._adapter.receive_can_frame(self._profile.bus.response_id, self._poll_slice_ms(deadline))
            _throw_if_cancelled(cancel_token)
            if frame is None:
                self._sleep(self._profile.uds.poll_gap_ms / 1000.0)
                continue
            self._write_trace("RX", frame.can_id, frame.data)
            try:
                payload, payload_frames = self._parse_response_payload(frame, deadline, cancel_token)
            except HostToolError as exc:
                if ignore_invalid_responses and exc.category == ErrorCategory.TRANSPORT:
                    last_unexpected_response = exc
                    continue
                raise
            raw_frames.extend(payload_frames)

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
        raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP response timeout")

    def _parse_response_payload(
        self,
        first_frame: CanFrame,
        deadline: float,
        cancel_token: CancellationToken | None,
    ) -> tuple[bytes, tuple[CanFrame, ...]]:
        if first_frame.can_id != self._profile.bus.response_id:
            raise HostToolError(ErrorCategory.TRANSPORT, "CAN response ID mismatch")
        if len(first_frame.data) != CAN_CLASSIC_DLC:
            raise HostToolError(ErrorCategory.TRANSPORT, "CAN response frame must be 8 bytes")

        pci = first_frame.data[0]
        frame_type = pci & 0xF0
        if frame_type == 0x00:
            payload_len = pci & 0x0F
            if payload_len == 0 or payload_len > CAN_ISOTP_SF_PAYLOAD_MAX:
                raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP single-frame length is invalid")
            return first_frame.data[1 : 1 + payload_len], (first_frame,)
        if frame_type != 0x10:
            raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP response starts with invalid PCI")

        total_len = ((pci & 0x0F) << 8) | first_frame.data[1]
        if total_len <= CAN_ISOTP_SF_PAYLOAD_MAX or total_len > CAN_ISOTP_MAX_PAYLOAD_LEN:
            raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP first-frame length is invalid")

        self._send_flow_control(cancel_token=cancel_token)
        payload = bytearray(first_frame.data[2:8])
        raw_frames = [first_frame]
        expected_sequence = 1
        while len(payload) < total_len and monotonic() <= deadline:
            _throw_if_cancelled(cancel_token)
            frame = self._adapter.receive_can_frame(self._profile.bus.response_id, self._poll_slice_ms(deadline))
            if frame is None:
                continue
            self._write_trace("RX", frame.can_id, frame.data)
            if frame.can_id != self._profile.bus.response_id or len(frame.data) != CAN_CLASSIC_DLC:
                raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP consecutive frame is invalid")
            pci = frame.data[0]
            if (pci & 0xF0) != 0x20 or (pci & 0x0F) != expected_sequence:
                raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP consecutive frame sequence mismatch")
            raw_frames.append(frame)
            payload.extend(frame.data[1:8])
            expected_sequence = (expected_sequence + 1) & 0x0F

        if len(payload) < total_len:
            raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP consecutive frame timeout")
        return bytes(payload[:total_len]), tuple(raw_frames)

    def _send_flow_control(self, *, cancel_token: CancellationToken | None) -> None:
        data = bytes([0x30, CAN_ISOTP_DEFAULT_FC_BLOCK_SIZE, CAN_ISOTP_DEFAULT_STMIN])
        self._send_request_frame(data, cancel_token=cancel_token)

    def _send_request_frame(self, data: bytes, *, cancel_token: CancellationToken | None) -> None:
        _throw_if_cancelled(cancel_token)
        padded = _pad(data, self._profile.bus.padding)
        self._adapter.send_can_frame(self._profile.bus.request_id, padded)
        self._write_trace("TX", self._profile.bus.request_id, padded)
        if self._profile.uds.frame_gap_ms > 0:
            self._sleep(self._profile.uds.frame_gap_ms / 1000.0)
        _throw_if_cancelled(cancel_token)

    def _poll_slice_ms(self, deadline: float) -> int:
        remaining_ms = max(1, int((deadline - monotonic()) * 1000))
        return min(remaining_ms, max(1, self._profile.uds.poll_gap_ms))

    def _write_trace(self, direction: str, frame_id: int, data: bytes) -> None:
        if self._trace is not None:
            self._trace.write(TraceEvent(direction=direction, frame_id=frame_id, data=data, bus="CAN"))


def _pad(data: bytes, padding: int) -> bytes:
    if len(data) > CAN_CLASSIC_DLC:
        raise HostToolError(ErrorCategory.TRANSPORT, "CAN frame exceeds 8 bytes")
    return data + bytes([padding] * (CAN_CLASSIC_DLC - len(data)))


def _decode_stmin(stmin: int) -> float:
    if 0x00 <= stmin <= 0x7F:
        return stmin / 1000.0
    if 0xF1 <= stmin <= 0xF9:
        return (stmin - 0xF0) / 10000.0
    raise HostToolError(ErrorCategory.TRANSPORT, "CAN ISO-TP STmin is invalid")


def _is_response_pending(payload: bytes) -> bool:
    return len(payload) >= 3 and payload[0] == 0x7F and payload[2] == 0x78


def _throw_if_cancelled(cancel_token: CancellationToken | None) -> None:
    if cancel_token is not None:
        cancel_token.throw_if_cancelled()
