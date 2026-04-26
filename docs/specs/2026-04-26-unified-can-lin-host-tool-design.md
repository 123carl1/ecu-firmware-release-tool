# 统一 CAN/LIN 上位机设计说明

日期：2026-04-26

## 目标

做一个 Windows 上位机，统一支持图莫斯 USB2XXX 和同星 TSMaster 工具。用户插入 USB 工具后，上位机能扫描当前连接的工具和可用通道，在界面选择 Profile 和通道后，完成 CAN/LIN 手动收发、UDS 诊断、E68 LIN Bootloader 刷写，并实时显示和自动保存收发日志。

第一版采用“通用总线平台 + E68 首个闭环”的两层架构：

1. 底层是通用能力：设备枚举、通道抽象、连接、发送、接收、日志。
2. 上层是项目能力：Profile、UDS、安全访问算法、E68 LIN 刷写流程。

第一版做工程师自用版，使用 Python + PySide6 开发，但目录、配置、日志和适配器边界按后续 exe 打包预留。

## 不做的范围

第一版明确不做以下能力：

1. 不做多通道并发连接。一次只连接一个活动通道。
2. 不做完整 CAN Bootloader 刷写闭环。CAN 只做通用收发和 UDS 基础接口预留。
3. 不做 Profile 图形化编辑器。Profile 由软件目录下的配置文件提供，界面只负责下拉选择。
4. 不做日志回放。日志实时显示并自动保存文件。
5. 不做外部 Seed/Key 算法文件加载。第一版内置 E68 Level1 和 E68 FBL 算法，并在界面可选；外部算法文件作为后续扩展。

## 已确认需求

1. 技术栈：Python + PySide6。
2. 交付形态：先做工程师自用版，架构预留后续打包。
3. 设备范围：图莫斯 USB2XXX、同星 TSMaster。
4. 扫描含义：扫描 PC 侧 USB 工具和通道，不在扫描阶段主动发送 CAN/LIN 探测报文。
5. 通道选择：按 Profile 推荐匹配通道，用户确认后连接。
6. 连接策略：第一版一次只连接一个通道。
7. 刷写范围：第一版完整支持 E68 LIN Bootloader；CAN 刷写后续扩展。
8. 固件输入：App 固件和 FlashDriver 固件分开选择。
9. 文件格式：支持 `.bin` 和 `.hex`。
10. 日志：实时显示并自动保存文件，不做回放。
11. UI：混合工程面板。左侧常驻 Profile、扫描结果和连接状态；右侧 Tab 切换总线收发、UDS 诊断、E68 刷写、配置摘要；底部常驻 Trace Log。

## 事实依据

当前仓库中没有完整 E68 LIN 上位机刷写脚本。已有工具脚本和当前 E68 LIN Bootloader 流程不是同一套协议：

1. `C:/Users/LIT/.codex/skills/usb2xxx-bus-tool/scripts/usb2xxx_bus_tool.py`
   - 可复用 USB2XXX 枚举、CAN/LIN 收发、LIN UDS 调用方式。
   - 其中 `can-boot-download` 是 CAN 私有 Boot 命令流程，使用 `0x22 + 0x80/0xC1/0x42/0x03/0xC4/0x85/0x06`、CRC16、1024 字节 chunk。
   - 该流程不能直接用于当前 E68 LIN UDS Bootloader。

2. `C:/Users/LIT/.codex/skills/tsmaster-bus-tool/scripts/tsmaster_lin_uds.py`
   - 可复用 TSMaster 通过 LIN `0x3C/0x3D` 发送原始 UDS 请求和轮询响应的方式。
   - 它没有完整刷写状态机、Seed/Key、文件解析、进度和错误恢复。

3. 当前 E68 LIN Bootloader 真实流程来自源码和文档：
   - `Application/App/Src/lin_diag_app.c`
   - `Bootloader/App/Src/boot_diag.c`
   - `Bootloader/Driver/Src/boot_lin_diag.c`
   - `Bootloader/Driver/Src/boot_flash_exec.c`
   - `Bootloader/Cfg/boot_diag_cfg.h`
   - `Bootloader/Cfg/boot_mem_map.h`
   - `docs/OTA压力测试方案.md`

## 总体架构

上位机分为四层：

1. UI 层
   - PySide6 主窗口。
   - 左侧：Profile 下拉框、扫描按钮、设备和通道列表、连接按钮、连接状态。
   - 右侧：总线收发、UDS 诊断、E68 刷写、配置摘要。
   - 底部：实时 Trace Log。

