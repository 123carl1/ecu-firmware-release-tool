# M1 UI Alpha 验收记录

## 范围

本记录用于收口 M1 UI Alpha。当前验收对象是 `feature/m0-e68-lin-flash-cli` 分支上的 PySide6 Fake UI Alpha，不包含真实同星 TSMaster 或图莫斯 USB2XXX 硬件接入。

## 已验收能力

1. PySide6 UI 可以启动。
2. `--smoke` 可以在 `QT_QPA_PLATFORM=offscreen` 下构造主窗口并稳定退出。
3. 主窗口包含左侧 Profile/后端/设备树/连接状态，右侧总线收发、UDS 诊断、E68 刷写、配置摘要 Tab，底部常驻 Trace Log。
4. Fake 扫描可以返回 Fake TSMaster 和 Fake USB2XXX，每个工具至少有一个 LIN 通道。
5. Fake LIN 通道可以连接。
6. UDS 页发送 `10 01` 可以得到 `50 01`。
7. UDS 页手动请求会产生 LIN `0x3C` TX 和 `0x3D` RX trace，并写入日志。
8. E68 刷写页使用 fixture 可以跑到 `FLASH SUCCESS`。
9. Fake 刷写 trace 可以实时追加到底部 Trace Log。
10. Worker 运行不在 QApplication UI 线程内。
11. 窗口关闭时会对当前 active `QThread` 做 `quit()` + `wait()` 收尾。
12. 手动 UDS 运行期间会禁用刷写按钮，避免 UI 层出现可预防的并发 busy 提示。
13. `TraceLogger` 文件名已包含微秒和自增后缀，避免同秒覆盖。

## 自动化验证

执行环境：

```powershell
$env:PYTHONPATH='src'
$env:QT_QPA_PLATFORM='offscreen'
```

全量测试：

```powershell
$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; python -m unittest discover -s tests -v
```

结果：

```text
Ran 37 tests
OK
```

UI smoke：

```powershell
$env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'; python -m unified_can_lin_host_tool.cli.ui --smoke
```

结果：

```text
UI SMOKE OK
```

空白检查：

```powershell
git diff --check
```

结果：

```text
通过
```

工作区状态：

```powershell
git status --short --branch
```

结果：

```text
工作区干净
```

说明：PySide6 当前会打印字体目录警告，但相关命令退出码为 0。该问题不影响 M1 Alpha，后置到打包发布阶段处理。

## 真实窗口验证

执行方式：

1. 正常 Qt 窗口启动，不设置 `QT_QPA_PLATFORM=offscreen`。
2. 点击 `扫描`。
3. 选择 Fake TSMaster 的 `LIN 0`。
4. 点击 `连接`。
5. 在 UDS 页发送 `10 01`。
6. 确认响应区出现 `50 01`。
7. 确认 Trace Log 出现 `id=0x3C` 和 `id=0x3D`。
8. 在 E68 刷写页点击 `使用测试 fixture`。
9. 点击 `开始刷写`。
10. 确认阶段日志出现 `FLASH SUCCESS`。
11. 关闭窗口，确认 active threads 已收尾。

结果：

```text
UI FLOW TRACE OK
OK scan devices
OK connect fake lin
OK uds response
OK uds tx trace
OK uds rx trace
OK fake flash success
OK flash trace rx
OK threads stopped
```

截图路径：

```text
logs/ui_flow_m1_alpha.png
```

截图说明：

1. 左侧设备树显示 Fake TSMaster 和 Fake USB2XXX。
2. E68 刷写页进度到 100%。
3. 阶段日志显示 `FLASH SUCCESS`。
4. 底部 Trace Log 显示 LIN `0x3C` / `0x3D` TX/RX。

## 未覆盖项

1. 未验证真实 TSMaster 硬件扫描、通道映射和 LIN 收发。
2. 未验证真实 USB2XXX 硬件扫描、通道映射和 LIN 收发。
3. 未验证真实 E68 ECU 刷写。
4. 未验证真实硬件阻塞调用下的取消、关闭确认和线程中断。
5. 未实现 CAN 通用收发能力。
6. 未实现 Profile 图形化编辑。
7. 未处理 Qt 字体目录警告和发布打包资源。
8. 未做 Windows 安装包、快捷方式、版本信息和发布流程。

## M2 优先项

1. 真实 TSMaster/USB2XXX UI 后端接入。
2. 真实通道映射参数在 UI 中展示和选择。
3. 真实硬件阻塞调用的协作式取消，或刷写/诊断运行中关闭窗口确认。
4. 错误分类展示：设备错误、传输错误、UDS NRC、刷写流程错误、文件/Profile 错误。
5. 真实总线 trace 与 UI Trace Log 对齐。
6. 真实硬件到位后的台架验证记录。

## 分支建议

当前分支可以作为 M1 Alpha 代码基线。

建议默认先保留 `feature/m0-e68-lin-flash-cli` 分支和 worktree，作为后续 M2 真实硬件接入的起点；若需要主线同步，再合并回 `master`。
