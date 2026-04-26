# M2A Hardware Integration Prep Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在没有真实同星/图莫斯硬件的情况下，完成 M2 真实硬件接入前置层，让后续硬件到位后只需要补适配器实测和参数校准。

**Architecture:** 保持 M1 的 PySide6 UI 和 Fake 后端闭环，把后端契约、硬件映射配置、错误分类、取消令牌和关闭确认抽成真实后端可复用的公共层。M2A 不声明真实硬件可用，只保证真实硬件接入所需的 UI、Worker、Profile、Trace 和测试边界已经稳定。

**Tech Stack:** Python 3.11+、PySide6、unittest、现有 `FakeHostBackend`、`LinDiagTransport`、`FlashWorkflow`、`TraceLogger`、TSMaster ctypes 适配器占位。

---

## 成功标准

做完 M2A 只表示“硬件接入前置层完成”，不表示真实硬件已经验证。

M2A 完成必须满足：

1. 无真实硬件时，全量 unittest 通过。
2. `QT_QPA_PLATFORM=offscreen` 下 UI smoke 稳定通过。
3. Fake 扫描、连接、UDS、E68 刷写、Trace Log 能继续跑通。
4. UI 后端选择和后端契约不再写死为 `FakeHostBackend` / `FakeHostSession`。
5. TSMaster 和 USB2XXX 的参数模型存在，UI 能展示当前配置来源和默认值。
6. 真实后端缺 DLL、缺设备、映射参数错误时，错误能按 `DEVICE` / `PROFILE` / `TRANSPORT` 分类展示，不崩溃。
7. UDS 和刷写 Worker 支持协作式取消；关闭窗口时如果有运行中操作，必须进入确认或取消流程。
8. `LinDiagTransport` 和 `FlashWorkflow` 能接收取消令牌，并在安全点退出。
9. 新增 M2A 验收记录，明确“未验证真实硬件”。

M2A 不做：

1. 不要求真实 TSMaster 或 USB2XXX 插入验证。
2. 不实现完整 CAN 收发 UI。
3. 不实现 Profile 图形化编辑器。
4. 不实现安装包和发布资源。
5. 不把 E68 参数硬编码进传输层或 UI 控件。

## 文件结构

新增文件：

1. `src/unified_can_lin_host_tool/backends/base.py`
   - 定义 UI 后端和会话协议。
   - 定义后端能力、后端类型、通道映射字段。
   - 不导入 PySide6。

2. `src/unified_can_lin_host_tool/backends/settings.py`
   - 定义 `TsmasterSettings`、`Usb2xxxSettings`、`BackendSettings`。
   - 提供默认配置和只读摘要。
   - 后续可扩展为从 YAML/JSON 读取。

3. `src/unified_can_lin_host_tool/core/cancel.py`
   - 定义 `CancellationToken` 和 `OperationCancelled`。
   - 基于 `threading.Event`，不依赖 Qt。

4. `tests/test_backend_contract.py`
   - 验证 Fake 后端符合公共协议。
   - 验证通道映射信息能从扫描结果传到连接请求。

5. `tests/test_backend_settings.py`
   - 验证默认 TSMaster/USB2XXX 配置。
   - 验证配置摘要不丢关键映射字段。

6. `tests/test_cancel.py`
   - 验证取消令牌。
   - 验证 `LinDiagTransport` 轮询期间可取消。
   - 验证 `FlashWorkflow` 在安全点可取消。

7. `docs/superpowers/reports/2026-04-26-m2a-hardware-integration-prep-acceptance.md`
   - M2A 执行完成后创建。

修改文件：

1. `src/unified_can_lin_host_tool/ui/models.py`
   - 扩展 `UiChannel`，增加 `mapping` 和 `capabilities`。
   - 保持现有 Fake UI 不破坏。

2. `src/unified_can_lin_host_tool/backends/fake_backend.py`
   - 实现新的后端协议。
   - Fake 会话支持 `cancel_token` 参数。
   - Fake 扫描结果补齐通道映射字段。

3. `src/unified_can_lin_host_tool/ui/workers.py`
   - Worker 依赖公共协议，不直接依赖 Fake 类型。
   - 增加 `cancel()` 方法。
   - UDS/刷写调用传入取消令牌。

4. `src/unified_can_lin_host_tool/ui/main_window.py`
   - 后端选择从 registry 获取。
   - 显示当前后端配置摘要。
   - 操作运行中禁用互斥按钮。
   - 关闭窗口时处理运行中 Worker。

