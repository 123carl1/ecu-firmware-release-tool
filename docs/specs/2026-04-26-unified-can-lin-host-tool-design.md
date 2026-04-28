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
   - `WorkerController`：管理扫描、接收轮询、UDS 和刷写任务的后台线程生命周期。

3. 总线抽象层
   - `BusAdapter` 接口：枚举设备、打开通道、关闭通道、发送底层帧、接收底层帧。
   - `LinChannel` / `CanChannel` 通道模型。
   - `BusFrame` 统一帧模型。
   - `LinDiagTransport`：封装 LIN 诊断 `0x3C/0x3D` 的请求组帧、连续帧发送和响应轮询。
   - `CanUdsTransport`：预留 CAN UDS 传输层，不在第一版实现完整 CAN 刷写。

4. 厂家适配层
   - `Usb2xxxAdapter`：封装 `USB2XXX.dll`。
   - `TsmasterAdapter`：封装 `TSMaster.dll`。
   - 厂家 DLL 路径和默认参数来自配置，不写死在业务流程里。

## 线程模型

PySide6 UI 线程只负责渲染、收集用户操作和接收事件，不允许直接执行可能阻塞的硬件操作或刷写流程。

后台任务必须放入 Worker 线程：

1. 设备扫描
   - 加载 DLL、枚举 USB 工具、读取设备信息都在扫描 Worker 中执行。
   - UI 线程只发起扫描请求，并接收 `device_scan_started`、`device_found`、`device_scan_finished`、`device_scan_failed` 事件。

2. 连接和接收轮询
   - 打开通道、配置波特率、启动 LIN/CAN 通道在连接 Worker 中执行。
   - 连接成功后由接收 Worker 持续轮询当前活动通道。
   - 接收 Worker 将原始帧、错误和状态变化通过 Qt signal 或线程安全队列送回 UI。
   - LIN 诊断或刷写占用当前通道时，通用接收 Worker 必须暂停该通道的读取；`0x3D` 响应轮询只能由 `LinDiagTransport` 管理，避免普通接收线程抢走诊断响应。

3. UDS 请求
   - 手动 UDS 请求在 UDS Worker 中执行。
   - UI 只提交请求参数，并接收请求开始、TX/RX、解析结果、超时或失败事件。
   - 同一时间只允许一个 UDS 请求占用当前活动通道，避免和刷写流程抢占 `0x3D` 响应。
   - 对 LIN 通道，UDS Worker 必须先请求 `BusSession` 进入 `DIAG_EXCLUSIVE` 状态；进入失败则拒绝本次请求。

4. 刷写流程
   - `FlashWorkflow` 必须运行在刷写 Worker 中。
   - `FlashWorkflow` 启动前必须由 `BusSession` 成功进入 `DIAG_EXCLUSIVE` 状态；退出、失败或取消收尾时必须释放该独占状态。
   - UI 只接收阶段、进度、TX/RX、错误、完成、取消结果。
   - 刷写进行中禁止手动收发和手动 UDS 操作，除非先请求取消并等待 Worker 进入安全停止点。

5. 日志
   - Worker 产生结构化事件。
   - `TraceLogger` 负责把事件写入文件并转发给 UI。
   - UI 不直接从硬件线程读取状态。

取消策略：

1. UI 可以请求取消扫描、手动 UDS 和刷写。
2. 取消不是强杀线程，而是设置取消标志。
3. 刷写 Worker 只能在安全停止点退出：
   - UDS 请求完成后。
   - 当前 `$36` 块收到响应后。
   - `$31 FF00` 擦除完成或超时后。
4. 已进入 App 擦除或 App 下载阶段时，取消后 UI 必须显示“ECU 可能停留在 Boot，需要重新刷写或重新上电后恢复”，不能提示已恢复到 App。
5. Worker 异常退出时必须关闭当前通道或标记通道不可用，并写入日志。

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
    workers.py
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

## LIN 诊断传输抽象

