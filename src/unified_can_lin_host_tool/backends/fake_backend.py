from __future__ import annotations

from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice


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
