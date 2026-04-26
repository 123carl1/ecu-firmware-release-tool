# M0 E68 LIN Flash CLI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先做一个无 UI 或极简 CLI 的 M0 闭环，能加载固定 Profile，通过单一优先工具链执行 E68 LIN UDS 刷写流程，并保存完整 Trace Log。

**Architecture:** M0 只实现核心链路：Profile -> 固件镜像 -> LIN 诊断传输 -> E68 刷写状态机 -> Trace Log。通用 CAN/LIN 面板、PySide6、双厂家完整扫描先不做，但传输层不能硬编码 E68 参数，所有 NAD、请求 ID、响应 ID、超时和间隔必须来自 Profile。

**Tech Stack:** Python 3.11+，标准库 `unittest`，CLI 使用 `argparse`。YAML 读取使用 `PyYAML`，如果本机未安装，执行实现前必须先询问用户是否允许安装，不能自动安装。

---

## Chunk 1: M0 范围和成功标准

### 默认决策

1. M0 默认优先实现 `TsmasterAdapter`。
   - 原因：现有同星脚本已经有 LIN `0x3C` 请求和 `0x3D` 响应轮询路径，可从 `C:/Users/LIT/.codex/skills/tsmaster-bus-tool/scripts/tsmaster_lin_uds.py` 迁移。
   - 如果现场硬件只接了图莫斯，则只替换 Task 9 的真实适配器写入范围为 `Usb2xxxAdapter`，Task 1-8、Task 10-12 不变。
2. M0 不做 PySide6。
3. M0 不做 Profile 图形化下拉管理，但必须从固定 Profile 文件读取参数。
4. M0 不做完整双厂家扫描，但保留 `BusAdapter` 接口，避免后续 M1/M2 重写核心刷写流程。
5. M0 的真实硬件刷写命令必须由用户手动执行，计划和测试不能自动触发真实 ECU 刷写。

### M0 做完的判定

1. `$env:PYTHONPATH="src"; python -m unittest discover -s tests -v` 通过。
2. `$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.probe --adapter tsmaster` 能枚举或给出明确设备错误。
3. `$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.flash_e68_lin --adapter fake --profile profiles/e68_lin_bootloader.yaml --flash-driver tests/fixtures/flash_driver_18b.bin --app tests/fixtures/app_20b.bin --log-dir logs --dry-run` 能跑完整模拟刷写流程。
4. 真实硬件验证时，命令行必须显式传入 `--adapter tsmaster --no-dry-run`，且日志中能看到 `$10/$27/$31/$34/$36/$37/$11` 的 TX/RX。
5. 任何失败必须映射到明确错误分类：设备错误、Profile 错误、文件错误、传输错误、UDS 错误、刷写状态错误。

### 环境门禁

- [ ] **Step 1: 确认 Python 版本**

Run:

```powershell
python --version
```

Expected:

```text
Python 3.11.x 或更高版本
```

- [ ] **Step 2: 确认 PyYAML 是否已安装**

Run:

```powershell
python -c "import yaml; print(yaml.__version__)"
```

Expected:

```text
能打印版本号
```

If it fails:

```text
停止实现，询问用户是否允许安装 PyYAML。禁止自动安装。
```

- [ ] **Step 3: 确认当前仓库干净**

Run:

```powershell
git status --short
```

Expected:

```text
无输出，或只有本计划文件自身改动
```

---

## Chunk 2: 文件结构

### 新建文件

- `pyproject.toml`
  - Python 包配置，声明 `src` layout。
  - 不自动安装依赖，只记录 M0 运行要求。
- `requirements-m0.txt`
  - 只写 `PyYAML>=6.0`。
  - 执行安装前必须得到用户确认。
- `profiles/e68_lin_bootloader.yaml`
  - 固定 E68 Profile。
  - 包含 LIN、memory、uds、seedkey、workflow。
- `src/unified_can_lin_host_tool/__init__.py`
  - 包版本和包标识。
- `src/unified_can_lin_host_tool/core/errors.py`
  - 错误分类和异常类型。
- `src/unified_can_lin_host_tool/core/events.py`
  - Trace 事件数据结构。
- `src/unified_can_lin_host_tool/core/session.py`
  - `BusSession` 和 `DIAG_EXCLUSIVE` 独占状态。
- `src/unified_can_lin_host_tool/profile.py`
  - Profile 数据类、YAML 加载、校验。
- `src/unified_can_lin_host_tool/firmware/image.py`
  - `.bin` 固件读取、地址/长度校验、擦除长度对齐、分块。
- `src/unified_can_lin_host_tool/e68/crc32.py`
  - E68 Boot CRC32 口径。
- `src/unified_can_lin_host_tool/e68/seedkey.py`
  - App Level1 和 Boot FBL Seed/Key Provider。