2. 应用服务层
   - `ProfileManager`：加载和校验 Profile。
   - `DeviceManager`：统一枚举图莫斯和同星工具。
   - `BusSession`：管理当前唯一活动通道。
   - `UdsClient`：封装 UDS 请求响应。
   - `FlashWorkflow`：执行 E68 LIN 刷写状态机。
   - `TraceLogger`：统一记录 UI 日志和文件日志。

3. 总线抽象层
   - `BusAdapter` 接口：枚举设备、打开通道、关闭通道、发送帧、接收帧。
   - `LinChannel` / `CanChannel` 通道模型。
   - `BusFrame` 统一帧模型。

4. 厂家适配层
   - `Usb2xxxAdapter`：封装 `USB2XXX.dll`。
   - `TsmasterAdapter`：封装 `TSMaster.dll`。
   - 厂家 DLL 路径和默认参数来自配置，不写死在业务流程里。

## 建议目录结构

建议在仓库下新增 `host_tool/`：

```text
host_tool/
  main.py
  app/
    main_window.py
    widgets/
  core/
    profile.py
    firmware_image.py
    trace_logger.py
    errors.py
  bus/
    models.py
    session.py
    adapter.py
  adapters/
    usb2xxx_adapter.py
    tsmaster_adapter.py
  uds/
    lin_transport.py
    can_transport.py
    client.py
    nrc.py
  seedkey/
    provider.py
    e68_level1.py
    e68_fbl.py
  flashing/
    e68_lin_workflow.py
  profiles/
    e68_lin_bootloader.yaml
    generic_can_500k.yaml
    generic_lin_19200.yaml
  logs/
  tests/
```

## Profile 设计

Profile 是项目配置档案，用户在界面下拉选择，不需要每次手动找路径。

`E68 LIN Bootloader` Profile 至少包含：

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

seedkey:
  app_level1: e68_level1
  boot_fbl: e68_fbl

workflow:
  name: e68_lin_bootloader_v1
