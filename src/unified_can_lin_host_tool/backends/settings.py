from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TsmasterSettings:
    dll_path: str = "D:/software/TSMaster/bin64/TSMaster.dll"
    app_name: str = "Codex_UnifiedHostTool"
    project_dir: str | None = "D:/01_WorkProgram/Company_Program/10_AI_Adapted_Seat/DAU_FM33_HT/上位机"
    app_channel: int = 0
    hw_name: str = "TC1016"
    hw_subtype: int = 11
    hw_index: int = 0
    hw_channel: int = 0
    baud_kbps: float = 19.2
    close_mode: str = "skip"

    def summary_lines(self) -> list[str]:
        return [
            f"TSMaster.dll_path: {self.dll_path}",
            f"TSMaster.app_name: {self.app_name}",
            f"TSMaster.project_dir: {self.project_dir}",
            f"TSMaster.app_channel: {self.app_channel}",
            f"TSMaster.hw_name: {self.hw_name}",
            f"TSMaster.hw_subtype: {self.hw_subtype}",
            f"TSMaster.hw_index: {self.hw_index}",
            f"TSMaster.hw_channel: {self.hw_channel}",
            f"TSMaster.baud_kbps: {self.baud_kbps}",
            f"TSMaster.close_mode: {self.close_mode}",
        ]


@dataclass(frozen=True)
class Usb2xxxSettings:
    # M2B will verify the exact SDK install path with real hardware.
    dll_path: str = "D:/software/USB2XXX/USB2XXX.dll"
    device_index: int = 0
    channel_index: int = 0
    baudrate: int = 19200

    def summary_lines(self) -> list[str]:
        return [
            f"USB2XXX.dll_path: {self.dll_path}",
            f"USB2XXX.device_index: {self.device_index}",
            f"USB2XXX.channel_index: {self.channel_index}",
            f"USB2XXX.baudrate: {self.baudrate}",
        ]


@dataclass(frozen=True)
class BackendSettings:
    tsmaster: TsmasterSettings
    usb2xxx: Usb2xxxSettings

    def summary_lines(self) -> list[str]:
        return self.tsmaster.summary_lines() + self.usb2xxx.summary_lines()


def default_backend_settings() -> BackendSettings:
    return BackendSettings(tsmaster=TsmasterSettings(), usb2xxx=Usb2xxxSettings())
