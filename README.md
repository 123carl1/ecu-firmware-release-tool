# 统一 CAN/LIN 上位机

本仓库用于实现统一 CAN/LIN 上位机，目标是在 Windows 下统一支持图莫斯 USB2XXX 和同星 TSMaster 工具。

第一版目标：

1. 扫描 PC 侧 USB 工具和可用 CAN/LIN 通道。
2. 通过界面选择 Profile 和活动通道。
3. 支持 CAN/LIN 手动收发和实时日志。
4. 支持基础 UDS 诊断。
5. 支持 E68 LIN Bootloader 完整刷写闭环。

设计文档：

- `docs/specs/2026-04-26-unified-can-lin-host-tool-design.md`

约束：

1. 第一版使用 Python + PySide6。
2. 第一版一次只连接一个活动通道。
3. 不自动安装依赖。
4. 不把 E68 项目参数写死到底层工具适配器。