```

Profile 校验规则：

1. 总线类型必须与活动通道类型匹配。
2. LIN Profile 必须有 NAD、请求 ID、响应 ID。
3. 地址范围必须不重叠 Boot 和 Flag 区。
4. App 地址、FlashDriver 地址和最大长度必须能被文件解析结果校验。
5. SeedKey 算法名必须能找到对应内置 Provider。

## 固件文件解析

第一版支持 `.bin` 和 `.hex`。

`.bin` 规则：

1. App `.bin` 使用 Profile 中的 `app_start` 作为起始地址。
2. FlashDriver `.bin` 使用 Profile 中的 `flash_driver_ram` 作为起始地址。
3. 文件长度必须小于等于 Profile 允许范围。

`.hex` 规则：

1. 解析 Intel HEX 地址段。
2. App HEX 地址必须落在 App 区。
3. FlashDriver HEX 地址必须落在 FlashDriver RAM 区，或通过配置明确允许重定位。
4. 若 HEX 存在多个不连续段，第一版可以拒绝，也可以合并为带空洞填充的 MemoryImage。推荐第一版先拒绝多段不连续镜像，减少刷写歧义。

刷写前 UI 必须显示：

1. 文件路径。
2. 文件类型。
3. 起始地址。
4. 结束地址。
5. 有效长度。
6. CRC32。
7. 是否越界。
8. 是否满足对齐要求。

## 统一日志

Trace Log 同时写 UI 和文件。

每条日志建议包含：

```text
timestamp
level
tool_vendor
tool_name
channel
direction
frame_type
raw_frame
uds_sid
uds_desc
workflow_step
elapsed_ms
message
```

日志类别：

1. `DEVICE`：SDK 加载、设备枚举、通道列表。
2. `CONNECT`：连接、断开、配置波特率。
3. `BUS_TX` / `BUS_RX`：原始 CAN/LIN 帧。
4. `UDS_REQ` / `UDS_RESP`：UDS 解析后的请求和响应。
5. `FLASH`：刷写阶段、进度、文件信息、耗时。
6. `ERROR`：失败原因、NRC、异常栈摘要。

每次连接或刷写生成独立日志文件。文件名建议：

```text
logs/2026-04-26/195000_e68_lin_bootloader_usb2xxx_lin1.log
```

## UI 工作流

启动后：

1. 加载 `profiles/`。
2. 默认选择上次使用的 Profile；没有历史记录时选择 `E68 LIN Bootloader`。
3. 用户点击“扫描 USB 工具”。
4. `DeviceManager` 调用两个适配器分别枚举：
   - 图莫斯 USB2XXX
   - 同星 TSMaster
5. UI 显示工具和通道：
   - 工具厂商
   - 设备名
   - 序列号或句柄
   - CAN 通道
   - LIN 通道
6. 按 Profile 推荐通道。例如 E68 LIN Profile 优先推荐 LIN 通道。
7. 用户选择通道并连接。
8. 连接成功后，右侧 Tab 可执行手动收发、UDS 或刷写。

## UDS 诊断界面

第一版需要支持两类操作：

1. 快捷服务
   - `$10` 会话控制。
   - `$22` 读 DID。
   - `$27` 安全访问。
   - `$31` RoutineControl。
   - `$11` ECUReset。

2. 原始 UDS
   - 用户输入十六进制 UDS 负载。
   - 上位机按当前 Profile 自动补 LIN NAD/PCI 或 CAN 传输层。
   - 显示原始响应和解析响应。

安全访问界面：

1. 安全等级可选：
   - App Level1：`$27 01/02`
   - Boot FBL：`$27 09/0A`
2. 算法可选：
   - `E68 Level1`
   - `E68 FBL`
3. 刷写流程自动选择正确算法，不要求用户手动选择。
4. 后续扩展外部算法文件加载。

## E68 LIN 刷写流程

当前真实流程不能简化为直接从 App 进入 Boot。第一版必须实现完整 App 预编程和 Boot 主编程。

标准流程：

1. App 默认会话：发送 `$10 01`，期望 `$50 01`。
2. App 扩展会话：发送 `$10 03`，期望 `$50 03`。
3. App Level1 安全访问：发送 `$27 01` 获取 4 字节 Seed，计算 Key 后发送 `$27 02 + Key`，期望 `$67 02`。
4. App 预编程检查：发送 `$31 01 02 03`，期望 `$71 01 02 03 00`。
5. App 进入编程会话：发送 `$10 02`，期望 `$50 02`；App 响应发出后写 Boot 请求标志并复位。
6. 等待 Boot 响应。上位机应在窗口期内轮询 Boot 编程会话请求。
7. Boot 编程会话：发送 `$10 02`，期望 `$50 02`。
8. Boot FBL 安全访问：发送 `$27 09` 获取 Seed，计算 Key 后发送 `$27 0A + Key`，期望 `$67 0A`。
9. FlashDriver 下载：
   - 发送 `$34 00 44 + flashDriverAddr32 + flashDriverSize32`。
   - 期望 `$74 20 00 06` 或等价最大块长响应。
   - 按 6 字节以内有效数据发送多次 `$36 blockSequence + data`。
   - 每块期望 `$76 blockSequence`。
   - 发送 `$37 + CRC32`。
   - 期望 `$77 + CRC32`。
10. FlashDriver 检查：发送 `$31 01 02 02`，期望 `$71 01 02 02 00`。
11. App 擦除：
   - 发送 `$31 01 FF 00 + appStart32 + eraseLength32`。
   - 先期望 `$7F 31 78`。
   - 持续轮询响应，直到收到 `$71 01 FF 00` 或超时。
12. App 下载：
   - 发送 `$34 00 44 + appStart32 + appSize32`。
   - 按 6 字节以内有效数据发送 `$36`。
   - 发送 `$37 + CRC32`。
   - 期望 `$77 + CRC32`。
13. App 完整性检查：发送 `$31 01 FF 01`，期望 `$71 01 FF 01 00`。
14. ECU 复位：发送 `$11 01`，期望 `$51 01`。
15. 复位后可选读 App 版本或观察业务帧，确认回到 App。

关键协议规则：

1. LIN 诊断帧使用 `0x3C` 请求、`0x3D` 响应。
2. NAD 固定为 `0x02`。
3. Boot 侧 LIN 诊断支持单帧和请求多帧；响应当前为单帧，最大 UDS 响应载荷 6 字节。
4. `$34` 格式固定为 `34 00 44 + addr32 + size32`。
5. `$36` 每块请求 UDS 负载长度必须在 `3..8`，即有效数据最多 6 字节。
6. `$36` 块序号从 `0x01` 开始，`0xFF` 后回绕到 `0x00`。
7. `$37` CRC32 初始值为 `0xFFFFFFFF`，只覆盖 `$36` 数据区，不包含块序号。
8. App 擦除长度必须按 512B 页对齐。
9. App 下载长度可以非 4 字节整数，Boot 写入层最终用 `0xFF` 补齐最后不足 4 字节的数据。

## Seed/Key 算法

第一版实现两个内置 Provider：

1. `E68Level1SeedKeyProvider`
   - 对应 App 侧 `$27 01/02`。
   - 4 字节 Seed，4 字节 Key。
   - 算法来自 `Application/App/Src/lin_diag_app.c`。

2. `E68FblSeedKeyProvider`
   - 对应 Boot 侧 `$27 09/0A`。
   - 4 字节 Seed，4 字节 Key。
   - 算法来自 `Bootloader/App/Src/boot_security.c`。

两个算法不能混用。UI 必须把安全等级和算法绑定清楚。

## 错误处理

错误必须分类，不能只显示“刷写失败”。

错误分类：

1. 设备错误
   - SDK DLL 不存在。
   - 设备未插入。
   - 设备打开失败。
   - 通道配置失败。

2. Profile 错误
   - 通道类型与 Profile 不匹配。
   - 缺少 NAD、请求 ID、响应 ID。
   - 地址范围非法。
   - SeedKey Provider 不存在。

3. 文件错误
   - 文件不存在。
   - 格式不支持。
   - HEX 地址越界。
   - BIN 大小超限。
   - App 或 FlashDriver 区域错误。

4. 传输错误
   - LIN 无响应。
   - LIN 校验错误。
   - 响应 NAD 不匹配。
   - 响应 PCI 不合法。
   - 超时。

5. UDS 错误
   - 正响应 SID 不匹配。
   - 块序号不匹配。
   - NRC 负响应。
   - `0x78` 后等待最终响应超时。

6. 刷写状态错误
   - App 预编程检查失败。
   - FlashDriver 检查失败。
   - 擦除失败。
   - `$37` CRC 失败。
   - App 完整性检查失败。
   - `$11 01` 前 App 未验证。

NRC 显示必须包含含义：

```text
0x12 子功能不支持
0x13 长度错误
0x22 条件不满足
0x24 请求序列错误
0x31 请求超范围
0x35 Key 错误
0x36 安全访问尝试次数超限
0x72 通用编程失败
0x78 响应挂起
```

## 验证策略

第一版实现必须先通过纯逻辑测试和模拟总线测试，再做硬件验证。

纯逻辑测试：

1. BIN 解析。
2. Intel HEX 解析。
3. 地址范围校验。
4. CRC32，含分段更新。
5. `$36` 块序号递增和回绕。
6. App Level1 Seed/Key。
7. Boot FBL Seed/Key。
8. NRC 解析。
9. Profile 校验。

模拟总线测试：

1. 正常完整刷写流程。
2. App 预编程检查返回失败。
3. Boot `$27` Key 错误。
4. FlashDriver `$37` CRC 错误。
5. `$31 FF00` 返回 `0x78` 后最终成功。
6. `$31 FF00` 返回 `0x78` 后最终超时。
7. `$36` 块序号错误。
8. LIN 响应超时。

硬件验证：

1. 图莫斯 USB2XXX 枚举。
2. 同星 TSMaster 枚举。
3. 图莫斯 LIN 手动 UDS 读版本。
4. 同星 LIN 手动 UDS 读版本。
5. 图莫斯完整 E68 LIN 刷写。
6. 同星完整 E68 LIN 刷写。
7. 每次硬件刷写保存完整日志。

## MVP 完成标准

第一版完成必须满足：

1. 软件启动后能扫描图莫斯和同星工具。
2. 能显示工具和通道列表。
3. 能按 Profile 推荐 LIN 通道，用户确认后连接。
4. 能手动发送和接收 CAN/LIN 报文。
5. 能执行基础 UDS 请求并显示解析结果。
6. 能选择 App 固件和 FlashDriver 固件。
7. 能执行完整 E68 LIN 刷写流程。
8. 刷写过程显示阶段、进度、耗时和失败原因。
9. 所有 TX/RX 和 UDS 步骤实时显示并自动保存日志。
10. 纯逻辑测试和模拟总线测试通过。
11. 至少完成一次图莫斯或同星硬件完整刷写验证。

