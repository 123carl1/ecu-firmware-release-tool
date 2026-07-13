# Task 4 实施报告

## 状态

完成 AS5PR `SignedArtifact` 固定字节契约、完整复验和同目录原子写出；仅修改 Task4 指定源码、测试和本报告。

## TDD 证据

- RED：`$env:PYTHONPATH='src'; python -m pytest tests/release/test_as5pr_signer.py -q`
  - 结果：收集失败，`ModuleNotFoundError: unified_can_lin_host_tool.release.as5pr_signer`。
- GREEN：实现后同命令结果 `18 passed in 0.08s`。
- 首轮 GREEN 暴露测试对空 key 的空字符串判断恒失败；仅修正测试断言，32 字节生产约束未放宽。

## 验证

- signer 专项：`18 passed in 0.08s`。
- release 回归：`106 passed in 0.90s`。
- `git diff --check`：通过，无输出。
- 覆盖参考向量、错误 HMAC、认证头各字段、key 长度、artifact/policy identity、派生 hash/ID、防密钥泄露、冻结模型、原子写成功及复验失败保留旧目标。

## 自审

- HMAC 输入严格为 `normalizedPayload || authHeader`，认证块固定 48 字节。
- `verify_as5pr` 从原 artifact 和 policy 重建期望结果，并用 `hmac.compare_digest` 比较所有字节、hash 和 ID。
- 临时文件位于目标同目录，写入后 `fsync`、复读、复验，最后才 `os.replace`；异常路径清理临时文件并保留旧目标。
- key 不进入不可变对象、repr 或异常文本。

## Commit

初始本地提交：`f51a0df`。

## Concerns

- 当前接口只提供开发 HMAC 签名核心；跨进程内容寻址存储属于 Task5，不在本任务范围。
- 未导出到 release 包顶层；调用方可从 `release.as5pr_signer` 显式导入，避免扩大现有公共入口。

## 独立评审阻塞项修复

- RED：新增 manifest 工厂、禁止手工 policy、source/segments/normalized payload/identity segments 伪造测试；首次运行 `26 failed`，明确暴露工厂缺失和内部一致性未校验。
- 修复：`As5prSignPolicy.from_verified_manifest()` 从 `ReleaseManifest` 派生 targetId、formatVersion、magic、signPolicyId、bundleId 和 manifest 内容 hash，并生成不可伪改的绑定摘要；公开构造拒绝绕过工厂。
- 修复：签名和复验共用完整 artifact 校验，重新规范化 segments，并交叉核对 source hash、segments、payload、payload hash、identity 和 ArtifactId。
- GREEN：signer 专项 `26 passed in 0.18s`；release 回归 `114 passed in 1.02s`；`git diff --check` 通过。
- 修复提交：待本次本地提交后，以交付消息中的最终 hash 为准。


## 第二轮信任根与 TOCTOU 修复

- RED：manifest+signer 专项因缺少 `VerifiedReleaseManifest` 在收集阶段失败；新增普通 manifest、外部任意 hash、源变化/删除及快照离线复验测试。
- Manifest：增加模块私有 token 保护的 `VerifiedReleaseManifest`；仅 `load_verified_manifest` 在 Ed25519、schema 和全部资源校验完成后创建并返回。
- Policy：`from_verified_manifest` 只接受上述已验证类型，删除外部 `manifest_bundle_sha256` 参数，直接对已验签原始 `manifest_bytes` 计算 SHA-256。
- TOCTOU：`sign_as5pr` 和 `write_signed_as5pr` 在签名/写出前调用 `revalidate_source`；`verify_as5pr` 仍允许源文件不存在时验证不可变快照。
- GREEN：manifest+signer 专项 `62 passed in 0.63s`；release 回归 `119 passed in 0.68s`；`git diff --check` 通过。
- 修复提交：待本次本地提交后，以交付消息中的最终 hash 为准。
