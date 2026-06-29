from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep

from unified_can_lin_host_tool.core.cancel import CancellationToken
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


@dataclass(frozen=True)
class FlashProgress:
    percent: int
    stage: str
    message: str
    current: int | None = None
    total: int | None = None


ProgressCallback = Callable[[FlashProgress], None]


class FlashWorkflow:
    def __init__(
        self,
        profile: ToolProfile,
        transport: LinDiagTransport,
        session: BusSession,
        sleep_func=sleep,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._profile = profile
        self._transport = transport
        self._session = session
        self._sleep = sleep_func
        self._progress_callback = progress_callback

    def run(
        self,
        *,
        flash_driver: FirmwareImage,
        app: FirmwareImage,
        start_in_bootloader: bool = False,
        cancel_token: CancellationToken | None = None,
    ) -> FlashResult:
        if not self._session.enter_diag_exclusive("flash"):
            raise HostToolError(ErrorCategory.TRANSPORT, "LIN channel is busy")

        try:
            self._check_cancel(cancel_token)
            self._emit_progress(5, "准备刷写", "开始 E68 OTA 流程")
            if start_in_bootloader:
                self._emit_progress(8, "Boot 起步", "从 Bootloader 开始，执行完整预编程流程")
            self._run_preprogramming(cancel_token)
            self._run_boot_programming(
                flash_driver=flash_driver,
                app=app,
                cancel_token=cancel_token,
            )
            self._emit_progress(100, "完成", "FLASH SUCCESS")
            return FlashResult(success=True)
        finally:
            self._session.release_diag_exclusive("flash")

    def _run_preprogramming(self, cancel_token: CancellationToken | None) -> None:
        preprogramming_timeout_ms = self._p2_star_transport_timeout_ms()

        self._emit_progress(10, "预编程", "进入默认会话")
        self._request(
            bytes.fromhex("10 01"),
            expect_prefix=bytes.fromhex("50 01"),
            timeout_ms=preprogramming_timeout_ms,
            cancel_token=cancel_token,
        )
        self._emit_progress(14, "预编程", "进入扩展会话")
        self._request(
            bytes.fromhex("10 03"),
            expect_prefix=bytes.fromhex("50 03"),
            timeout_ms=preprogramming_timeout_ms,
            cancel_token=cancel_token,
        )
        self._emit_progress(18, "预编程", "Level1 安全访问")
        self._security_access(
            seed_sub_function=0x01,
            key_sub_function=0x02,
            key_func=calc_e68_level1_key,
            timeout_ms=preprogramming_timeout_ms,
            cancel_token=cancel_token,
        )
        self._emit_progress(24, "预编程", "执行刷写条件检查")
        self._request(
            bytes.fromhex("31 01 02 03"),
            expect_prefix=bytes.fromhex("71 01 02 03 00"),
            timeout_ms=preprogramming_timeout_ms,
            cancel_token=cancel_token,
        )
        self._emit_progress(28, "编程会话", "进入编程会话")
        self._request(
            bytes.fromhex("10 02"),
            expect_prefix=bytes.fromhex("50 02"),
            timeout_ms=preprogramming_timeout_ms,
            cancel_token=cancel_token,
        )

    def _run_boot_programming(
        self,
        *,
        flash_driver: FirmwareImage,
        app: FirmwareImage,
        cancel_token: CancellationToken | None,
    ) -> None:
        self._emit_progress(38, "Boot 解锁", "Boot FBL 安全访问")
        self._security_access(
            seed_sub_function=0x09,
            key_sub_function=0x0A,
            key_func=calc_e68_fbl_key,
            timeout_ms=self._p2_star_transport_timeout_ms(),
            cancel_token=cancel_token,
        )
        self._download_image(
            label="下载 FlashDriver",
            progress_start=42,
            progress_end=52,
            start_address=flash_driver.start_address,
            data=flash_driver.data,
            cancel_token=cancel_token,
        )
        self._emit_progress(56, "运行 FlashDriver", "启动 RAM FlashDriver")
        self._request(bytes.fromhex("31 01 02 02"), expect_prefix=bytes.fromhex("71 01 02 02 00"), cancel_token=cancel_token)
        self._erase_app(app, cancel_token)
        self._download_image(
            label="下载 App",
            progress_start=66,
            progress_end=90,
            start_address=app.start_address,
            data=app.data,
            cancel_token=cancel_token,
        )
        self._emit_progress(94, "校验 App", "请求 App 完整性校验")
        self._request(
            bytes.fromhex("31 01 FF 01"),
            expect_prefix=bytes.fromhex("71 01 FF 01 00"),
            allow_response_pending=True,
            timeout_ms=self._profile.uds.p2_star_ms,
            cancel_token=cancel_token,
        )
        self._emit_progress(96, "复位", "请求 ECU Reset")
        self._request(bytes.fromhex("11 01"), expect_prefix=bytes.fromhex("51 01"), cancel_token=cancel_token)
        self._wait_for_app_did_after_reset(cancel_token)

    def _wait_for_app_did_after_reset(self, cancel_token: CancellationToken | None) -> None:
        self._emit_progress(98, "等待 App", "等待 App DID 恢复通信")
        deadline = monotonic() + self._profile.uds.p2_star_ms / 1000.0
        last_error: HostToolError | None = None

        while monotonic() <= deadline:
            self._check_cancel(cancel_token)
            try:
                self._request(
                    bytes.fromhex("22 30 00"),
                    expect_prefix=bytes.fromhex("62 30 00"),
                    timeout_ms=self._profile.uds.poll_timeout_ms,
                    cancel_token=cancel_token,
                )
                return
            except HostToolError as exc:
                if exc.category not in (ErrorCategory.TRANSPORT, ErrorCategory.UDS):
                    raise
                last_error = exc
                self._sleep(self._profile.uds.poll_gap_ms / 1000.0)

        if last_error is not None:
            raise HostToolError(ErrorCategory.TRANSPORT, f"App DID after reset timeout: {last_error.message}") from last_error
        raise HostToolError(ErrorCategory.TRANSPORT, "App DID after reset timeout")

    def _security_access(
        self,
        *,
        seed_sub_function: int,
        key_sub_function: int,
        key_func,
        timeout_ms: int | None = None,
        cancel_token: CancellationToken | None,
    ) -> None:
        seed_response = self._request(
            bytes([0x27, seed_sub_function]),
            expect_prefix=bytes([0x67, seed_sub_function]),
            timeout_ms=timeout_ms,
            cancel_token=cancel_token,
        ).payload
        seed = seed_response[2:6]
        if len(seed) != 4:
            raise HostToolError(ErrorCategory.UDS, "security seed length mismatch")

        key = key_func(seed)
        self._request(
            bytes([0x27, key_sub_function]) + key,
            expect_prefix=bytes([0x67, key_sub_function]),
            timeout_ms=timeout_ms,
            cancel_token=cancel_token,
        )

    def _download_image(
        self,
        *,
        label: str,
        progress_start: int,
        progress_end: int,
        start_address: int,
        data: bytes,
        cancel_token: CancellationToken | None,
    ) -> None:
        self._emit_progress(progress_start, label, f"{label}: RequestDownload")
        self._request_download(start_address=start_address, size=len(data), cancel_token=cancel_token)
        crc = E68_CRC32_INIT
        block_sequence = 1
        chunks = list(split_transfer_chunks(data, self._profile.uds.max_transfer_payload))
        total_blocks = len(chunks)
        update_step = max(1, total_blocks // 200)
        transferred = 0
        for block_index, chunk in enumerate(chunks, start=1):
            self._request(
                bytes([0x36, block_sequence]) + chunk,
                expect_prefix=bytes([0x76, block_sequence]),
                cancel_token=cancel_token,
            )
            crc = e68_crc32_update(crc, chunk)
            transferred += len(chunk)
            if block_index == 1 or block_index == total_blocks or block_index % update_step == 0:
                percent = _scale_progress(progress_start, progress_end, block_index, total_blocks)
                self._emit_progress(
                    percent,
                    label,
                    f"{label}: block {block_index}/{total_blocks}, {transferred}/{len(data)} bytes",
                    current=block_index,
                    total=total_blocks,
                )
            block_sequence = (block_sequence + 1) & 0xFF

        self._emit_progress(progress_end, label, f"{label}: TransferExit/CRC")
        self._request(
            bytes([0x37]) + crc.to_bytes(4, "big"),
            expect_prefix=bytes([0x77]) + crc.to_bytes(4, "big"),
            cancel_token=cancel_token,
        )

    def _request_download(self, *, start_address: int, size: int, cancel_token: CancellationToken | None) -> None:
        payload = (
            bytes([0x34, 0x00, self._profile.uds.request_download_format])
            + start_address.to_bytes(4, "big")
            + size.to_bytes(4, "big")
        )
        response = self._request(payload, expect_prefix=bytes.fromhex("74"), cancel_token=cancel_token)
        self._validate_request_download_response(response.payload)

    def _validate_request_download_response(self, payload: bytes) -> None:
        if len(payload) < 3:
            raise HostToolError(ErrorCategory.UDS, "RequestDownload response is too short")
        if payload[0] != 0x74:
            raise HostToolError(ErrorCategory.UDS, "RequestDownload response has invalid header")

        mbl_byte_count = (payload[1] >> 4) & 0x0F
        if ((payload[1] & 0x0F) != 0) or (mbl_byte_count == 0) or (mbl_byte_count > 4):
            raise HostToolError(ErrorCategory.UDS, "RequestDownload response has invalid length format")
        if len(payload) != (2 + mbl_byte_count):
            raise HostToolError(ErrorCategory.UDS, "RequestDownload response length does not match length format")

        max_number_of_block_length = int.from_bytes(payload[2 : 2 + mbl_byte_count], "big")
        required = self._profile.uds.max_transfer_payload + 2
        if max_number_of_block_length < required:
            raise HostToolError(
                ErrorCategory.UDS,
                (
                    "RequestDownload maxNumberOfBlockLength "
                    f"0x{max_number_of_block_length:04X} is smaller than required 0x{required:04X}"
                ),
            )

    def _erase_app(self, app: FirmwareImage, cancel_token: CancellationToken | None) -> None:
        self._emit_progress(62, "擦除 App", "擦除 App 区域")
        erase_length = align_up(app.size, self._profile.memory.page_size)
        payload = (
            bytes.fromhex("31 01 FF 00")
            + app.start_address.to_bytes(4, "big")
            + erase_length.to_bytes(4, "big")
        )
        self._request(
            payload,
            expect_prefix=bytes.fromhex("71 01 FF 00"),
            allow_response_pending=True,
            timeout_ms=self._profile.uds.p2_star_ms,
            cancel_token=cancel_token,
        )

    def _request(
        self,
        payload: bytes,
        *,
        expect_prefix: bytes,
        cancel_token: CancellationToken | None,
        timeout_ms: int | None = None,
        allow_response_pending: bool = False,
        ignore_invalid_responses: bool = True,
    ):
        self._check_cancel(cancel_token)
        response = self._transport.request(
            payload,
            expect_prefix=expect_prefix,
            timeout_ms=timeout_ms,
            allow_response_pending=allow_response_pending,
            ignore_invalid_responses=ignore_invalid_responses,
            cancel_token=cancel_token,
        )
        self._check_cancel(cancel_token)
        return response

    def _check_cancel(self, cancel_token: CancellationToken | None) -> None:
        if cancel_token is not None:
            cancel_token.throw_if_cancelled()

    def _p2_star_transport_timeout_ms(self) -> int:
        return self._profile.uds.p2_star_ms + max(1000, self._profile.uds.poll_timeout_ms)

    def _emit_progress(
        self,
        percent: int,
        stage: str,
        message: str,
        *,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(
            FlashProgress(
                percent=max(0, min(100, percent)),
                stage=stage,
                message=message,
                current=current,
                total=total,
            )
        )


def _scale_progress(start: int, end: int, current: int, total: int) -> int:
    if total <= 0:
        return start
    return start + int((end - start) * current / total)
