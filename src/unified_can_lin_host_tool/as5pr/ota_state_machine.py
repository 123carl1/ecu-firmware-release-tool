"""AS5PR CAN OTA 的自动入口识别、编程和安全取消状态机。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import monotonic, sleep
from typing import Any, Callable

from unified_can_lin_host_tool.as5pr.crc32 import AS5PR_CRC32_INIT, as5pr_crc32_update
from unified_can_lin_host_tool.as5pr.seedkey import calc_as5pr_fbl_key, calc_as5pr_level1_key
from unified_can_lin_host_tool.core.cancel import CancellationToken, OperationCancelled
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError
from unified_can_lin_host_tool.firmware.image import align_up, split_transfer_chunks
from unified_can_lin_host_tool.release.ecu_identity import EcuRole, IdentityProbeStatus, probe_identity
from unified_can_lin_host_tool.release.package import ResourceKind, VerifiedReleasePackage
from unified_can_lin_host_tool.release.project_config import ProjectCode, compute_config_digest, get_project_config
from unified_can_lin_host_tool.release.runtime_ota import RuntimeOtaPackage, validate_runtime_ota_package


class OtaResultStatus(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_UNVERIFIED = "COMPLETED_UNVERIFIED"
    CANCELLED_SAFE = "CANCELLED_SAFE"
    ECU_IN_BOOT = "ECU_IN_BOOT"
    FAILED_UNKNOWN = "FAILED_UNKNOWN"
    PACKAGE_REJECTED = "PACKAGE_REJECTED"
    IDENTITY_REJECTED = "IDENTITY_REJECTED"


@dataclass(frozen=True)
class OtaResult:
    status: OtaResultStatus
    release_set_id: str
    message: str = ""


@dataclass(frozen=True)
class OtaProgress:
    percent: int
    stage: str
    message: str
    current: int | None = None
    total: int | None = None


class As5prOtaStateMachine:
    def __init__(self, transport: Any, progress: Callable[[OtaProgress], None] | None = None,
                 sleep_func: Callable[[float], None] = sleep) -> None:
        self._transport = transport
        self._progress = progress
        self._config = get_project_config(ProjectCode.AS5PR)
        self._destructive_started = False
        self._sleep = sleep_func

    def run(
        self,
        package: VerifiedReleasePackage | RuntimeOtaPackage,
        cancel_token: CancellationToken | None = None,
    ) -> OtaResult:
        self._destructive_started = False
        if not self._package_matches(package):
            return OtaResult(OtaResultStatus.PACKAGE_REJECTED, package.release_set_id,
                             "发布资源与 AS5PR 内置项目配置不一致")
        try:
            self._check_cancel(cancel_token)
            self._emit(5, "识别 ECU", "读取 AS5PR ECU 身份")
            identity = probe_identity(self._transport, self._config)
            if identity.status is IdentityProbeStatus.APP_CONFIRMED:
                self._enter_boot_from_app(cancel_token)
                boot_identity = probe_identity(self._transport, self._config)
                if boot_identity.status is not IdentityProbeStatus.BOOT_CONFIRMED:
                    return OtaResult(OtaResultStatus.IDENTITY_REJECTED, package.release_set_id,
                                     "App 交接后未确认 AS5PR Boot 身份")
            elif identity.status is not IdentityProbeStatus.BOOT_CONFIRMED:
                return OtaResult(OtaResultStatus.IDENTITY_REJECTED, package.release_set_id,
                                 f"在线 ECU 身份未确认: {identity.status.value}")

            self._program(package, cancel_token)
            result = self._verify_after_reset(package, cancel_token)
            if result.status is OtaResultStatus.COMPLETED:
                self._emit(100, "完成", "OTA 完成，App 通信验证通过")
            return result
        except OperationCancelled:
            status = OtaResultStatus.ECU_IN_BOOT if self._destructive_started else OtaResultStatus.CANCELLED_SAFE
            return OtaResult(status, package.release_set_id, "取消已在协议安全点生效")
        except Exception as exc:  # 状态机边界必须把异常映射为稳定结果。
            status = OtaResultStatus.FAILED_UNKNOWN if self._destructive_started else OtaResultStatus.CANCELLED_SAFE
            return OtaResult(status, package.release_set_id, str(exc))

    def _package_matches(self, package: VerifiedReleasePackage | RuntimeOtaPackage) -> bool:
        cfg = self._config
        if isinstance(package, RuntimeOtaPackage):
            try:
                validate_runtime_ota_package(package)
            except (TypeError, ValueError):
                return False
            return True
        if (package.project is not ProjectCode.AS5PR or package.project_code != cfg.project_code
                or package.config_version != cfg.config_version
                or package.config_digest != compute_config_digest(cfg)):
            return False
        if tuple(item.kind for item in package.resources) != tuple(ResourceKind):
            return False
        return True

    def _enter_boot_from_app(self, token: CancellationToken | None) -> None:
        self._emit(10, "进入 Boot", "执行 App 预编程与 Boot 交接")
        self._request(bytes.fromhex("10 01"), bytes.fromhex("50 01"), token)
        self._request(bytes.fromhex("10 03"), bytes.fromhex("50 03"), token)
        self._security(0x01, 0x02, calc_as5pr_level1_key, token)
        self._request(bytes.fromhex("31 01 02 03"), bytes.fromhex("71 01 02 03 00"), token)
        self._request(bytes.fromhex("10 02"), bytes.fromhex("50 02"), token,
                      timeout_ms=self._p2_star_timeout())

    def _program(self, package: VerifiedReleasePackage, token: CancellationToken | None) -> None:
        resources = {item.kind: item for item in package.resources}
        driver = resources[ResourceKind.FLASH_DRIVER]
        app = resources[ResourceKind.APP]
        self._emit(25, "Boot 解锁", "执行 Boot FBL 安全访问")
        try:
            self._security(0x09, 0x0A, calc_as5pr_fbl_key, token)
        except HostToolError as exc:
            if exc.category is not ErrorCategory.UDS or "NRC 0x7F" not in exc.message:
                raise
            self._request(bytes.fromhex("10 02"), bytes.fromhex("50 02"), token,
                          timeout_ms=self._p2_star_timeout())
            identity = probe_identity(self._transport, self._config)
            if identity.status is not IdentityProbeStatus.BOOT_CONFIRMED:
                raise ValueError("切换编程会话后 Boot 身份未确认")
            self._security(0x09, 0x0A, calc_as5pr_fbl_key, token)
        self._download(driver.load_address, driver.content, token,
                       label="下载 FlashDriver", progress_start=30, progress_end=42)
        self._emit(45, "运行 FlashDriver", "启动 SRAM FlashDriver")
        self._request(bytes.fromhex("31 01 02 02"), bytes.fromhex("71 01 02 02 00"), token)
        erase_size = align_up(len(app.content), self._config.memory.page_size)
        erase = (bytes.fromhex("31 01 FF 00") + app.load_address.to_bytes(4, "big")
                 + erase_size.to_bytes(4, "big"))
        self._emit(50, "擦除 App", "擦除 App Flash 区域")
        self._request(erase, bytes.fromhex("71 01 FF 00"), token,
                      timeout_ms=self._config.timing.p2_star_ms, allow_response_pending=True,
                      check_after=False, destructive=True)
        self._check_cancel(token)
        self._download(app.load_address, app.content, token,
                       label="下载 App", progress_start=55, progress_end=90)
        self._emit(94, "认证 App", "校验镜像认证块并提交 AppValid")
        self._request(bytes.fromhex("31 01 FF 01"), bytes.fromhex("71 01 FF 01 00"), token,
                      timeout_ms=self._config.timing.p2_star_ms, allow_response_pending=True)
        self._emit(96, "复位 ECU", "请求 ECU 复位并启动新 App")
        self._request(bytes.fromhex("11 01"), bytes.fromhex("51 01"), token)

    def _verify_after_reset(self, package: VerifiedReleasePackage, token: CancellationToken | None) -> OtaResult:
        self._emit(98, "验证 App", "复位后等待 App 身份与通信恢复")
        deadline = monotonic() + self._config.timing.p2_star_ms / 1000.0
        last_status = IdentityProbeStatus.NO_RESPONSE
        while monotonic() <= deadline:
            self._check_cancel(token)
            identity = probe_identity(self._transport, self._config)
            last_status = identity.status
            if identity.status is IdentityProbeStatus.APP_CONFIRMED:
                did = self._config.communication_check.version_did
                self._request(b"\x22" + did.to_bytes(2, "big"), b"\x62" + did.to_bytes(2, "big"), token)
                return OtaResult(OtaResultStatus.COMPLETED, package.release_set_id)
            if identity.status in (IdentityProbeStatus.WRONG_TARGET, IdentityProbeStatus.AMBIGUOUS):
                break
            self._sleep(self._config.timing.poll_gap_ms / 1000.0)
        return OtaResult(OtaResultStatus.COMPLETED_UNVERIFIED, package.release_set_id,
                         f"复位后未确认 App 身份: {last_status.value}")

    def _security(self, seed_sub: int, key_sub: int, algorithm: Callable[[bytes], bytes],
                  token: CancellationToken | None) -> None:
        seed = self._request(bytes([0x27, seed_sub]), bytes([0x67, seed_sub]), token,
                             timeout_ms=self._p2_star_timeout()).payload[2:6]
        if len(seed) != 4:
            raise ValueError("安全访问 seed 长度错误")
        self._request(bytes([0x27, key_sub]) + algorithm(seed), bytes([0x67, key_sub]), token,
                      timeout_ms=self._p2_star_timeout())

    def _download(self, address: int, data: bytes, token: CancellationToken | None, *,
                  label: str, progress_start: int, progress_end: int) -> None:
        cfg = self._config
        request = (bytes([0x34, 0, cfg.timing.request_download_format])
                   + address.to_bytes(4, "big") + len(data).to_bytes(4, "big"))
        response = self._request(request, b"\x74", token).payload
        self._validate_request_download_response(response)
        crc = AS5PR_CRC32_INIT
        sequence = 1
        chunks = list(split_transfer_chunks(data, cfg.timing.max_transfer_payload))
        total = len(chunks)
        update_step = max(1, (total + 99) // 100)
        for index, chunk in enumerate(chunks, start=1):
            self._request(bytes([0x36, sequence]) + chunk, bytes([0x76, sequence]), token)
            crc = as5pr_crc32_update(crc, chunk)
            if index == 1 or index == total or index % update_step == 0:
                percent = progress_start + int((progress_end - progress_start) * index / max(1, total))
                self._emit(percent, label, f"block {index}/{total}", current=index, total=total)
            sequence = (sequence + 1) & 0xFF
            if index < total and cfg.timing.poll_gap_ms > 0:
                # ECU 已确认本块后留出固定调度间隔，避免长镜像连续多帧压垮接收 FIFO。
                self._sleep(cfg.timing.poll_gap_ms / 1000.0)
        self._request(b"\x37" + crc.to_bytes(4, "big"), b"\x77" + crc.to_bytes(4, "big"), token)

    def _validate_request_download_response(self, response) -> None:
        payload = response
        if len(payload) < 3 or payload[0] != 0x74:
            raise ValueError("RequestDownload 响应长度或服务号错误")
        byte_count = (payload[1] >> 4) & 0x0F
        if (payload[1] & 0x0F) != 0 or byte_count == 0 or byte_count > 4:
            raise ValueError("RequestDownload 长度格式错误")
        if len(payload) != 2 + byte_count:
            raise ValueError("RequestDownload 响应长度与长度格式不一致")
        max_block_length = int.from_bytes(payload[2:], "big")
        if max_block_length < self._config.timing.max_transfer_payload + 2:
            raise ValueError("ECU 声明的下载块长度不足")

    def _request(self, payload: bytes, prefix: bytes, token: CancellationToken | None,
                 *, timeout_ms: int | None = None, allow_response_pending: bool = False,
                 check_after: bool = True, destructive: bool = False):
        self._check_cancel(token)
        if destructive:
            # 从请求交给传输层开始，ECU 可能已经擦除；响应超时也不得再声明安全。
            self._destructive_started = True
        response = self._transport.request(
            payload, expect_prefix=prefix, timeout_ms=timeout_ms,
            allow_response_pending=allow_response_pending, ignore_invalid_responses=True,
            cancel_token=token,
        )
        if check_after:
            self._check_cancel(token)
        return response

    @staticmethod
    def _check_cancel(token: CancellationToken | None) -> None:
        if token is not None:
            token.throw_if_cancelled()

    def _p2_star_timeout(self) -> int:
        return self._config.timing.p2_star_ms + max(1000, self._config.timing.poll_timeout_ms)

    def _emit(self, percent: int, stage: str, message: str, *,
              current: int | None = None, total: int | None = None) -> None:
        if self._progress is None:
            return
        try:
            self._progress(OtaProgress(max(0, min(100, percent)), stage, message, current, total))
        except Exception:
            # 进度是观测通道，任何 UI/管道故障都不得打断已经开始的诊断编程链。
            self._progress = None