5. `src/unified_can_lin_host_tool/transport/lin_diag.py`
   - `request()` 增加 `cancel_token`。
   - 发送每帧前、轮询每次响应前后检查取消。

6. `src/unified_can_lin_host_tool/e68/flash_workflow.py`
   - `run()` 增加 `cancel_token`。
   - 在 UDS 请求之间和 `$36` 块边界检查取消。

7. `docs/superpowers/reports/2026-04-26-m1-ui-alpha-acceptance.md`
   - 如执行后行为变化，只追加 M2A 关联说明，不改写 M1 验收结论。

## Chunk 1: 后端契约和配置模型

### Task 1: 写后端配置模型测试

**Files:**
- Create: `tests/test_backend_settings.py`
- Create: `src/unified_can_lin_host_tool/backends/settings.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_backend_settings.py`：

```python
import unittest

from unified_can_lin_host_tool.backends.settings import (
    BackendSettings,
    TsmasterSettings,
    Usb2xxxSettings,
    default_backend_settings,
)


class BackendSettingsTest(unittest.TestCase):
    def test_default_settings_include_required_tsmaster_mapping_fields(self):
        settings = default_backend_settings()

        self.assertIsInstance(settings.tsmaster, TsmasterSettings)
        self.assertEqual(settings.tsmaster.dll_path, "D:/software/TSMaster/bin64/TSMaster.dll")
        self.assertEqual(settings.tsmaster.app_channel, 0)
        self.assertEqual(settings.tsmaster.hw_index, 0)
        self.assertEqual(settings.tsmaster.hw_channel, 0)
        self.assertGreater(settings.tsmaster.baud_kbps, 0)

    def test_default_settings_include_required_usb2xxx_fields(self):
        settings = default_backend_settings()

        self.assertIsInstance(settings.usb2xxx, Usb2xxxSettings)
        self.assertEqual(settings.usb2xxx.dll_path, "D:/software/USB2XXX/USB2XXX.dll")
        self.assertEqual(settings.usb2xxx.device_index, 0)
        self.assertEqual(settings.usb2xxx.channel_index, 0)
        self.assertEqual(settings.usb2xxx.baudrate, 19200)

    def test_settings_summary_is_plain_text_friendly(self):
        settings = BackendSettings(
            tsmaster=TsmasterSettings(hw_name="TC1016", hw_channel=1),
            usb2xxx=Usb2xxxSettings(channel_index=2),
        )

        summary = settings.summary_lines()

        self.assertIn("TSMaster.hw_name: TC1016", summary)
        self.assertIn("TSMaster.hw_channel: 1", summary)
        self.assertIn("USB2XXX.channel_index: 2", summary)
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_backend_settings -v
```

预期：失败，提示 `unified_can_lin_host_tool.backends.settings` 不存在。

- [ ] **Step 3: 实现配置模型**

创建 `src/unified_can_lin_host_tool/backends/settings.py`：

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TsmasterSettings:
    dll_path: str = "D:/software/TSMaster/bin64/TSMaster.dll"
    app_name: str = "Codex_UnifiedHostTool"
    app_channel: int = 0
    hw_name: str = "TC1016"
    hw_subtype: int = 11
    hw_index: int = 0
    hw_channel: int = 0
    baud_kbps: float = 19.2

    def summary_lines(self) -> list[str]:
        return [
            f"TSMaster.dll_path: {self.dll_path}",
            f"TSMaster.app_name: {self.app_name}",
            f"TSMaster.app_channel: {self.app_channel}",
            f"TSMaster.hw_name: {self.hw_name}",
            f"TSMaster.hw_subtype: {self.hw_subtype}",
            f"TSMaster.hw_index: {self.hw_index}",
            f"TSMaster.hw_channel: {self.hw_channel}",
            f"TSMaster.baud_kbps: {self.baud_kbps}",
        ]


@dataclass(frozen=True)
class Usb2xxxSettings:
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
```

- [ ] **Step 4: 运行测试确认通过**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_backend_settings -v
```

预期：通过。

- [ ] **Step 5: 提交**

```powershell
git add src/unified_can_lin_host_tool/backends/settings.py tests/test_backend_settings.py
git commit -m "新增硬件后端配置模型"
```

### Task 2: 定义后端协议并适配 Fake 后端

**Files:**
- Create: `src/unified_can_lin_host_tool/backends/base.py`
- Modify: `src/unified_can_lin_host_tool/ui/models.py`
- Modify: `src/unified_can_lin_host_tool/backends/fake_backend.py`
- Create: `tests/test_backend_contract.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_backend_contract.py`：

