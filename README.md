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

## 安装、版本与更新

从本仓库 GitHub Release 下载 `EcuReleaseTool_Setup_<版本>.exe` 安装。安装后可运行：

```powershell
EcuReleaseCLI.exe --version
```

GUI 可检查本仓库的稳定通道 GitHub 更新。客户端先取得最新标签，再从该标签下载并验证 `update.json`、64 字节 Ed25519 签名和安装包 SHA-256；更新不会绕过正在运行的扫描、诊断或 OTA 任务。

当前 Windows 安装包没有软件代码签名证书，安装时会出现“未知发布者”。更新信息的 Ed25519 签名只证明本工具取得的 GitHub 更新资源与发布密钥一致，不能替代 Windows 发布者代码签名。

## 第三方运行库与凭据边界

安装包内的 USB2XXX 运行库固定来自图莫斯官方示例仓库提交 `d1fd307a72cad0c71aa81db79ab413b8e7a26175`，文件来源、大小和 SHA-256 记录在 `THIRD_PARTY_NOTICES.txt`。当前上游仓库未发现明确再分发许可；公开分发可能存在授权风险，使用或再次分发前应由项目负责人确认权利边界。同星 DLL 不随安装包发布，使用同星适配器时应由用户按其授权方式自行安装 TSMaster 运行环境。

`development_keys.py` 中公开的固件包签名私有种子和 Boot HMAC 密钥仅限开发台架，用于可重复的联调样本。量产禁止使用这些公开凭据，量产密钥必须由独立的受控密钥体系生成、保存和轮换。自动更新发布私钥与这些台架凭据相互独立，不进入 Git、安装包、日志或构建产物。

本仓库未新增开源许可证。源码公开可查看不等于授予额外开源许可，第三方组件服从其各自权利；复制、修改或再分发前应分别确认适用授权。

## 发布制品核心阶段边界

当前 `release/` 包已提供第一阶段离线核心能力：

1. 严格解析 BIN、Intel HEX、Motorola S-record，并生成不可变分段镜像。
2. 校验 Ed25519 签名的 release manifest、固定资源 hash 与资源路径边界。
3. 计算可复算的 `ArtifactId`，并通过会话级内容寻址存储阻止检查后替换输入。
4. 按 AS5PR Bootloader 契约生成和复验 `normalizedPayload || authHeader` HMAC。
5. 只有 `SignedArtifact` 认证成功且 manifest 允许时，才生成带有效 AppValid 的离线完整镜像；同时输出并回读核对 HEX/S19。

该阶段尚未把新核心接入 GUI、CLI 或现有 E68/AS5PR OTA 工作流，也未授权 GUI 使用该核心执行真实擦除或刷写。在线 ECU 身份探测、App/Boot 双入口、取消状态机、Windows 安装包和两项目实机门禁属于后续阶段。