`BusAdapter.send_frame()` / `receive_frame()` 只能表示底层原始帧能力，不能直接承载 E68 LIN UDS 刷写语义。LIN 诊断必须通过独立的 `LinDiagTransport`。

`LinDiagTransport` 的职责：

1. 根据 Profile 读取 LIN 诊断参数：
   - NAD：从 Profile 读取，E68 默认值为 `0x02`
   - 请求 ID：从 Profile 读取，E68 默认值为 `0x3C`
   - 响应 ID：从 Profile 读取，E68 默认值为 `0x3D`
   - 校验和：诊断帧使用 Classic checksum
   - poll timeout
   - poll gap
   - frame gap
   - P2/P2*

2. 请求组帧：
   - 单帧：`[NAD] [PCI=len] [UDS...] [0xFF padding]`
   - 首帧：`[NAD] [0x10 | lenHigh] [lenLow] [UDS 前 5 字节]`
   - 连续帧：`[NAD] [0x20 | SN] [后续最多 6 字节] [0xFF padding]`
   - 连续帧 SN 从 1 开始，低 4 位递增。

3. 请求发送：
   - 通过底层适配器发送 `0x3C` LIN 数据帧。
   - 每帧间隔使用 Profile 的 `frame_gap_ms`。
   - 发送过程产生日志事件：`LIN_DIAG_TX_FRAME`。

4. 响应轮询：
   - 主机调度 `0x3D` 响应帧。
   - 轮询直到收到匹配 NAD 和有效 PCI 的响应，或超时。
   - 当前 Boot/App 响应均为单帧，UDS 响应载荷最大 6 字节。
   - 必须支持 `0x7F <sid> 0x78`：返回给上层作为 ResponsePending，而不是普通失败。
   - 轮询过程产生日志事件：`LIN_DIAG_RX_FRAME`、`LIN_DIAG_TIMEOUT`。

5. 响应解析：
   - 校验 NAD。
   - 校验 PCI 长度。
   - 提取 UDS payload。
   - 识别正响应、负响应和 ResponsePending。

6. 互斥：
   - 同一活动通道同一时间只能有一个 `LinDiagTransport.request()` 在执行。
   - 刷写流程占用该传输层期间，UI 手动 UDS 和手动 LIN 诊断发送必须禁用。

建议接口：

```python
class LinDiagTransport:
    def request(
        self,
        uds_payload: bytes,
        *,
        expect_sid: int | None = None,
        expect_prefix: bytes | None = None,
        timeout_ms: int | None = None,
        allow_response_pending: bool = False,
        cancel_token: CancelToken | None = None,
    ) -> UdsResponse:
        ...
```

底层适配器只需要提供两类 LIN 原语：

1. 发送请求帧到指定 ID。
2. 调度并读取指定响应 ID。

厂家差异由适配器内部处理：

1. USB2XXX 可以优先使用官方 `LIN_UDS_Request` / `LIN_UDS_Response`，但仍要把 TX/RX 和超时转换成统一事件。
   - 如果官方 UDS helper 不能暴露逐帧 TX/RX、frame gap、poll timeout、poll gap 或 `0x78` 轮询细节，第一版必须退回原始 LIN 帧发送和 `0x3D` 轮询实现。
2. TSMaster 使用主机发送 `0x3C`，再 `transmit_header_and_receive_msg` 轮询 `0x3D`。
3. 如果厂家 SDK 自动处理 checksum，适配器仍必须在日志里标记使用的是 Classic checksum 诊断帧。

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
  max_transfer_payload: 1024
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
   - 如果 App 已擦除或目标已人工停在 Bootloader，必须显式启用 `start_in_bootloader` / `--start-in-bootloader`，跳过步骤 1..5。