```python
import tempfile
import unittest
from pathlib import Path

from unified_can_lin_host_tool.backends.base import HostBackend, HostSession
from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
from unified_can_lin_host_tool.profile import load_profile


class BackendContractTest(unittest.TestCase):
    def test_fake_backend_matches_host_backend_protocol(self):
        backend = FakeHostBackend()

        self.assertIsInstance(backend, HostBackend)

    def test_fake_session_matches_host_session_protocol(self):
        backend = FakeHostBackend()
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        channel = backend.scan()[0].channels[0]

        session = backend.connect(channel, profile)

        self.assertIsInstance(session, HostSession)

    def test_scan_channels_expose_mapping_fields(self):
        backend = FakeHostBackend()

        channel = backend.scan()[0].channels[0]

        self.assertEqual(channel.vendor, "TSMaster")
        self.assertEqual(channel.mapping["app_channel"], 0)
        self.assertEqual(channel.mapping["hw_channel"], 0)
        self.assertIn("lin_diag", channel.capabilities)

    def test_manual_uds_still_returns_repeatable_response(self):
        backend = FakeHostBackend()
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        session = backend.connect(backend.scan()[0].channels[0], profile)

        with tempfile.TemporaryDirectory() as tmp:
            first = session.request_uds(bytes.fromhex("10 01"), log_dir=Path(tmp))
            second = session.request_uds(bytes.fromhex("10 01"), log_dir=Path(tmp))

        self.assertEqual(first, bytes.fromhex("50 01"))
        self.assertEqual(second, bytes.fromhex("50 01"))
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_backend_contract -v
```

预期：失败，提示 `backends.base` 不存在，或 `UiChannel` 没有 `mapping`。

- [ ] **Step 3: 创建后端协议**

创建 `src/unified_can_lin_host_tool/backends/base.py`：

```python
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from unified_can_lin_host_tool.core.cancel import CancellationToken
from unified_can_lin_host_tool.profile import ToolProfile
from unified_can_lin_host_tool.ui.models import UiChannel, UiDevice, WorkerEvent

EventCallback = Callable[[WorkerEvent], None]


@runtime_checkable
class HostSession(Protocol):
    profile: ToolProfile

    def request_uds(
        self,
        payload: bytes,
        *,
        log_dir: Path | None = None,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> bytes:
        ...

    def flash_e68(
        self,
        *,
        flash_driver_path: Path,
        app_path: Path,
        log_dir: Path,
        dry_run: bool = True,
        on_event: EventCallback | None = None,
        cancel_token: CancellationToken | None = None,
    ) -> list[WorkerEvent]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class HostBackend(Protocol):
    name: str

    def scan(self) -> list[UiDevice]:
        ...

    def connect(self, channel: UiChannel, profile: ToolProfile) -> HostSession:
        ...
```

注意：这个文件引用 `CancellationToken`，因此 Task 2 执行时可以先创建最小 `src/unified_can_lin_host_tool/core/cancel.py`：

```python
from __future__ import annotations

from threading import Event


class OperationCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise OperationCancelled("operation cancelled")
```

- [ ] **Step 4: 扩展 UI 通道模型**

修改 `src/unified_can_lin_host_tool/ui/models.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from unified_can_lin_host_tool.core.events import TraceEvent


class ConnectionState(str, Enum):
    IDLE = "idle"
    SCANNED = "scanned"
    CONNECTED = "connected"
    BUSY = "busy"
    ERROR = "error"


@dataclass(frozen=True)
class UiChannel:
    vendor: str
    device_name: str
    channel_name: str
    bus: str
    channel_index: int
    mapping: dict[str, str | int | float | bool] = field(default_factory=dict)
    capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class UiDevice:
    vendor: str
    name: str
    serial: str
    channels: list[UiChannel]


@dataclass(frozen=True)
class WorkerEvent:
    kind: str
    message: str
    progress: int | None = None
    trace: TraceEvent | None = None
    timestamp: datetime = field(default_factory=datetime.now)
```

- [ ] **Step 5: 更新 Fake 后端**

修改 `src/unified_can_lin_host_tool/backends/fake_backend.py`：

1. `FakeHostBackend` 增加类属性：

```python
name = "Fake"
```

2. Fake 扫描结果通道增加映射和能力：

```python
UiChannel(
    vendor="TSMaster",
    device_name="Fake TSMaster",
    channel_name="LIN 0",
    bus="LIN",
    channel_index=0,
    mapping={
        "app_channel": 0,
        "hw_name": "FAKE-TS",
        "hw_index": 0,
        "hw_channel": 0,
    },
    capabilities=("lin_raw", "lin_diag", "e68_flash"),
)
```

