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

本地提交：`dd6b448`（提交 amend 后最终 hash 以交付消息为准）。

## Concerns

- 当前接口只提供开发 HMAC 签名核心；跨进程内容寻址存储属于 Task5，不在本任务范围。
- 未导出到 release 包顶层；调用方可从 `release.as5pr_signer` 显式导入，避免扩大现有公共入口。
