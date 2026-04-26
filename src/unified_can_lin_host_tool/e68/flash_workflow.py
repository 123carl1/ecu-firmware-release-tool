from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep

from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.crc32 import E68_CRC32_INIT, e68_crc32_update
from unified_can_lin_host_tool.e68.seedkey import calc_e68_fbl_key, calc_e68_level1_key
from unified_can_lin_host_tool.firmware.image import FirmwareImage, align_up, split_transfer_chunks
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


@dataclass(frozen=True)
class FlashResult:
    success: bool


class FlashWorkflow:
    def __init__(self, profile: ToolProfile, transport: LinDiagTransport, session: BusSession) -> None:
        self._profile = profile
        self._transport = transport
        self._session = session

    def run(self, *, flash_driver: FirmwareImage, app: FirmwareImage) -> FlashResult:
        if not self._session.enter_diag_exclusive("flash"):
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN channel is busy")

        try:
            self._run_app_preprogramming()
            self._run_boot_programming(flash_driver=flash_driver, app=app)
            return FlashResult(success=True)
        finally:
            self._session.release_diag_exclusive("flash")

    def _run_app_preprogramming(self) -> None:
        self._transport.request(bytes.fromhex("10 01"), expect_prefix=bytes.fromhex("50 01"))
        self._transport.request(bytes.fromhex("10 03"), expect_prefix=bytes.fromhex("50 03"))
        self._security_access(
            seed_sub_function=0x01,
            key_sub_function=0x02,
            key_func=calc_e68_level1_key,
        )
        self._transport.request(bytes.fromhex("31 01 02 03"), expect_prefix=bytes.fromhex("71 01 02 03 00"))
        self._transport.request(bytes.fromhex("10 02"), expect_prefix=bytes.fromhex("50 02"))

    def _run_boot_programming(self, *, flash_driver: FirmwareImage, app: FirmwareImage) -> None:
        self._wait_for_boot_programming_session()
        self._security_access(
            seed_sub_function=0x09,
            key_sub_function=0x0A,
            key_func=calc_e68_fbl_key,
        )
        self._download_image(start_address=flash_driver.start_address, data=flash_driver.data)
        self._transport.request(bytes.fromhex("31 01 02 02"), expect_prefix=bytes.fromhex("71 01 02 02 00"))
        self._erase_app(app)
        self._download_image(start_address=app.start_address, data=app.data)
        self._transport.request(bytes.fromhex("31 01 FF 01"), expect_prefix=bytes.fromhex("71 01 FF 01 00"))
        self._transport.request(bytes.fromhex("11 01"), expect_prefix=bytes.fromhex("51 01"))

    def _security_access(self, *, seed_sub_function: int, key_sub_function: int, key_func) -> None:
        seed_response = self._transport.request(
            bytes([0x27, seed_sub_function]),
            expect_prefix=bytes([0x67, seed_sub_function]),
        ).payload
        seed = seed_response[2:6]
        if len(seed) != 4:
            raise HostToolError(ErrorCategory.UDS, "security seed length mismatch")

        key = key_func(seed)
        self._transport.request(
            bytes([0x27, key_sub_function]) + key,
            expect_prefix=bytes([0x67, key_sub_function]),
        )

    def _wait_for_boot_programming_session(self) -> None:
        deadline = monotonic() + self._profile.uds.p2_star_ms / 1000.0
        last_error: HostToolError | None = None
        while monotonic() <= deadline:
            try:
                self._transport.request(
                    bytes.fromhex("10 02"),
                    expect_prefix=bytes.fromhex("50 02"),
                    timeout_ms=self._profile.uds.poll_timeout_ms,
                )
                return
            except HostToolError as exc:
                if exc.category != ErrorCategory.TRANSPORT:
                    raise
                last_error = exc
                sleep(self._profile.uds.poll_gap_ms / 1000.0)

        if last_error is not None:
            raise HostToolError(ErrorCategory.TRANSPORT, f"Boot programming session timeout: {last_error.message}") from last_error
        raise HostToolError(ErrorCategory.TRANSPORT, "Boot programming session timeout")

    def _download_image(self, *, start_address: int, data: bytes) -> None:
        self._request_download(start_address=start_address, size=len(data))
        crc = E68_CRC32_INIT
        block_sequence = 1
        for chunk in split_transfer_chunks(data, self._profile.uds.max_transfer_payload):
            self._transport.request(
                bytes([0x36, block_sequence]) + chunk,
                expect_prefix=bytes([0x76, block_sequence]),
            )
            crc = e68_crc32_update(crc, chunk)
            block_sequence = (block_sequence + 1) & 0xFF

        self._transport.request(
            bytes([0x37]) + crc.to_bytes(4, "big"),
            expect_prefix=bytes([0x77]) + crc.to_bytes(4, "big"),
        )

    def _request_download(self, *, start_address: int, size: int) -> None:
        payload = (
            bytes([0x34, 0x00, self._profile.uds.request_download_format])
            + start_address.to_bytes(4, "big")
            + size.to_bytes(4, "big")
        )
        self._transport.request(payload, expect_prefix=bytes.fromhex("74 20 00 06"))

    def _erase_app(self, app: FirmwareImage) -> None:
        erase_length = align_up(app.size, self._profile.memory.page_size)
        payload = (
            bytes.fromhex("31 01 FF 00")
            + app.start_address.to_bytes(4, "big")
            + erase_length.to_bytes(4, "big")
        )
        self._transport.request(
            payload,
            expect_prefix=bytes.fromhex("71 01 FF 00"),
            allow_response_pending=True,
            timeout_ms=self._profile.uds.p2_star_ms,
        )
