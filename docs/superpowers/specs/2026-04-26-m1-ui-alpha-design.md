# M1 UI Alpha 设计

## 背景

当前 M0 已经具备核心刷写闭环：

1. E68 Profile 加载和校验。
2. E68 CRC32、App Level1 SeedKey、Boot FBL SeedKey。
3. BIN 镜像读取、分块和擦除长度对齐。
4. `LinDiagTransport`、`BusSession DIAG_EXCLUSIVE`、`FlashWorkflow`。
5. Fake dry-run CLI 和文本 Trace Log。
6. TSMaster probe 和基础 LIN 适配器。

当前没有真实硬件。下一步不应卡在真实同星/图莫斯验证上，而是先做一个可演示、可操作、可验证的工程师风格 UI Alpha。UI Alpha 使用 Fake 后端跑通扫描、连接、UDS、刷写和日志，后续硬件到位后替换为 TSMaster/USB2XXX 适配器。

## 目标

M1 UI Alpha 的目标是让用户打开软件后能看到真实上位机形态，并能在没有硬件的情况下点完整个工作流。

做完后必须满足：

1. 能启动 PySide6 主窗口。
2. 主界面是工程师工具风格，不做营销页、欢迎页或大面积装饰。
3. 点击扫描后能出现 Fake 同星、Fake 图莫斯和 LIN 通道。
4. 能选择 `E68 LIN Bootloader` Profile。
5. 能连接 Fake LIN 通道。
6. UDS 诊断页能发送 `10 01` 并显示 `50 01`。
7. E68 刷写页能选择或默认使用测试 fixture，执行 fake 刷写并显示阶段、进度和结果。
8. 底部 Trace Log 实时显示 TX/RX、阶段和错误，并保存日志文件。
9. 扫描、UDS、刷写都在 Worker 线程执行，UI 不冻结。

## 非目标

M1 UI Alpha 不做：

1. 真实硬件刷写保证。
2. USB2XXX 完整适配。
3. CAN 通用收发完整能力。
4. Profile 图形化编辑器。
5. 复杂主题、皮肤、动效。
6. DBC/LDF 解析。
7. 日志回放。
8. 打包安装程序。

## 界面风格

选择工程师工具风格：

1. 信息密度高，布局稳定。
2. 优先清晰状态、参数和日志，不做大图、卡片堆叠、渐变背景。
3. 主色保持克制，状态色只用于成功、运行、警告、失败。
4. 按钮使用明确动词，例如 `扫描`、`连接`、`发送`、`开始刷写`、`取消`、`清空日志`。
5. 表格和列表用于设备、通道和 Trace，不用过大的卡片。
6. 所有危险动作，例如真实刷写，在 Alpha 中仍默认禁用或要求显式切换后端。

## 主窗口布局

使用 PySide6 `QMainWindow`。

整体结构：

1. 顶部：菜单或轻量工具栏
   - `文件`
   - `工具`
   - `帮助`
   - Alpha 阶段可以只保留占位菜单。

2. 左侧固定面板：设备与 Profile
   - Profile 下拉框。
   - 后端模式选择：`Fake`、后续预留 `TSMaster`、`USB2XXX`。
   - `扫描` 按钮。
   - 设备/通道树。
   - `连接` / `断开` 按钮。
   - 当前状态：未扫描、已扫描、已连接、忙、错误。

3. 右侧主区域：Tab
   - `总线收发`
   - `UDS 诊断`
   - `E68 刷写`
   - `配置摘要`

4. 底部 Trace Log
   - 常驻，不随 Tab 切换消失。
   - 显示时间、方向、总线、ID、数据、备注。
   - 按钮：`清空`、`打开日志目录`。

布局建议：

1. 左侧宽度默认 280px，可拖拽。
2. 底部日志高度默认 220px，可拖拽。
3. 主区域随窗口缩放。
4. 所有表格列有最小宽度，避免文字重叠。

## Tab 设计

### 总线收发

Alpha 阶段只做 Fake 基础能力：

1. 总线类型选择：`LIN`，CAN 先禁用或标记未实现。
2. LIN ID 输入，默认 `0x3C`。
3. 数据输入，例如 `02 02 10 01 FF FF FF FF`。
4. `发送` 按钮。
5. 接收区显示 Fake 响应。

这个 Tab 的意义是验证 UI 与日志链路，不追求真实总线完整能力。

### UDS 诊断

用于手动诊断请求：

