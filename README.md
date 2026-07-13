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

## 发布制品核心阶段边界

当前 `release/` 包已提供第一阶段离线核心能力：

1. 严格解析 BIN、Intel HEX、Motorola S-record，并生成不可变分段镜像。
2. 校验 Ed25519 签名的 release manifest、固定资源 hash 与资源路径边界。
3. 计算可复算的 `ArtifactId`，并通过会话级内容寻址存储阻止检查后替换输入。
4. 按 AS5PR Bootloader 契约生成和复验 `normalizedPayload || authHeader` HMAC。
5. 只有 `SignedArtifact` 认证成功且 manifest 允许时，才生成带有效 AppValid 的离线完整镜像；同时输出并回读核对 HEX/S19。

该阶段尚未把新核心接入 GUI、CLI 或现有 E68/AS5PR OTA 工作流，也未授权 GUI 使用该核心执行真实擦除或刷写。在线 ECU 身份探测、App/Boot 双入口、取消状态机、Windows 安装包和两项目实机门禁属于后续阶段。

