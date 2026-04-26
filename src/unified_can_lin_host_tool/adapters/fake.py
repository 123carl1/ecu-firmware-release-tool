from __future__ import annotations

from unified_can_lin_host_tool.transport.base import LinFrame


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