1. UDS Payload 输入，例如 `10 01`。
2. 自动使用当前 Profile 的 NAD、request_id、response_id。
3. `发送` 按钮。
4. 响应显示原始 payload 和简要解析：
   - 正响应 SID。
   - NRC 码和含义。
   - 超时或传输错误。

Alpha 必须支持：

1. `10 01 -> 50 01`
2. `10 03 -> 50 03`
3. `27 01 -> 67 01 + seed`

### E68 刷写

刷写页是 Alpha 的主流程。

控件：

1. FlashDriver 文件路径。
2. App 文件路径。
3. `使用测试 fixture` 按钮。
4. 文件摘要：
   - 起始地址。
   - 大小。
   - CRC32。
   - 擦除长度。
5. `开始刷写` 按钮。
6. `取消` 按钮。
7. 阶段显示：
   - App 默认会话。
   - App 扩展会话。
   - App Level1 解锁。
   - App 预编程检查。
   - 进入 Boot。
   - Boot FBL 解锁。
   - FlashDriver 下载。
   - FlashDriver 检查。
   - App 擦除。
   - App 下载。
   - App 完整性检查。
   - ECU 复位。
8. 进度条：
   - Alpha 阶段按阶段和 `$36` 块数粗略更新。
   - 不要求精确到真实耗时。

Alpha 默认使用 Fake 后端。真实后端按钮可以显示但禁用，避免误触真实刷写。

### 配置摘要

显示当前 Profile：

1. name。
2. LIN baudrate。
3. NAD。
4. request_id / response_id。
5. App 地址范围。
6. FlashDriver RAM 地址和最大长度。
7. P2、P2*、frame gap、poll gap。
8. SeedKey 算法名。

这个页面只读，不做编辑。

## Fake 后端

M1 UI Alpha 必须有独立 Fake 后端，不能把 UI 直接绑死到 CLI。

Fake 后端职责：

1. 扫描：
   - 返回 `Fake TSMaster`。
   - 返回 `Fake USB2XXX`。
   - 每个工具至少提供一个 LIN 通道。
2. 连接：
   - 记录当前活动 Fake 通道。
   - 返回连接成功事件。
3. UDS：
   - 使用现有 `FakeLinAdapter` + `LinDiagTransport`。
   - 支持常用请求的正响应。
   - 支持构造 NRC 和超时用于 UI 错误显示测试。
4. 刷写：
   - 使用 `FakeLinAdapter.for_e68_flash_success()` + `FlashWorkflow`。
   - 发送的 TX/RX 进入 Trace Log。

Fake 后端不是临时代码。它后续继续作为 UI 自动化测试和演示模式使用。

## 线程模型

UI 线程只做：

1. 渲染界面。
2. 收集用户操作。
3. 接收 Worker signal。
4. 更新状态、进度和日志。

以下操作必须在 Worker 线程执行：

1. 扫描设备。
2. 连接/断开设备。
3. 手动 UDS 请求。
4. E68 刷写流程。

Worker 统一输出事件：

1. `started`
2. `progress`
3. `trace`
4. `result`
5. `failed`
6. `cancelled`

刷写 Worker 启动前必须进入 `DIAG_EXCLUSIVE`。刷写运行中禁用手动收发和手动 UDS。取消请求只能在安全停止点生效，Alpha 可先实现“请求取消后禁用新动作，当前步骤完成后停止”。

## 数据流

### 启动

1. `main.py` 创建 `QApplication`。
2. `MainWindow` 加载内置 Profile 列表。
3. 默认选择 `profiles/e68_lin_bootloader.yaml`。
4. UI 状态为 `未扫描`。

### 扫描

1. 用户点击 `扫描`。
2. UI 创建 `DeviceScanWorker`。
3. Worker 调用 Fake 后端。
4. Worker 返回设备/通道列表。
5. UI 更新左侧设备树。

### 连接

1. 用户选择设备通道。
2. 点击 `连接`。
3. UI 创建 `ConnectWorker`。
4. Worker 建立 Fake session。
5. UI 状态变为 `已连接`。

### UDS

1. 用户输入 UDS Payload。
2. 点击 `发送`。
3. UI 创建 `UdsWorker`。
4. Worker 调用 `LinDiagTransport.request()`。
5. Worker 发送 TX/RX trace 和结果。
6. UI 更新响应区和 Trace Log。

### 刷写

1. 用户选择 FlashDriver 和 App，或点击使用 fixture。
2. UI 解析文件摘要。
3. 用户点击 `开始刷写`。
4. UI 创建 `FlashWorker`。
5. Worker 申请 `DIAG_EXCLUSIVE` 并执行 `FlashWorkflow`。
6. Worker 发出阶段、进度、TX/RX、完成或失败事件。
7. UI 更新阶段列表、进度条和 Trace Log。