USB2XXX Fake 通道映射：

```python
UiChannel(
    vendor="USB2XXX",
    device_name="Fake USB2XXX",
    channel_name="LIN 0",
    bus="LIN",
    channel_index=0,
    mapping={
        "device_index": 0,
        "channel_index": 0,
    },
    capabilities=("lin_raw", "lin_diag", "e68_flash"),
)
```

3. `FakeHostSession` 增加 `close()`：

```python
def close(self) -> None:
    pass
```

4. `request_uds()` 和 `flash_e68()` 暂时接收但不使用 `cancel_token`，Chunk 2 再补检查点：

```python
cancel_token: CancellationToken | None = None,
```

- [ ] **Step 6: 运行测试确认通过**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_backend_contract -v
```

预期：通过。

- [ ] **Step 7: 回归 M1 Fake UI 相关测试**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_fake_backend tests.test_ui_smoke -v
```

预期：通过。

- [ ] **Step 8: 提交**

```powershell
git add src/unified_can_lin_host_tool/backends/base.py src/unified_can_lin_host_tool/core/cancel.py src/unified_can_lin_host_tool/ui/models.py src/unified_can_lin_host_tool/backends/fake_backend.py tests/test_backend_contract.py
git commit -m "抽象上位机后端会话契约"
```

## Chunk 2: 协作式取消和安全退出

### Task 3: 补取消令牌单元测试

**Files:**
- Modify: `src/unified_can_lin_host_tool/core/cancel.py`
- Create: `tests/test_cancel.py`

- [ ] **Step 1: 写取消令牌测试**

创建 `tests/test_cancel.py`：

```python
import unittest

from unified_can_lin_host_tool.core.cancel import CancellationToken, OperationCancelled


class CancellationTokenTest(unittest.TestCase):
    def test_new_token_is_not_cancelled(self):
        token = CancellationToken()

        self.assertFalse(token.is_cancelled)

    def test_cancel_sets_flag(self):
        token = CancellationToken()

        token.cancel()

        self.assertTrue(token.is_cancelled)

    def test_throw_if_cancelled_raises_classified_exception(self):
        token = CancellationToken()
        token.cancel()

        with self.assertRaises(OperationCancelled):
            token.throw_if_cancelled()
```

- [ ] **Step 2: 运行测试**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_cancel -v
```

预期：通过。如果 Task 2 已创建最小实现，这一步应直接通过。

- [ ] **Step 3: 提交**

```powershell
git add src/unified_can_lin_host_tool/core/cancel.py tests/test_cancel.py
git commit -m "新增协作式取消令牌"
```

### Task 4: `LinDiagTransport` 支持轮询取消

**Files:**
- Modify: `src/unified_can_lin_host_tool/transport/lin_diag.py`
- Modify: `tests/test_cancel.py`
- Modify: `tests/test_lin_diag_transport.py`

- [ ] **Step 1: 写轮询取消测试**

追加到 `tests/test_cancel.py`：

```python
from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