- `src/unified_can_lin_host_tool/transport/base.py`
  - `LinFrame`、`BusAdapter` 抽象。
- `src/unified_can_lin_host_tool/transport/lin_diag.py`
  - `LinDiagTransport`，负责 NAD/PCI 组帧、连续帧、`0x3D` 轮询、响应解析。
- `src/unified_can_lin_host_tool/adapters/fake.py`
  - 纯测试 Fake Adapter。
- `src/unified_can_lin_host_tool/adapters/tsmaster.py`
  - M0 真实同星适配器。
- `src/unified_can_lin_host_tool/e68/flash_workflow.py`
  - E68 刷写状态机。
- `src/unified_can_lin_host_tool/trace.py`
  - 文本 Trace Log 写入。
- `src/unified_can_lin_host_tool/cli/probe.py`
  - M0 设备枚举 CLI。
- `src/unified_can_lin_host_tool/cli/flash_e68_lin.py`
  - M0 刷写 CLI。
- `tests/fixtures/flash_driver_18b.bin`
  - 测试用 FlashDriver 小文件。
- `tests/fixtures/app_20b.bin`
  - 测试用 App 小文件。

### 新建测试

- `tests/test_profile.py`
- `tests/test_e68_crc32.py`
- `tests/test_seedkey.py`
- `tests/test_firmware_image.py`
- `tests/test_bus_session.py`
- `tests/test_lin_diag_transport.py`
- `tests/test_flash_workflow_fake.py`
- `tests/test_trace.py`

---

## Chunk 3: Profile 和错误模型

### Task 1: 建立包骨架和依赖声明

**Files:**

- Create: `pyproject.toml`
- Create: `requirements-m0.txt`
- Create: `src/unified_can_lin_host_tool/__init__.py`

- [ ] **Step 1: 写最小包配置**