## 模块边界

建议新增文件：

1. `src/unified_can_lin_host_tool/ui/app.py`
   - PySide6 应用入口。
   - 创建 `QApplication` 和 `MainWindow`。

2. `src/unified_can_lin_host_tool/ui/main_window.py`
   - 主窗口和布局。
   - 只负责组装控件和响应 signal。

3. `src/unified_can_lin_host_tool/ui/models.py`
   - UI 展示用数据类。
   - Device、Channel、ConnectionState、FlashStage。

4. `src/unified_can_lin_host_tool/ui/workers.py`
   - `DeviceScanWorker`
   - `ConnectWorker`
   - `UdsWorker`
   - `FlashWorker`

5. `src/unified_can_lin_host_tool/backends/fake_backend.py`
   - Fake 扫描、连接、UDS、刷写后端。
   - 封装现有 Fake adapter 和工作流。

6. `src/unified_can_lin_host_tool/cli/ui.py`
   - 启动 UI 的命令行入口。

7. `tests/test_ui_fake_backend.py`
   - 不依赖真实 PySide6 界面的后端测试。

8. `tests/test_ui_worker_contract.py`
   - Worker 事件契约测试。
   - 如果 PySide6 未安装，UI 层测试可以跳过，但 fake backend 测试必须运行。

## 依赖策略

PySide6 是 M1 UI Alpha 的新依赖。

规则：

1. 不自动安装 PySide6。
2. 实现前先检查 `python -c "import PySide6"`。
3. 若缺失，停止并询问是否允许安装。
4. 若允许安装，优先写入 `requirements-ui.txt`，再由用户确认安装命令。
5. M0 核心测试不能依赖 PySide6，避免没有 GUI 依赖时核心测试跑不了。

## 错误处理

UI 错误必须分类显示：

1. Profile 错误。
2. 文件错误。
3. 设备错误。
4. 传输错误。
5. UDS 错误。
6. 刷写状态错误。

错误显示规则：

1. 状态栏显示简短错误。
2. Trace Log 写入完整错误。
3. 刷写页显示失败阶段。
4. 失败后恢复按钮状态。
5. Fake 后端提供超时和 NRC 模拟入口，便于验证 UI 错误显示。

## 测试策略

必须测试：

1. Fake 后端扫描返回两类工具和 LIN 通道。
2. Fake 连接状态迁移。
3. Fake UDS `10 01 -> 50 01`。
4. Fake 刷写流程能完成并输出阶段事件。
5. Worker 不在 UI 线程内直接执行耗时逻辑。
6. 刷写运行中 UDS/手动发送按钮禁用。
7. Trace Log 能接收 Worker 事件并追加显示。

验证命令：

```powershell
$env:PYTHONPATH="src"; python -m unittest discover -s tests -v
```

若 PySide6 已安装，增加 UI smoke：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unified_can_lin_host_tool.cli.ui --smoke
```

手工验收：

1. 打开 UI。
2. 点击扫描。
3. 选择 Fake LIN。
4. 点击连接。
5. 在 UDS 页发送 `10 01`。
6. 在 E68 刷写页使用 fixture 执行刷写。
7. 确认底部 Trace Log 持续更新。
8. 确认日志文件保存到 `logs/`。

## 推进顺序

1. 环境门禁：检查 PySide6。
2. 实现 Fake UI backend。
3. 实现 Worker 事件模型。
4. 实现主窗口布局。
5. 接入扫描和连接。
6. 接入 UDS 页面。
7. 接入 E68 刷写页面。
8. 接入 Trace Log。
9. 做 offscreen smoke 和手工运行。

## 风险

1. PySide6 未安装会阻塞 UI 实现。
2. Windows Qt 插件路径问题可能导致启动失败。
3. 如果 UI 直接调用刷写流程，会阻塞界面；必须用 Worker。
4. 如果 Fake 后端与真实后端接口不一致，后续接硬件会返工；因此 Alpha 也要走后端接口。
5. 真实硬件未验证前，界面只能声明 Fake 演示通过，不能声明真实刷写可用。

## 结论

M1 UI Alpha 应先做“工程师工具风格 + Fake 后端完整演示”。这样即使没有硬件，也能验证上位机形态、线程模型、日志链路和刷写状态机的 UI 表达。真实硬件到位后，只需要把后端从 Fake 切到 TSMaster/USB2XXX，并做总线级调试验证。