6. 等待 Boot 响应。上位机应在窗口期内轮询 Boot 编程会话请求。
7. Boot 编程会话：发送 `$10 02`，期望 `$50 02`。
8. Boot FBL 安全访问：发送 `$27 09` 获取 Seed，计算 Key 后发送 `$27 0A + Key`，期望 `$67 0A`。
9. FlashDriver 下载：
   - 发送 `$34 00 44 + flashDriverAddr32 + flashDriverSize32`。
   - 期望 `$74 20 04 02`，其中 `0x0402` 表示 `$36` UDS 请求总长最大 1026 字节。
   - 按 1024 字节以内数据区发送多次 `$36 blockSequence + data`；LIN TP 负责拆成 1 个首帧和若干连续帧。
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
   - 按 1024 字节以内数据区发送 `$36`，也就是每个完整 `$36` 请求 UDS 长度为 1026 字节。
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
5. `$36` 每块请求 UDS 负载长度最大为 `0x0402`，其中 2 字节为 SID 和块序号，数据区最多 1024 字节。
6. `$36` 块序号从 `0x01` 开始，`0xFF` 后回绕到 `0x00`。
7. `$37` CRC32 初始值为 `0xFFFFFFFF`，只覆盖 `$36` 数据区，不包含块序号。
8. App 擦除长度必须按 512B 页对齐。
9. App 下载长度可以非 4 字节整数，Boot 写入层最终用 `0xFF` 补齐最后不足 4 字节的数据。

## CRC32 口径

E68 LIN Bootloader 的 `$37` CRC32 必须逐字节复刻固件 `BootCrc32_Update()`，不能直接使用 Python `zlib.crc32()` 的默认口径。

源码依据：

1. 初值：`BOOT_CRC32_INIT_VALUE = 0xFFFFFFFF`
2. 更新公式：

```c
currentCrc = ((currentCrc >> 8U) & 0x00FFFFFFUL) ^
             table[(currentCrc ^ byte) & 0xFFUL];
```

3. 未看到 final xor，所以上位机发送 `$37` 时使用累加后的当前 CRC 值，不再异或 `0xFFFFFFFF`。
4. 覆盖范围：仅覆盖所有 `$36` 的数据区，不包含 SID `0x36`、块序号、NAD、PCI、LIN checksum。
5. 每个逻辑下载块单独计算：
   - FlashDriver 下载：从 `0xFFFFFFFF` 开始，覆盖 FlashDriver 镜像数据。
   - App 下载：重新从 `0xFFFFFFFF` 开始，覆盖 App 镜像数据。
6. `$37` 请求格式：`37 + crc32_be`，CRC32 使用大端字节序发送。
7. Boot 正响应：`77 + crc32_be`，上位机必须校验返回 CRC 与本地计算一致。

Python 端建议实现：

```python
def e68_boot_crc32_update(current_crc: int, data: bytes) -> int:
    crc = current_crc & 0xFFFFFFFF
    for value in data:
        crc = ((crc >> 8) & 0x00FFFFFF) ^ TABLE[(crc ^ value) & 0xFF]
        crc &= 0xFFFFFFFF
    return crc
```

必须加入的测试向量：

1. 数据 `b"123456789"` 从 `0xFFFFFFFF` 开始计算，结果必须为 `0x340BC6D9`。
2. 分段计算结果必须等于一次性计算结果。
3. 发送 `$37` 时不做 final xor。
4. CRC 只包含 `$36` 数据区，加入块序号时结果应不同，用于防误用测试。

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

## 刷写失败恢复策略

刷写失败后的 UI 不能只停在错误弹窗，必须给出下一步动作。恢复策略由失败阶段决定。

恢复状态分级：

1. `SAFE_IN_APP`
   - 失败发生在 App 预编程阶段，例如 `$10 01`、`$10 03`、`$27 01/02`、`$31 01 02 03`。
   - App 仍在运行，未写 Boot 请求标志，未擦除 App。
   - UI 允许直接重试预编程或退出。

