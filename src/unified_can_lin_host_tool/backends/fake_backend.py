from __future__ import annotations

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport
from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice


class FakeHostSession:
    def __init__(self, profile: ToolProfile) -> None:
        self.profile = profile
        self.adapter = FakeLinAdapter(
            responses=[
                (profile.bus.response_id, _lin_single(profile.bus.nad, bytes.fromhex("50 01"))),
                (profile.bus.response_id, _lin_single(profile.bus.nad, bytes.fromhex("50 03"))),
                (
                    profile.bus.response_id,
                    _lin_single(profile.bus.nad, bytes.fromhex("67 01 35 79 24 68")),
                ),
            ]
        )
        self.transport = LinDiagTransport(self.adapter, profile)

    def request_uds(self, payload: bytes) -> bytes:
        return self.transport.request(payload).payload


class FakeHostBackend:
    def scan(self) -> list[UiDevice]:
        return [
            UiDevice(
                vendor="TSMaster",
                name="Fake TSMaster",
                serial="FAKE-TS-001",
                channels=[
                    UiChannel(
                        vendor="TSMaster",
                        device_name="Fake TSMaster",
                        channel_name="LIN 0",
                        bus="LIN",
                        channel_index=0,
                    )
                ],
            ),
            UiDevice(
                vendor="USB2XXX",
                name="Fake USB2XXX",
                serial="FAKE-USB2-001",
                channels=[
                    UiChannel(
                        vendor="USB2XXX",
                        device_name="Fake USB2XXX",
                        channel_name="LIN 0",
                        bus="LIN",
                        channel_index=0,
                    )
                ],
            ),
        ]

    def connect(self, channel: UiChannel, profile: ToolProfile) -> FakeHostSession:
        if channel.bus != "LIN":
            raise ValueError("fake backend only supports LIN in M1 Alpha")
        return FakeHostSession(profile)


def _lin_single(nad: int, payload: bytes) -> bytes:
    if len(payload) > 6:
        raise ValueError("fake LIN single-frame payload must be at most 6 bytes")
    return bytes([nad, len(payload)]) + payload + bytes([0xFF] * (6 - len(payload)))
