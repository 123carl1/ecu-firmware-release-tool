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
8. 用户主动取消和关闭窗口触发取消时，UI 显示“已取消”，不显示为 `ERROR`。
9. 关闭窗口时会先请求后台 Worker 取消，再进行线程收尾。
10. 缺 DLL/缺设备类错误能按分类显示，不崩溃。

## 自动化验证

执行环境：

```powershell
$env:PYTHONPATH="src"
$env:QT_QPA_PLATFORM="offscreen"
```

全量测试：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unittest discover -s tests -v
```

结果：

```text
Ran 56 tests in 1.384s
OK
```

UI smoke：

```powershell
$env:PYTHONPATH="src"; $env:QT_QPA_PLATFORM="offscreen"; python -m unified_can_lin_host_tool.cli.ui --smoke
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
## feature/m0-e68-lin-flash-cli
```

说明：PySide6 当前仍会打印字体目录警告，但命令退出码为 0。该问题仍后置到打包发布阶段处理。

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
4. 记录真实硬件错误码和 `HostToolError` 分类映射。
5. 台架验证刷写取消、掉电、CRC 失败和 Boot 停留恢复策略。