class LinDiagCancellationTest(unittest.TestCase):
    def test_request_polling_can_be_cancelled(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        token = CancellationToken()

        def cancel_on_sleep(_seconds):
            token.cancel()

        transport = LinDiagTransport(
            FakeLinAdapter(),
            profile,
            sleep_func=cancel_on_sleep,
        )

        with self.assertRaises(OperationCancelled):
            transport.request(bytes.fromhex("10 01"), cancel_token=token)
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_cancel.LinDiagCancellationTest -v
```

预期：失败，提示 `request()` 不接收 `cancel_token`，或超时错误而不是取消错误。

- [ ] **Step 3: 修改传输层接口**

修改 `src/unified_can_lin_host_tool/transport/lin_diag.py`：

```python
from unified_can_lin_host_tool.core.cancel import CancellationToken
```

`request()` 签名改为：

```python
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
```

发送循环中加入：

```python
for frame in self._build_request_frames(uds_payload):
    if cancel_token is not None:
        cancel_token.throw_if_cancelled()
    self._adapter.send_lin_frame(self._profile.bus.request_id, frame)
    self._write_trace("TX", self._profile.bus.request_id, frame)
    self._sleep(self._profile.uds.frame_gap_ms / 1000.0)
```

`_poll_response()` 签名增加：

```python
cancel_token: CancellationToken | None,
```

轮询循环顶部和 sleep 后加入：

```python
if cancel_token is not None:
    cancel_token.throw_if_cancelled()
```

调用 `_poll_response()` 时传入 `cancel_token=cancel_token`。

- [ ] **Step 4: 运行取消和传输层回归测试**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_cancel tests.test_lin_diag_transport -v
```

预期：通过。

- [ ] **Step 5: 提交**

```powershell
git add src/unified_can_lin_host_tool/transport/lin_diag.py tests/test_cancel.py
git commit -m "支持LIN诊断请求协作取消"
```

### Task 5: `FlashWorkflow` 支持安全点取消

**Files:**
- Modify: `src/unified_can_lin_host_tool/e68/flash_workflow.py`
- Modify: `src/unified_can_lin_host_tool/backends/fake_backend.py`
- Modify: `tests/test_cancel.py`
- Modify: `tests/test_flash_workflow_fake.py`

- [ ] **Step 1: 阅读当前刷写流程**

运行：

```powershell
Get-Content -Path 'src\unified_can_lin_host_tool\e68\flash_workflow.py'
```

确认每个 UDS 请求封装点，并标出安全取消点：

1. App 编程会话前。
2. App 安全访问后。
3. 跳 Boot 后等待 Boot 编程会话期间。
4. FlashDriver 下载每个 `$36` 块之间。
5. App 擦除完成后。
6. App 下载每个 `$36` 块之间。
7. `$37` 校验后。

- [ ] **Step 2: 写刷写取消测试**

追加到 `tests/test_cancel.py`：

```python
from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.core.session import BusSession
from unified_can_lin_host_tool.e68.flash_workflow import FlashWorkflow
from unified_can_lin_host_tool.firmware.image import load_bin_image


class FlashWorkflowCancellationTest(unittest.TestCase):
    def test_flash_workflow_cancels_at_safe_point(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        flash_driver = load_bin_image(
            "tests/fixtures/flash_driver_18b.bin",
            start_address=profile.memory.flash_driver_ram,
            max_size=profile.memory.flash_driver_max_size,
        )
        app = load_bin_image(
            "tests/fixtures/app_20b.bin",
            start_address=profile.memory.app_start,
            max_size=profile.memory.app_size,
        )
        adapter = FakeLinAdapter.for_e68_flash_success(
            profile,
            flash_driver_data=flash_driver.data,
            app_data=app.data,
        )
        token = CancellationToken()
        request_count = 0

        class CancellingTransport(LinDiagTransport):
            def request(self, *args, **kwargs):
                nonlocal request_count
                request_count += 1
                if request_count == 4:
                    token.cancel()
                return super().request(*args, **kwargs)

        transport = CancellingTransport(adapter, profile, sleep_func=lambda _: None)
        workflow = FlashWorkflow(profile, transport, BusSession())

        with self.assertRaises(OperationCancelled):
            workflow.run(flash_driver=flash_driver, app=app, cancel_token=token)

        self.assertFalse(workflow.bus_session.is_diag_exclusive)
```

如果当前 `FlashWorkflow` 没有公开 `bus_session` 属性，测试改为保存外部 `session = BusSession()` 后断言 `session.is_diag_exclusive`。

- [ ] **Step 3: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_cancel.FlashWorkflowCancellationTest -v
```

预期：失败，提示 `run()` 不接收 `cancel_token`。

- [ ] **Step 4: 修改 `FlashWorkflow.run()`**

将 `run()` 签名改为：

```python
def run(
    self,
    *,
    flash_driver: FirmwareImage,
    app: FirmwareImage,
    cancel_token: CancellationToken | None = None,
) -> FlashResult:
```

在类内增加：

```python
def _check_cancel(self, cancel_token: CancellationToken | None) -> None:
    if cancel_token is not None:
        cancel_token.throw_if_cancelled()
```

所有 `self._transport.request(...)` 调用都传：

```python
cancel_token=cancel_token
```

每个 UDS 请求前后、每个 `$36` 块前后调用：

```python
self._check_cancel(cancel_token)
```

必须保持 `finally` 中释放 `DIAG_EXCLUSIVE`。

- [ ] **Step 5: 更新 Fake 后端传递取消令牌**

`FakeHostSession.request_uds()` 调用：

```python
return self.transport.request(payload, cancel_token=cancel_token).payload
```

`FakeHostSession.flash_e68()` 调用：

```python
result = workflow.run(flash_driver=flash_driver, app=app, cancel_token=cancel_token)
```

- [ ] **Step 6: 运行回归测试**

运行：

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_cancel tests.test_flash_workflow_fake tests.test_ui_fake_backend -v
```

预期：通过。

- [ ] **Step 7: 提交**

```powershell
git add src/unified_can_lin_host_tool/e68/flash_workflow.py src/unified_can_lin_host_tool/backends/fake_backend.py tests/test_cancel.py
git commit -m "支持E68刷写流程安全取消"
```

## Chunk 3: Worker 和 UI 接入前置层

### Task 6: Worker 改用公共协议并支持取消

**Files:**
- Modify: `src/unified_can_lin_host_tool/ui/workers.py`
- Modify: `tests/test_ui_threading.py`
- Modify: `tests/test_ui_fake_backend.py`

- [ ] **Step 1: 写 Worker 取消测试**

在 `tests/test_ui_fake_backend.py` 或新增 `tests/test_ui_workers.py` 中添加：

```python
import tempfile
import unittest
from pathlib import Path

from unified_can_lin_host_tool.backends.fake_backend import FakeHostBackend
from unified_can_lin_host_tool.core.cancel import OperationCancelled
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.ui.workers import UdsWorker


class WorkerCancellationTest(unittest.TestCase):
    def test_uds_worker_cancel_sets_token_before_run(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        backend = FakeHostBackend()
        session = backend.connect(backend.scan()[0].channels[0], profile)

        with tempfile.TemporaryDirectory() as tmp:
            worker = UdsWorker(session, bytes.fromhex("10 01"), log_dir=Path(tmp))
            worker.cancel()

            with self.assertRaises(OperationCancelled):
                worker._run_for_test()
```

说明：为了避免直接启动 Qt 线程，`UdsWorker` 可以新增一个私有 `_run_for_test()`，内部复用 `run()` 的核心逻辑。若不想增加测试钩子，则用现有 Qt 测试方式接收 `failed` 信号并断言消息包含取消。

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_fake_backend -v
```

预期：失败，提示 `cancel()` 或 `_run_for_test()` 不存在。

- [ ] **Step 3: 修改 Worker 类型依赖和取消令牌**

修改 `src/unified_can_lin_host_tool/ui/workers.py`：

```python
from unified_can_lin_host_tool.backends.base import HostBackend, HostSession
from unified_can_lin_host_tool.core.cancel import CancellationToken
```

构造函数类型改为公共协议：

```python
def __init__(self, backend: HostBackend) -> None:
```

```python
def __init__(self, session: HostSession, payload: bytes, *, log_dir: Path = Path("logs")) -> None:
```

在 `UdsWorker` 和 `FlashWorker` 中增加：

```python
self._cancel_token = CancellationToken()

def cancel(self) -> None:
    self._cancel_token.cancel()
```

调用后端时传：

```python
cancel_token=self._cancel_token
```

如果采用 `_run_for_test()`：

```python
def _run_for_test(self) -> bytes:
    return self._session.request_uds(
        self._payload,
        log_dir=self._log_dir,
        on_event=self.event.emit,
        cancel_token=self._cancel_token,
    )
```

`run()` 内调用 `_run_for_test()` 并 emit 结果。

- [ ] **Step 4: 运行 Worker/UI 回归**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_threading tests.test_ui_fake_backend tests.test_ui_smoke -v
```

预期：通过。

- [ ] **Step 5: 提交**

```powershell
git add src/unified_can_lin_host_tool/ui/workers.py tests/test_ui_fake_backend.py tests/test_ui_threading.py
git commit -m "让UI Worker使用公共后端契约"
```

### Task 7: UI 增加后端 registry 和配置摘要

**Files:**
- Modify: `src/unified_can_lin_host_tool/ui/main_window.py`
- Modify: `tests/test_ui_smoke.py`
- Modify: `tests/test_ui_fake_backend.py`

- [ ] **Step 1: 写 UI 配置摘要测试**

在 `tests/test_ui_smoke.py` 中添加：

```python
def test_main_window_shows_backend_mapping_summary(self):
    window = MainWindow()

    summary_text = window.config_summary_text.toPlainText()

    self.assertIn("TSMaster.hw_channel", summary_text)
    self.assertIn("USB2XXX.channel_index", summary_text)
    window.close()
```

如果当前测试文件使用 `unittest.TestCase`，按类方法形式添加。

- [ ] **Step 2: 写真实后端缺 DLL 错误展示测试**

先不调用真实硬件。通过注入一个会抛 `HostToolError(ErrorCategory.DEVICE, ...)` 的 backend，验证 UI 展示分类错误：

```python
from unified_can_lin_host_tool.core.errors import ErrorCategory, HostToolError


class MissingDllBackend:
    name = "TSMaster"

    def scan(self):
        raise HostToolError(ErrorCategory.DEVICE, "load TSMaster DLL failed: missing.dll")


def test_main_window_shows_classified_backend_error(self):
    window = MainWindow(backends={"TSMaster": MissingDllBackend()})
    window.backend_combo.setCurrentText("TSMaster")

    window._on_scan_clicked()
    wait_for_worker_threads(window)

    self.assertIn("device:", window.trace_log.toPlainText())
    self.assertIn("missing.dll", window.trace_log.toPlainText())
    window.close()
```

测试中如果没有 `wait_for_worker_threads()`，可参考现有 UI 测试里的等待方式，或新增一个测试 helper。

- [ ] **Step 3: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_smoke tests.test_ui_fake_backend -v
```

预期：失败，提示 `config_summary_text` 或 `MainWindow(backends=...)` 不存在。

- [ ] **Step 4: 修改 MainWindow 构造函数**

`MainWindow.__init__()` 改为：

```python
def __init__(
    self,
    *,
    backends: dict[str, HostBackend] | None = None,
    backend_settings: BackendSettings | None = None,
) -> None:
```

初始化：

```python
self._backend_settings = backend_settings or default_backend_settings()
self._backends = backends or {"Fake": FakeHostBackend()}
self._backend = self._backends["Fake"]
```

后端下拉框内容来自 registry：

```python
self.backend_combo.addItems(list(self._backends.keys()))
self.backend_combo.currentTextChanged.connect(self._on_backend_changed)
```

`_on_backend_changed()`：

```python
def _on_backend_changed(self, name: str) -> None:
    self._backend = self._backends[name]
    self._session = None
    self._set_connected(False)
    self.status_label.setText(f"后端: {name}")
```

- [ ] **Step 5: 修改配置摘要页**

将原来的 `QFormLayout` 改为保留 Profile 字段，同时增加只读文本区：

```python
self.config_summary_text = QPlainTextEdit()
self.config_summary_text.setReadOnly(True)
self.config_summary_text.setPlainText("\n".join(self._backend_settings.summary_lines()))
layout.addRow("backend", self.config_summary_text)
```

- [ ] **Step 6: 错误展示保留分类**

`_show_error()` 如果收到 `HostToolError` 的字符串已经包含 `device:`，直接显示。Worker 当前传 `str(exc)`，先不改事件模型。

如果需要更强契约，Worker 可以把异常对象分类成：

```python
message = str(exc)
self.failed.emit(message)
```

M2A 不要求 UI 对不同错误加颜色，只要求分类文字存在。

- [ ] **Step 7: 运行 UI 测试**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_smoke tests.test_ui_fake_backend -v
```

预期：通过。

- [ ] **Step 8: 提交**

```powershell
git add src/unified_can_lin_host_tool/ui/main_window.py tests/test_ui_smoke.py tests/test_ui_fake_backend.py
git commit -m "增加后端配置摘要和后端注册表"
```

### Task 8: 关闭窗口时处理运行中操作

**Files:**
- Modify: `src/unified_can_lin_host_tool/ui/main_window.py`
- Modify: `tests/test_ui_smoke.py`
- Modify: `tests/test_ui_threading.py`

- [ ] **Step 1: 写关闭行为测试**

添加测试：

```python
def test_main_window_close_requests_cancel_for_active_workers(self):
    window = MainWindow()
    worker = FakeCancelableWorker()
    window._active_workers.append(worker)

    window._stop_active_threads()

    self.assertTrue(worker.cancel_called)
    window.close()
```

测试 helper：

```python
class FakeCancelableWorker:
    cancel_called = False

    def cancel(self):
        self.cancel_called = True
```

如果 `_active_workers` 只允许 `QObject`，则用继承 `QObject` 的 fake worker。

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_smoke tests.test_ui_threading -v
```

预期：失败，当前 `_stop_active_threads()` 只 quit thread，不调用 worker.cancel()。

- [ ] **Step 3: 修改 `_stop_active_threads()`**

先取消 worker，再停止线程：

```python
for worker in list(self._active_workers):
    cancel = getattr(worker, "cancel", None)
    if callable(cancel):
        cancel()
```

然后保留现有：

```python
thread.requestInterruption()
thread.quit()
thread.wait(2000)
```

注意：这仍不能强杀真实硬件 DLL 阻塞调用。验收记录必须写清：M2A 是协作式取消，真实硬件阻塞要 M2B 台架验证。

- [ ] **Step 4: 运行 UI 线程测试**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest tests.test_ui_threading tests.test_ui_smoke -v
```

预期：通过。

- [ ] **Step 5: 提交**

```powershell
git add src/unified_can_lin_host_tool/ui/main_window.py tests/test_ui_smoke.py tests/test_ui_threading.py
git commit -m "关闭窗口时请求取消后台任务"
```

## Chunk 4: 验收和收口

### Task 9: 全量验证

**Files:**
- No code change.

- [ ] **Step 1: 全量测试**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest discover -s tests -v
```

预期：

```text
OK
```

- [ ] **Step 2: UI smoke**

运行：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unified_can_lin_host_tool.cli.ui --smoke
```

预期：

```text
UI SMOKE OK
```

- [ ] **Step 3: 空白检查**

运行：

```powershell
git diff --check
```

预期：无输出。

- [ ] **Step 4: 查看工作区**

运行：

```powershell
git status --short --branch
```

预期：只显示当前分支，无未提交内容。

### Task 10: 写 M2A 验收记录

**Files:**
- Create: `docs/superpowers/reports/2026-04-26-m2a-hardware-integration-prep-acceptance.md`

- [ ] **Step 1: 创建验收记录**

创建文件：

```markdown
# M2A 硬件接入前置层验收记录

## 范围

本记录用于收口 M2A。当前验收对象是 `feature/m0-e68-lin-flash-cli` 分支上的硬件接入前置层。

M2A 不包含真实同星 TSMaster 或图莫斯 USB2XXX 硬件验证。

## 已验收能力

1. 后端契约不再写死 Fake 类型。
2. TSMaster/USB2XXX 默认映射参数有明确配置模型。
3. UI 可以显示后端配置摘要。
4. Fake 扫描、连接、UDS、刷写闭环保持可用。
5. UDS Worker 和 Flash Worker 支持协作式取消。
6. `LinDiagTransport` 支持取消令牌。
7. `FlashWorkflow` 在安全点支持取消，并释放 `DIAG_EXCLUSIVE`。
8. 关闭窗口时会先请求后台 Worker 取消，再进行线程收尾。
9. 缺 DLL/缺设备类错误能按分类显示，不崩溃。

## 自动化验证

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest discover -s tests -v
```

结果：

```text
OK
```

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unified_can_lin_host_tool.cli.ui --smoke
```

结果：

```text
UI SMOKE OK
```

```powershell
git diff --check
```

结果：

```text
通过
```

## 未覆盖项

1. 未验证真实 TSMaster USB 枚举。
2. 未验证真实 USB2XXX USB 枚举。
3. 未验证真实 LIN 收发、checksum、`0x3C/0x3D` 调度。
4. 未验证真实 ECU UDS 和刷写。
5. 未验证真实 DLL 阻塞时取消是否能及时返回。

## M2B 优先项

1. 插入真实 TSMaster，验证 DLL 加载、设备枚举和 LIN 通道映射。
2. 插入真实 USB2XXX，验证 DLL 加载、设备枚举和 LIN 通道映射。
3. 使用真实 LIN 从站或 ECU 验证 `LinDiagTransport` 的逐帧 TX/RX Trace。
4. 记录真实硬件错误码和 HostToolError 分类映射。
5. 台架验证刷写取消、掉电、CRC 失败和 Boot 停留恢复策略。
```

- [ ] **Step 2: 提交验收记录**

```powershell
git add docs/superpowers/reports/2026-04-26-m2a-hardware-integration-prep-acceptance.md
git commit -m "新增M2A硬件接入前置层验收记录"
```

### Task 11: 最终收口检查

**Files:**
- No code change.

- [ ] **Step 1: 查看提交历史**

运行：

```powershell
git log --oneline -8
```

预期：能看到 M2A 相关中文提交。

- [ ] **Step 2: 确认工作区干净**

运行：

```powershell
git status --short --branch
```

预期：

```text
## feature/m0-e68-lin-flash-cli
```

- [ ] **Step 3: 向用户报告**

报告必须包含：

1. M2A 做了什么。
2. 哪些命令已验证通过。
3. 明确说明没有真实硬件验证。
4. 下一步 M2B 需要插入真实工具验证哪些点。

## 执行注意事项

1. 不自动安装任何依赖。
2. 不要把真实硬件不可验证的结论写成“已通过”。
3. 不要让 UI 线程直接调用 DLL 或执行刷写流程。
4. 不要把 E68 的 NAD、`0x3C`、`0x3D`、时序参数硬编码进传输层。
5. 保持 `FakeHostBackend` 可用，它是 M2A 的无硬件回归基线。
6. 每个 Chunk 完成后都要运行对应测试并提交。
7. 出现测试失败时先定位根因，不要为了通过测试弱化断言。