2. `BOOT_NOT_ERASED`
   - 已通过 `$10 02` 进入 Boot，但尚未执行 App 擦除。
   - 可能已完成 Boot 编程会话、FBL 解锁或 FlashDriver 下载。
   - UI 允许重新执行 Boot 阶段流程。
   - 若 FlashDriver 已校验通过，允许从 App 擦除前继续；但第一版建议为了确定性，失败后默认重新下载并校验 FlashDriver。

3. `BOOT_APP_INVALID_OR_UNKNOWN`
   - 已发送 `$31 01 FF 00`，或 App 下载已经开始。
   - Boot 会先擦除 App 有效标志；此后失败、电源中断或取消都不能假设 App 可启动。
   - UI 必须提示“ECU 可能停留在 Boot，需重新刷写完整 App”。
   - UI 允许重新执行 Boot 编程会话、FBL 解锁、FlashDriver 下载、擦除、App 下载全流程。
   - 不允许提示“进入 App”或“刷写完成”。

4. `BOOT_APP_READY_RESET_PENDING`
   - `$31 01 FF 01` 已返回通过，但 `$11 01` 失败或复位后仍停留 Boot。
   - UI 应提供两个动作：
     - 重发 `$11 01`。
     - 如果重发失败，提示手动重新上电后再读 App 版本或观察业务帧。
   - 若重新上电后仍停 Boot，允许重新执行完整刷写流程。

5. `TRANSPORT_UNKNOWN`
   - DLL 崩溃、通道断开、LIN 长时间无响应、Worker 异常退出。
   - UI 必须关闭当前 BusSession 或标记通道不可用。
   - 用户需要重新扫描/连接。
   - 若失败发生在 App 擦除后，按 `BOOT_APP_INVALID_OR_UNKNOWN` 提示。

典型失败处理：

1. App 预编程检查失败
   - 显示检查结果和 NRC。
   - 允许直接重试 `$31 01 02 03` 或从 `$10 01` 重新开始。

2. `$10 02` 后等不到 Boot
   - 提示可能仍在 App、复位中、LIN 物理层异常或 Boot 未启动。
   - 允许重新轮询 Boot `$10 02`。
   - 允许重新上电后再扫描/连接。

3. FlashDriver 下载 `$37` CRC 失败
   - 当前 FlashDriver 逻辑块无效。
   - UI 必须重新发起 FlashDriver `$34/$36/$37`，不能直接进入 `$31 01 02 02`。

4. FlashDriver 检查失败
   - UI 必须重新下载 FlashDriver。
   - 若连续失败，提示检查 FlashDriver 文件、ABI、函数表和目标 RAM 地址。

5. App 擦除返回 `0x78` 后超时
   - App 有效标志可能已经被清除，App 区擦除状态未知。
   - UI 标记为 `BOOT_APP_INVALID_OR_UNKNOWN`。
   - 下一次只能走完整 Boot 刷写恢复流程。

6. App 下载 `$36` 中途失败或取消
   - App 镜像不完整。
   - UI 标记为 `BOOT_APP_INVALID_OR_UNKNOWN`。
   - 下一次必须重新擦除并重新下载 App。

7. App `$37` CRC 失败
   - Boot 会中止当前下载状态，App 有效标志不应被置位。
   - UI 标记为 `BOOT_APP_INVALID_OR_UNKNOWN`。
   - 下一次必须重新擦除并重新下载 App。

8. App 完整性检查失败
   - 可能是镜像地址、向量表、文件内容或下载数据错误。
   - UI 标记为 `BOOT_APP_INVALID_OR_UNKNOWN`。
   - 禁止发送 `$11 01` 作为进入 App 的动作。

9. `$11 01` 后未确认进入 App
   - 如果已收到 `$51 01`，先提示复位可能已发生，允许重新连接后读版本。
   - 若读 App 版本失败且 Boot 仍响应，标记为 `BOOT_APP_READY_RESET_PENDING` 或重新刷写。

恢复动作在 UI 上必须是明确按钮或菜单项：

1. `重试当前步骤`
2. `从 Boot 阶段重新刷写`
3. `重新扫描并连接`
4. `重新上电后继续`
5. `保存日志并停止`

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
10. 刷写失败恢复状态映射。
11. 取消请求只能在安全停止点生效。

