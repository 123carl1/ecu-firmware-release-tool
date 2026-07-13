# 发布制品核心第一阶段验证报告

## 范围

本阶段实现签名 manifest、Segment 镜像模型、ArtifactId、AS5PR SignedArtifact、会话级内容寻址存储和带认证门禁的完整镜像合并。未接入在线 OTA、GUI、CLI 或 Windows 安装包。

## 环境

- 日期：2026-07-13
- 操作系统：Windows
- Python：3.14.0
- PyYAML：6.0.3
- cryptography：46.0.3（当前执行环境版本；项目声明的支持范围仍以 `pyproject.toml` 为准）

## 固定测试向量

- `ArtifactIdentityV1` 编码长度：192 bytes
- 固定 `ArtifactId`：`f83e3a7c2d5c1ef48ebc783b7acc870d6756f9315ea59d7ca1bde53ff43b467e`
- AS5PR payload：`0102ff04`
- AS5PR key：`00..1f` 共 32 bytes
- authHeader：`a5a5a5a5040000004135504100000000`
- authBlock：`a5a5a5a5040000004135504100000000deb77e27a4529351187b684cb11238518dd779bcc9f0d39b702f185150f99548`
- HMAC 输入：`normalizedPayload || authHeader`，末尾 32-byte HMAC 不参与计算。

## 验证命令与结果

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/release/test_artifact_store.py tests/release/test_composer.py -q
# 25 passed

python -m pytest tests/release -q
# 144 passed
```

```powershell
python -m pytest -q
# 253 passed
```

`git diff --check` 必须无输出。

## 已覆盖门禁

- manifest Ed25519 签名、严格 UTF-8、资源 hash、绝对路径、`..` 和符号链接逃逸。
- 原始文件、规范化 payload、段表、ArtifactId、SignedArtifactId、认证块与 manifest bundle hash 篡改。
- 内容寻址存储重新读取实际 source/signed 文件，不信任 JSON 中的 hash 和 ID。
- AS5PR magic、payloadSize、targetId、version、HMAC 和签名策略复验。
- 未授权离线预置、认证失败与 SignedArtifact 篡改时禁止有效 AppValid。
- 擦除态 AppValid、有效 AppValid 整页保留字节、Boot/AppValid/App 不重叠。
- HEX/S19 临时写入、重新解析和逐字节一致性检查。

## 未覆盖及外部前置条件

- E68 targetId、签名认证块和 Bootloader 身份 DID 尚缺同构建 release manifest 与 Bootloader 字节契约，禁止从 AS5PR 推导。
- 在线 ECU 身份 DID、CAN ID/NAD、请求响应关联、新鲜时间窗和 App/Boot 双入口尚未实现。
- 擦除、传输、认证阶段取消状态机及断电恢复尚未实现。
- 本阶段没有执行 E68 LIN 或 AS5PR CAN 实机 OTA；完整镜像也未用于实机离线烧录。
- Windows PyInstaller/Inno Setup/AuthentiCode 安装链尚未实现。
