from __future__ import annotations

from unified_can_lin_host_tool.e68.crc32 import e68_crc32
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.transport.base import LinFrame


def _request_download_response(profile: ToolProfile) -> bytes:
    max_number = profile.uds.max_transfer_payload + 2
    return bytes([0x74, 0x20]) + max_number.to_bytes(2, "big")


class FakeLinAdapter:
    def __init__(self, responses: list[tuple[int, bytes]] | None = None) -> None:
        self.sent_frames: list[tuple[int, bytes]] = []
        self._responses = [LinFrame(frame_id, data) for frame_id, data in (responses or [])]

    def send_lin_frame(self, frame_id: int, data: bytes) -> None:
        self.sent_frames.append((frame_id, data))

    def receive_lin_frame(self, frame_id: int, timeout_ms: int) -> LinFrame | None:
        for index, response in enumerate(self._responses):
            if response.frame_id == frame_id:
                return self._responses.pop(index)
        return None

    @classmethod
    def for_e68_flash_success(
        cls,
        profile: ToolProfile,
        *,
        flash_driver_data: bytes,
        app_data: bytes,
        start_in_bootloader: bool = False,
    ) -> "FakeLinAdapter":
        responses: list[tuple[int, bytes]] = []
        nad = profile.bus.nad
        response_id = profile.bus.response_id
        app_seed = bytes.fromhex("35 79 24 68")
        boot_seed = bytes.fromhex("24 68 35 79")

        def add(payload: bytes) -> None:
            responses.append((response_id, _lin_single(nad, payload)))

        if not start_in_bootloader:
            add(bytes.fromhex("50 01"))
            add(bytes.fromhex("50 03"))
            add(bytes.fromhex("67 01") + app_seed)
            add(bytes.fromhex("67 02"))
            add(bytes.fromhex("71 01 02 03 00"))
            add(bytes.fromhex("50 02"))
        add(bytes.fromhex("50 02"))
        add(bytes.fromhex("67 09") + boot_seed)
        add(bytes.fromhex("67 0A"))

        add(_request_download_response(profile))
        for block_sequence in _block_sequences(flash_driver_data, profile.uds.max_transfer_payload):
            add(bytes([0x76, block_sequence]))
        add(bytes([0x77]) + e68_crc32(flash_driver_data).to_bytes(4, "big"))
        add(bytes.fromhex("71 01 02 02 00"))

        add(bytes.fromhex("7F 31 78"))
        add(bytes.fromhex("71 01 FF 00"))

        add(_request_download_response(profile))
        for block_sequence in _block_sequences(app_data, profile.uds.max_transfer_payload):
            add(bytes([0x76, block_sequence]))
        add(bytes([0x77]) + e68_crc32(app_data).to_bytes(4, "big"))
        add(bytes.fromhex("7F 31 78"))
        add(bytes.fromhex("71 01 FF 01 00"))
        add(bytes.fromhex("51 01"))
        add(bytes.fromhex("62 30 00 30 30 30"))

        return cls(responses=responses)

    def sent_uds_payloads(self) -> list[bytes]:
        payloads: list[bytes] = []
        pending_total: int | None = None
        pending = bytearray()

        for _, data in self.sent_frames:
            pci = data[1]
            frame_type = pci & 0xF0
            if frame_type == 0x00:
                payloads.append(data[2 : 2 + (pci & 0x0F)])
            elif frame_type == 0x10:
                pending_total = ((pci & 0x0F) << 8) | data[2]
                pending = bytearray(data[3:8])
                if len(pending) >= pending_total:
                    payloads.append(bytes(pending[:pending_total]))
                    pending_total = None
                    pending.clear()
            elif frame_type == 0x20 and pending_total is not None:
                pending.extend(data[2:8])
                if len(pending) >= pending_total:
                    payloads.append(bytes(pending[:pending_total]))
                    pending_total = None
                    pending.clear()

        return payloads


def _lin_single(nad: int, payload: bytes) -> bytes:
    if len(payload) > 6:
        raise ValueError("fake LIN single-frame payload must be at most 6 bytes")
    return bytes([nad, len(payload)]) + payload + bytes([0xFF] * (6 - len(payload)))


def _block_sequences(data: bytes, max_payload: int) -> list[int]:
    sequences: list[int] = []
    block_sequence = 1
    for _ in range(0, len(data), max_payload):
        sequences.append(block_sequence)
        block_sequence = (block_sequence + 1) & 0xFF
    return sequences