模拟总线测试：

1. 正常完整刷写流程。
2. App 预编程检查返回失败。
3. Boot `$27` Key 错误。
4. FlashDriver `$37` CRC 错误。
5. `$31 FF00` 返回 `0x78` 后最终成功。
6. `$31 FF00` 返回 `0x78` 后最终超时。
7. `$36` 块序号错误。
8. LIN 响应超时。
9. App 擦除后断连，再次启动应提示完整 Boot 恢复。
10. App `$37` CRC 失败后禁止 `$11 01`。
11. `$11 01` 收到正响应但读 App 版本失败时进入复位待确认状态。
12. 刷写 Worker 取消请求在 `$36` 块响应后退出。

硬件验证：

1. 图莫斯 USB2XXX 枚举。
2. 同星 TSMaster 枚举。
3. 图莫斯 LIN 手动 UDS 读版本。
4. 同星 LIN 手动 UDS 读版本。
5. 图莫斯完整 E68 LIN 刷写。
6. 同星完整 E68 LIN 刷写。
7. 每次硬件刷写保存完整日志。

## 阶段交付

第一版总目标较大，实施时拆成 M0/M1/M2 三个阶段，避免一开始同时做 UI、双厂家适配、通用 CAN/LIN 和完整刷写。

### M0：单工具链刷写闭环

目标：先证明 E68 LIN 刷写状态机、CRC32、Seed/Key、LIN 诊断传输和失败恢复能跑通。

范围：

1. 可以无 UI，或只有极简命令行/极简窗口。
2. 只支持一个优先工具链。建议先选当前最容易验证的一套，例如图莫斯 USB2XXX 或同星 TSMaster。
3. 使用固定 YAML/Profile 文件或命令行参数加载 Profile，不要求 GUI 下拉选择。
4. 实现 `LinDiagTransport`，但不能把 E68 的 NAD、请求 ID、响应 ID、poll gap、frame gap 等参数硬编码进传输层；这些参数必须来自 Profile。
5. 实现 E68 Level1/FBL SeedKey。
6. 实现 `.bin` 输入；`.hex` 可放到 M1。
7. 实现 E68 LIN 完整刷写流程。
8. 保存文本日志。
9. 通过纯逻辑测试和一次真实硬件刷写。

M0 不要求：

1. 完整 PySide6 界面。
2. 双厂家适配。
3. CAN 通用能力。
4. Profile 图形化下拉管理。

### M1：工程师可用 GUI 版

目标：把 M0 的刷写闭环变成日常可用的工程师上位机。

范围：

1. PySide6 混合工程面板。
2. Worker 线程模型。
3. Profile 加载和界面下拉选择。
4. `.bin + .hex` 文件解析。
5. 实时 Trace Log + 自动保存。
6. 模拟总线测试。
7. 手动 UDS 诊断页。
8. 当前优先工具链的完整连接、收发、刷写。

M1 完成后，必须能稳定完成 E68 LIN 刷写，并且 UI 不冻结。

### M2：双厂家和通用能力

目标：补齐统一上位机能力。

范围：

1. 第二厂家适配器。
2. 图莫斯和同星都能扫描、连接、LIN UDS、E68 刷写。
3. CAN 手动收发。
4. 通用 LIN 手动收发。
5. CAN UDS 传输层预留或基础实现。
6. 后续 CAN Bootloader Profile 接入准备。

M2 完成后，才认为“统一图莫斯/同星 CAN/LIN 上位机第一版”整体达成。

## 整体完成标准

M2 完成后，整体上位机第一版必须满足：

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
12. 扫描、接收轮询、UDS 和刷写期间 UI 不冻结，取消请求能在定义的安全停止点生效。
13. E68 LIN 刷写只能通过 `LinDiagTransport` 执行，不允许直接用普通 `BusAdapter.send/receive` 拼刷写流程。