`pyproject.toml` 内容应包含：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "unified-can-lin-host-tool"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["PyYAML>=6.0"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: 写 M0 依赖说明**

`requirements-m0.txt`:

```text
PyYAML>=6.0
```

- [ ] **Step 3: 添加包初始化文件**

`src/unified_can_lin_host_tool/__init__.py`:

```python
"""统一 CAN/LIN 上位机 M0 核心包。"""

__version__ = "0.1.0"
```

- [ ] **Step 4: 验证包可导入**

Run:

```powershell
$env:PYTHONPATH="src"; python -c "import unified_can_lin_host_tool; print(unified_can_lin_host_tool.__version__)"
```

Expected:

```text
0.1.0
```

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml requirements-m0.txt src/unified_can_lin_host_tool/__init__.py
git commit -m "初始化M0 Python包骨架"
```

### Task 2: 定义错误分类

**Files:**

- Create: `src/unified_can_lin_host_tool/core/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: 写失败测试**

```python
import unittest

from unified_can_lin_host_tool.core.errors import HostToolError, ErrorCategory


class ErrorTests(unittest.TestCase):
    def test_error_keeps_category_and_message(self):
        err = HostToolError(ErrorCategory.PROFILE, "缺少 NAD")
        self.assertEqual(err.category, ErrorCategory.PROFILE)
        self.assertIn("缺少 NAD", str(err))
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_errors -v
```

Expected:

```text
ImportError 或 ModuleNotFoundError
```

- [ ] **Step 3: 实现最小错误模型**

```python
from enum import Enum


class ErrorCategory(str, Enum):
    DEVICE = "device"
    PROFILE = "profile"
    FILE = "file"
    TRANSPORT = "transport"
    UDS = "uds"
    FLASH_STATE = "flash_state"


class HostToolError(Exception):
    def __init__(self, category: ErrorCategory, message: str):
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.message = message
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_errors -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

```powershell
git add src/unified_can_lin_host_tool/core/errors.py tests/test_errors.py
git commit -m "新增M0错误分类"
```

### Task 3: 实现固定 E68 Profile

**Files:**

- Create: `profiles/e68_lin_bootloader.yaml`
- Create: `src/unified_can_lin_host_tool/profile.py`
- Test: `tests/test_profile.py`

- [ ] **Step 1: 写 Profile 文件**

`profiles/e68_lin_bootloader.yaml` 必须包含：

```yaml
name: E68 LIN Bootloader
bus:
  type: LIN
  baudrate: 19200
  request_id: 0x3C
  response_id: 0x3D
  nad: 0x02
memory:
  app_start: 0x00007000
  app_size: 0x00019000
  app_end: 0x00020000
  flash_driver_ram: 0x20001000
  flash_driver_max_size: 0x00002000
  page_size: 512
uds:
  p2_ms: 50
  p2_star_ms: 5000
  max_transfer_payload: 6
  request_download_format: 0x44
  frame_gap_ms: 12
  poll_timeout_ms: 300
  poll_gap_ms: 20
seedkey:
  app_level1: e68_level1
  boot_fbl: e68_fbl
workflow:
  name: e68_lin_bootloader_v1
```

- [ ] **Step 2: 写 Profile 加载测试**

```python
import unittest
from pathlib import Path

from unified_can_lin_host_tool.profile import load_profile


class ProfileTests(unittest.TestCase):
    def test_load_e68_profile(self):
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        self.assertEqual(profile.bus.nad, 0x02)
        self.assertEqual(profile.bus.request_id, 0x3C)
        self.assertEqual(profile.bus.response_id, 0x3D)
        self.assertEqual(profile.uds.frame_gap_ms, 12)

    def test_profile_rejects_missing_nad(self):
        raw = {
            "name": "bad",
            "bus": {"type": "LIN", "baudrate": 19200, "request_id": 0x3C, "response_id": 0x3D},
            "memory": {"app_start": 0x7000, "app_size": 0x19000, "app_end": 0x20000, "flash_driver_ram": 0x20001000, "flash_driver_max_size": 0x2000, "page_size": 512},
            "uds": {"p2_ms": 50, "p2_star_ms": 5000, "max_transfer_payload": 6, "request_download_format": 0x44, "frame_gap_ms": 12, "poll_timeout_ms": 300, "poll_gap_ms": 20},
            "seedkey": {"app_level1": "e68_level1", "boot_fbl": "e68_fbl"},
            "workflow": {"name": "e68_lin_bootloader_v1"},
        }
        with self.assertRaisesRegex(Exception, "nad"):
            load_profile(raw)
```

- [ ] **Step 3: 实现 Profile 数据类和校验**

关键要求：

```python
@dataclass(frozen=True)
class BusProfile:
    type: str
    baudrate: int
    request_id: int
    response_id: int
    nad: int
```

校验规则：

```text
bus.type 必须为 LIN
request_id、response_id、nad 必须存在且在 0x00..0xFF
app_start + app_size 必须等于 app_end
page_size 必须大于 0
max_transfer_payload 必须为 6
seedkey.app_level1 必须为 e68_level1
seedkey.boot_fbl 必须为 e68_fbl
workflow.name 必须为 e68_lin_bootloader_v1
```

- [ ] **Step 4: 运行 Profile 测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_profile -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

```powershell
git add profiles/e68_lin_bootloader.yaml src/unified_can_lin_host_tool/profile.py tests/test_profile.py
git commit -m "实现E68固定Profile加载"
```

---

## Chunk 4: E68 基础算法

### Task 4: 实现 E68 CRC32

**Files:**

- Create: `src/unified_can_lin_host_tool/e68/crc32.py`
- Test: `tests/test_e68_crc32.py`

- [ ] **Step 1: 写 CRC32 测试**

```python
import unittest

from unified_can_lin_host_tool.e68.crc32 import E68_CRC32_INIT, e68_crc32_update, e68_crc32


class E68Crc32Tests(unittest.TestCase):
    def test_known_vector_without_final_xor(self):
        self.assertEqual(e68_crc32(b"123456789"), 0x340BC6D9)

    def test_chunked_equals_single_pass(self):
        single = e68_crc32(b"abcdef")
        crc = e68_crc32_update(E68_CRC32_INIT, b"abc")
        crc = e68_crc32_update(crc, b"def")
        self.assertEqual(crc, single)

    def test_block_sequence_is_not_part_of_crc(self):
        data_crc = e68_crc32(bytes.fromhex("01 02 03 04 05 06"))
        wrong_crc = e68_crc32(bytes.fromhex("01") + bytes.fromhex("01 02 03 04 05 06"))
        self.assertNotEqual(data_crc, wrong_crc)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_e68_crc32 -v
```

Expected:

```text
ImportError 或函数未定义
```

- [ ] **Step 3: 实现 CRC32**

实现必须按固件 `Bootloader/Driver/Src/boot_crc32.c`：

```python
E68_CRC32_INIT = 0xFFFFFFFF


def e68_crc32_update(current_crc: int, data: bytes) -> int:
    crc = current_crc & 0xFFFFFFFF
    for value in data:
        crc = ((crc >> 8) & 0x00FFFFFF) ^ TABLE[(crc ^ value) & 0xFF]
        crc &= 0xFFFFFFFF
    return crc


def e68_crc32(data: bytes) -> int:
    return e68_crc32_update(E68_CRC32_INIT, data)
```

禁止使用：

```python
zlib.crc32(data)
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_e68_crc32 -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

```powershell
git add src/unified_can_lin_host_tool/e68/crc32.py tests/test_e68_crc32.py
git commit -m "实现E68传输CRC32"
```

### Task 5: 实现 E68 SeedKey

**Files:**

- Create: `src/unified_can_lin_host_tool/e68/seedkey.py`
- Test: `tests/test_seedkey.py`

- [ ] **Step 1: 写 SeedKey 测试**

测试向量来自当前 MCU 源码：

- App Level1: `Application/App/Src/lin_diag_app.c:56-131`
- Boot FBL: `Bootloader/App/Src/boot_security.c:33-65`

```python
import unittest

from unified_can_lin_host_tool.e68.seedkey import calc_e68_level1_key, calc_e68_fbl_key


class SeedKeyTests(unittest.TestCase):
    def test_app_level1_first_seed_vector(self):
        self.assertEqual(calc_e68_level1_key(bytes.fromhex("35 79 24 68")), bytes.fromhex("70 C7 71 B5"))

    def test_fbl_first_seed_vector(self):
        self.assertEqual(calc_e68_fbl_key(bytes.fromhex("24 68 35 79")), bytes.fromhex("4D 62 06 0F"))

    def test_algorithms_are_not_interchangeable(self):
        seed = bytes.fromhex("12 34 56 78")
        self.assertEqual(calc_e68_level1_key(seed), bytes.fromhex("70 10 00 B2"))
        self.assertEqual(calc_e68_fbl_key(seed), bytes.fromhex("21 57 00 0F"))
        self.assertNotEqual(calc_e68_level1_key(seed), calc_e68_fbl_key(seed))
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_seedkey -v
```

Expected:

```text
ImportError 或函数未定义
```

- [ ] **Step 3: 实现两个算法**

共同 Mask：

```python
MASK = bytes([0xF0, 0x45, 0x53, 0x73])
```

App Level1：

```python
key[0] = ((mixed[0] & 0x0F) << 4) | (mixed[1] & 0xF0)
key[1] = ((mixed[1] & 0x0F) << 4) | ((mixed[2] & 0xF0) >> 4)
key[2] = (mixed[2] & 0xF0) | ((mixed[3] & 0xF0) >> 4)
key[3] = ((mixed[3] & 0x0F) << 4) | (mixed[0] & 0x0F)
```

Boot FBL：

```python
key[0] = ((mixed[0] & 0x0F) << 4) | (mixed[1] & 0x0F)
key[1] = ((mixed[1] & 0xF0) >> 4) | ((mixed[2] & 0x0F) << 4)
key[2] = ((mixed[2] & 0xF0) >> 4) | (mixed[3] & 0xF0)
key[3] = (mixed[3] & 0x0F) | ((mixed[0] & 0xF0) >> 4)
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_seedkey -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

```powershell
git add src/unified_can_lin_host_tool/e68/seedkey.py tests/test_seedkey.py
git commit -m "实现E68安全访问算法"
```

### Task 6: 实现 BIN 固件镜像

**Files:**

- Create: `src/unified_can_lin_host_tool/firmware/image.py`
- Test: `tests/test_firmware_image.py`
- Create: `tests/fixtures/flash_driver_18b.bin`
- Create: `tests/fixtures/app_20b.bin`

- [ ] **Step 1: 创建 fixture**

生成内容固定的小文件：

```powershell
New-Item -ItemType Directory -Force tests\fixtures | Out-Null
[byte[]](1..18) | Set-Content -AsByteStream tests\fixtures\flash_driver_18b.bin
[byte[]](101..120) | Set-Content -AsByteStream tests\fixtures\app_20b.bin
```

- [ ] **Step 2: 写固件镜像测试**

```python
import unittest
from pathlib import Path

from unified_can_lin_host_tool.firmware.image import load_bin_image, align_up, split_transfer_chunks
from unified_can_lin_host_tool.profile import load_profile


class FirmwareImageTests(unittest.TestCase):
    def test_load_app_bin_uses_profile_start(self):
        profile = load_profile(Path("profiles/e68_lin_bootloader.yaml"))
        image = load_bin_image(Path("tests/fixtures/app_20b.bin"), start_address=profile.memory.app_start, max_size=profile.memory.app_size)
        self.assertEqual(image.start_address, 0x7000)
        self.assertEqual(image.size, 20)

    def test_align_erase_length_to_page(self):
        self.assertEqual(align_up(20, 512), 512)
        self.assertEqual(align_up(1024, 512), 1024)

    def test_split_transfer_chunks_uses_payload_size_6(self):
        chunks = list(split_transfer_chunks(bytes(range(14)), max_payload=6))
        self.assertEqual(chunks, [bytes(range(6)), bytes(range(6, 12)), bytes(range(12, 14))])
```

- [ ] **Step 3: 实现镜像模型**

核心接口：

```python
@dataclass(frozen=True)
class FirmwareImage:
    path: Path
    start_address: int
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def end_address(self) -> int:
        return self.start_address + self.size
```

- [ ] **Step 4: 运行测试确认通过**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_firmware_image -v
```

Expected:

```text
OK
```

- [ ] **Step 5: Commit**

```powershell
git add src/unified_can_lin_host_tool/firmware/image.py tests/test_firmware_image.py tests/fixtures
git commit -m "实现BIN固件镜像处理"
```

---

## Chunk 5: LIN 传输和独占会话

### Task 7: 实现 BusSession 独占状态

**Files:**

- Create: `src/unified_can_lin_host_tool/core/session.py`
- Test: `tests/test_bus_session.py`

- [ ] **Step 1: 写独占状态测试**

```python
import unittest

from unified_can_lin_host_tool.core.session import BusSession


class BusSessionTests(unittest.TestCase):
    def test_diag_exclusive_blocks_second_owner(self):
        session = BusSession()
        self.assertTrue(session.enter_diag_exclusive("uds"))
        self.assertFalse(session.enter_diag_exclusive("flash"))
        session.release_diag_exclusive("uds")
        self.assertTrue(session.enter_diag_exclusive("flash"))

    def test_wrong_owner_cannot_release(self):
        session = BusSession()
        session.enter_diag_exclusive("flash")
        with self.assertRaisesRegex(RuntimeError, "owner"):
            session.release_diag_exclusive("uds")
```

- [ ] **Step 2: 实现 `BusSession`**

关键行为：

```text
enter_diag_exclusive(owner) 成功后记录 owner
已有 owner 时返回 False
release_diag_exclusive(owner) 只能由当前 owner 释放
FlashWorkflow 启动前必须申请，finally 中必须释放
```

- [ ] **Step 3: 运行测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_bus_session -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Commit**

```powershell
git add src/unified_can_lin_host_tool/core/session.py tests/test_bus_session.py
git commit -m "实现诊断独占会话"
```

### Task 8: 实现 LinDiagTransport 和 FakeAdapter

**Files:**

- Create: `src/unified_can_lin_host_tool/transport/base.py`
- Create: `src/unified_can_lin_host_tool/transport/lin_diag.py`
- Create: `src/unified_can_lin_host_tool/adapters/fake.py`
- Test: `tests/test_lin_diag_transport.py`

- [ ] **Step 1: 写单帧请求测试**

```python
import unittest

from unified_can_lin_host_tool.adapters.fake import FakeLinAdapter
from unified_can_lin_host_tool.profile import load_profile
from unified_can_lin_host_tool.transport.lin_diag import LinDiagTransport


class LinDiagTransportTests(unittest.TestCase):
    def test_single_frame_request_uses_profile_nad_and_ids(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("02 02 50 01 FF FF FF FF"))])
        transport = LinDiagTransport(adapter, profile)

        response = transport.request(bytes.fromhex("10 01"), expect_prefix=bytes.fromhex("50 01"))

        self.assertEqual(response.payload, bytes.fromhex("50 01"))
        self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("02 02 10 01 FF FF FF FF")))
```

- [ ] **Step 2: 写多帧请求测试**

```python
def test_multi_frame_request_splits_to_first_and_consecutive_frames(self):
    profile = load_profile("profiles/e68_lin_bootloader.yaml")
    adapter = FakeLinAdapter(responses=[(0x3D, bytes.fromhex("02 02 76 01 FF FF FF FF"))])
    transport = LinDiagTransport(adapter, profile)

    transport.request(bytes.fromhex("36 01 01 02 03 04 05 06"), expect_prefix=bytes.fromhex("76 01"))

    self.assertEqual(adapter.sent_frames[0], (0x3C, bytes.fromhex("02 10 08 36 01 01 02 03")))
    self.assertEqual(adapter.sent_frames[1], (0x3C, bytes.fromhex("02 21 04 05 06 FF FF FF")))
```

- [ ] **Step 3: 写 `0x78` 测试**

```python
def test_response_pending_waits_for_final_response(self):
    profile = load_profile("profiles/e68_lin_bootloader.yaml")
    adapter = FakeLinAdapter(responses=[
        (0x3D, bytes.fromhex("02 03 7F 31 78 FF FF FF")),
        (0x3D, bytes.fromhex("02 04 71 01 FF 00 FF FF")),
    ])
    transport = LinDiagTransport(adapter, profile)

    response = transport.request(bytes.fromhex("31 01 FF 00 00 00 70 00 00 00 02 00"), expect_prefix=bytes.fromhex("71 01 FF 00"), allow_response_pending=True)

    self.assertEqual(response.payload[:4], bytes.fromhex("71 01 FF 00"))
```

- [ ] **Step 4: 实现基础抽象**

`BusAdapter` 最小接口：

```python
class BusAdapter(Protocol):
    def send_lin_frame(self, frame_id: int, data: bytes) -> None:
        ...

    def receive_lin_frame(self, frame_id: int, timeout_ms: int) -> LinFrame | None:
        ...
```

`LinDiagTransport` 必须：

```text
从 Profile 读取 nad/request_id/response_id/frame_gap_ms/poll_timeout_ms/poll_gap_ms
单帧 UDS：NAD + PCI(len) + UDS + 0xFF padding
多帧 UDS：FF + CF，连续帧 SN 从 1 开始
只从 response_id 读取诊断响应
校验响应 NAD
解析单帧 PCI
遇到 7F xx 78 且 allow_response_pending=True 时继续轮询
遇到其他 NRC 时抛 UDS 错误
超时抛 TRANSPORT 错误
```

- [ ] **Step 5: 运行测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_lin_diag_transport -v
```

Expected:

```text
OK
```

- [ ] **Step 6: Commit**

```powershell
git add src/unified_can_lin_host_tool/transport src/unified_can_lin_host_tool/adapters/fake.py tests/test_lin_diag_transport.py
git commit -m "实现LIN诊断传输抽象"
```

### Task 9: 实现 TSMaster M0 适配器

**Files:**

- Create: `src/unified_can_lin_host_tool/adapters/tsmaster.py`
- Create: `src/unified_can_lin_host_tool/cli/probe.py`

- [ ] **Step 1: 迁移前核对来源脚本**

Read:

```powershell
Get-Content C:\Users\LIT\.codex\skills\tsmaster-bus-tool\scripts\tsmaster_lin_uds.py -TotalCount 260
```

重点确认：

```text
DLL 路径来自 D:/software/TSMaster/bin64
LIN master 需要 start channel
轮询 0x3D 使用 transmit_header_and_receive_msg
FIFO 需要先打开
完成后需要 disconnect/finalize
```

- [ ] **Step 2: 实现 `TsmasterAdapter.probe()`**

最小目标：

```text
能加载 TSMaster DLL
能返回工具名、app channel、hardware channel、LIN channel 数
失败时抛 DEVICE 错误
```

- [ ] **Step 3: 实现 LIN 帧发送/接收**

`send_lin_frame(0x3C, data)`:

```text
发送 LIN master request frame
Classic checksum
记录 TX 事件
```

`receive_lin_frame(0x3D, timeout_ms)`:

```text
发送 0x3D header
等待 slave response
收到后返回 LinFrame
超时返回 None
```

- [ ] **Step 4: 实现 probe CLI**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.probe --adapter tsmaster
```

Expected:

```text
有硬件时打印 TSMaster 工具和 LIN 通道
无硬件或 DLL 错误时打印明确 DEVICE 错误
```

- [ ] **Step 5: Commit**

```powershell
git add src/unified_can_lin_host_tool/adapters/tsmaster.py src/unified_can_lin_host_tool/cli/probe.py
git commit -m "实现同星M0适配器探测"
```

---

## Chunk 6: E68 刷写工作流和日志

### Task 10: 实现 Trace Log

**Files:**

- Create: `src/unified_can_lin_host_tool/core/events.py`
- Create: `src/unified_can_lin_host_tool/trace.py`
- Test: `tests/test_trace.py`

- [ ] **Step 1: 写日志测试**

```python
import tempfile
import unittest
from pathlib import Path

from unified_can_lin_host_tool.core.events import TraceEvent
from unified_can_lin_host_tool.trace import TraceLogger


class TraceTests(unittest.TestCase):
    def test_trace_logger_writes_tx_rx(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TraceLogger(Path(tmp))
            logger.write(TraceEvent(direction="TX", frame_id=0x3C, data=bytes.fromhex("02 02 10 01 FF FF FF FF"), note="$10 01"))
            logger.close()
            text = logger.path.read_text(encoding="utf-8")
            self.assertIn("TX", text)
            self.assertIn("0x3C", text)
            self.assertIn("02 02 10 01", text)
```

- [ ] **Step 2: 实现日志格式**

建议文本格式：

```text
2026-04-26T20:00:00.000 TX LIN id=0x3C data=02 02 10 01 FF FF FF FF note=$10 01
2026-04-26T20:00:00.040 RX LIN id=0x3D data=02 02 50 01 FF FF FF FF note=$50 01
```

- [ ] **Step 3: 运行测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_trace -v
```

Expected:

```text
OK
```

- [ ] **Step 4: Commit**

```powershell
git add src/unified_can_lin_host_tool/core/events.py src/unified_can_lin_host_tool/trace.py tests/test_trace.py
git commit -m "实现M0文本Trace日志"
```

### Task 11: 实现 E68 FlashWorkflow

**Files:**

- Create: `src/unified_can_lin_host_tool/e68/flash_workflow.py`
- Test: `tests/test_flash_workflow_fake.py`

- [ ] **Step 1: 写 fake 完整流程测试**

测试应使用 `FakeLinAdapter` 预置响应，验证 TX UDS 顺序：

```python
class FlashWorkflowFakeTests(unittest.TestCase):
    def test_full_flash_sequence_uses_diag_exclusive(self):
        profile = load_profile("profiles/e68_lin_bootloader.yaml")
        flash_driver = load_bin_image(Path("tests/fixtures/flash_driver_18b.bin"), profile.memory.flash_driver_ram, profile.memory.flash_driver_max_size)
        app = load_bin_image(Path("tests/fixtures/app_20b.bin"), profile.memory.app_start, profile.memory.app_size)
        session = BusSession()
        adapter = FakeLinAdapter.for_e68_flash_success(profile)
        transport = LinDiagTransport(adapter, profile)
        workflow = FlashWorkflow(profile, transport, session)

        result = workflow.run(flash_driver=flash_driver, app=app)

        self.assertTrue(result.success)
        self.assertFalse(session.is_diag_exclusive)
        uds_payloads = adapter.sent_uds_payloads()
        self.assertEqual(uds_payloads[0], bytes.fromhex("10 01"))
        self.assertIn(bytes.fromhex("31 01 02 03"), uds_payloads)
        self.assertIn(bytes.fromhex("11 01"), uds_payloads)
```

- [ ] **Step 2: 写失败释放独占测试**

```python
def test_failure_releases_diag_exclusive(self):
    profile = load_profile("profiles/e68_lin_bootloader.yaml")
    session = BusSession()
    adapter = FakeLinAdapter(responses=[])
    workflow = FlashWorkflow(profile, LinDiagTransport(adapter, profile), session)

    with self.assertRaises(Exception):
        workflow.run(flash_driver=small_flash_driver, app=small_app)

    self.assertFalse(session.is_diag_exclusive)
```

- [ ] **Step 3: 实现工作流步骤**

必须按设计文档顺序：

```text
1. 10 01 -> 50 01
2. 10 03 -> 50 03
3. 27 01 -> 67 01 + seed
4. 27 02 + app_level1_key -> 67 02
5. 31 01 02 03 -> 71 01 02 03 00
6. 10 02 -> 50 02
7. 轮询 10 02，直到 Boot 回 50 02 或超时
8. 27 09 -> 67 09 + seed
9. 27 0A + fbl_key -> 67 0A
10. FlashDriver: 34, 多个 36, 37, 31 01 02 02
11. App erase: 31 01 FF 00 + appStart32 + eraseLength32，允许 7F 31 78 后等 71 01 FF 00
12. App: 34, 多个 36, 37, 31 01 FF 01
13. 11 01 -> 51 01
```

`FlashWorkflow.run()` 必须：

```python
if not session.enter_diag_exclusive("flash"):
    raise HostToolError(ErrorCategory.TRANSPORT, "LIN channel is busy")
try:
    ...
finally:
    session.release_diag_exclusive("flash")
```

- [ ] **Step 4: 实现 `$36` 块序号**

规则：

```text
第一块 blockSequence = 0x01
0xFF 后回绕到 0x00
每个逻辑下载块独立使用同一递增规则
```

- [ ] **Step 5: 实现 `$37` CRC 校验**

规则：

```text
FlashDriver 下载和 App 下载分别从 0xFFFFFFFF 重新计算 CRC
CRC 只覆盖 `$36` 数据区
发送 `$37 + crc32_be`
响应必须等于 `$77 + crc32_be`
```

- [ ] **Step 6: 运行 fake 工作流测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest tests.test_flash_workflow_fake -v
```

Expected:

```text
OK
```

- [ ] **Step 7: Commit**

```powershell
git add src/unified_can_lin_host_tool/e68/flash_workflow.py tests/test_flash_workflow_fake.py
git commit -m "实现E68 LIN刷写状态机"
```

### Task 12: 实现刷写 CLI

**Files:**

- Create: `src/unified_can_lin_host_tool/cli/flash_e68_lin.py`
- Modify: `src/unified_can_lin_host_tool/adapters/fake.py`
- Modify: `src/unified_can_lin_host_tool/adapters/tsmaster.py`

- [ ] **Step 1: 实现 CLI 参数**

必须支持：

```text
--adapter fake|tsmaster
--profile profiles/e68_lin_bootloader.yaml
--flash-driver D:/path/flash_driver.bin
--app D:/path/app.bin
--log-dir logs
--dry-run
--no-dry-run
```

默认必须是 `--dry-run`，真实刷写必须显式传 `--no-dry-run`。

- [ ] **Step 2: 实现 fake dry-run 命令**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.flash_e68_lin --adapter fake --profile profiles/e68_lin_bootloader.yaml --flash-driver tests/fixtures/flash_driver_18b.bin --app tests/fixtures/app_20b.bin --log-dir logs --dry-run
```

Expected:

```text
FLASH SUCCESS
日志路径
```

- [ ] **Step 3: 实现真实硬件保护**

如果 `--adapter tsmaster` 但没有 `--no-dry-run`：

```text
只允许 probe 和参数检查，不允许发送任何刷写请求
```

如果传了 `--no-dry-run`：

```text
打印 Profile、工具链、通道、App 文件、FlashDriver 文件、擦除范围
要求用户在命令行输入 YES 后才继续
```

- [ ] **Step 4: 运行全量测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 5: 运行 dry-run**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.flash_e68_lin --adapter fake --profile profiles/e68_lin_bootloader.yaml --flash-driver tests/fixtures/flash_driver_18b.bin --app tests/fixtures/app_20b.bin --log-dir logs --dry-run
```

Expected:

```text
FLASH SUCCESS
```

- [ ] **Step 6: Commit**

```powershell
git add src/unified_can_lin_host_tool/cli/flash_e68_lin.py src/unified_can_lin_host_tool/adapters tests
git commit -m "新增E68刷写CLI"
```

---

## Chunk 7: M0 硬件验证

### Task 13: 同星硬件 smoke test

**Files:**

- No source changes unless smoke test exposes a concrete defect.

- [ ] **Step 1: 枚举 TSMaster**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.probe --adapter tsmaster
```

Expected:

```text
列出 TSMaster 设备和 LIN 通道
```

- [ ] **Step 2: 手动 UDS 轻量验证**

先只发非破坏性请求，例如 `$10 01` 或读版本请求，不进入擦除。

Expected:

```text
TX 0x3C: 02 02 10 01 FF FF FF FF
RX 0x3D: 02 02 50 01 FF FF FF FF
```

- [ ] **Step 3: 真实刷写前检查**

必须人工确认：

```text
ECU 供电稳定
LIN 接线和共地正确
App bin 和 FlashDriver bin 路径正确
当前 Profile 地址范围符合 Bootloader/Cfg/boot_mem_map.h
日志目录可写
```

- [ ] **Step 4: 执行真实刷写**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.flash_e68_lin --adapter tsmaster --profile profiles/e68_lin_bootloader.yaml --flash-driver D:\path\flash_driver.bin --app D:\path\app.bin --log-dir logs --no-dry-run
```

Expected:

```text
每个阶段有进度输出
完成时打印 FLASH SUCCESS
日志文件包含完整 TX/RX
```

- [ ] **Step 5: 失败时处理**

不要直接重试。先按错误类别处理：

```text
DEVICE：检查 DLL、设备、通道映射
TRANSPORT：检查 LIN 接线、NAD、0x3C/0x3D、超时和 poll gap
UDS：检查 NRC、会话状态、SeedKey、块序号
FLASH_STATE：检查 FlashDriver、擦除、CRC32、App 完整性
```

- [ ] **Step 6: Commit 修复**

只有发现并修复了代码问题才提交：

```powershell
git add <changed-files>
git commit -m "修复M0硬件刷写问题"
```

---

## Chunk 8: 最终验证和交付

### Task 14: M0 收口检查

**Files:**

- Modify only if verification exposes issue.

- [ ] **Step 1: 全量单元测试**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unittest discover -s tests -v
```

Expected:

```text
OK
```

- [ ] **Step 2: dry-run CLI**

Run:

```powershell
$env:PYTHONPATH="src"; python -m unified_can_lin_host_tool.cli.flash_e68_lin --adapter fake --profile profiles/e68_lin_bootloader.yaml --flash-driver tests/fixtures/flash_driver_18b.bin --app tests/fixtures/app_20b.bin --log-dir logs --dry-run
```

Expected:

```text
FLASH SUCCESS
```

- [ ] **Step 3: 文档一致性检查**

Run:

```powershell
rg -n "DIAG_EXCLUSIVE|LinDiagTransport|CRC32|e68_level1|e68_fbl|0x3C|0x3D" docs src tests profiles
```

Expected:

```text
关键约束在设计文档、实现和测试中都有对应命中
```

- [ ] **Step 4: git 空白检查**

Run:

```powershell
git diff --check
```

Expected:

```text
无错误
```

- [ ] **Step 5: 最终提交**

Run:

```powershell
git status --short
git log --oneline -5
```

Expected:

```text
工作区干净
最近提交覆盖 M0 核心任务
```

### M0 后进入 M1 的条件

满足以下条件再进入 M1：

1. Fake dry-run 稳定通过。
2. 至少完成一次真实硬件非破坏性 UDS 验证。
3. 真实刷写若尚未执行，必须明确标记为未完成，不允许声称 M0 刷写闭环完成。
4. Trace Log 足以复盘每条 LIN TX/RX。
5. `FlashWorkflow` 的 `DIAG_EXCLUSIVE` 申请和释放已有测试覆盖。
