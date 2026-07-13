# Task 5 Report

## 状态

完成会话级内容寻址存储。`put` 与 `load` 均从实际文件复算检查制品或签名制品，JSON 中的 ID、hash、段表和路径不作为可信输入。

## 核心约束

- `ArtifactStore(root, sign_policy=..., verification_key=...)` 不保存开发密钥。
- SignedArtifact 的写入和加载均要求注入由已验证 manifest 创建的签名策略及验证密钥。
- 内容按 64 位小写十六进制 ID 分目录保存；绝对路径、盘符、UNC、`..`、符号链接和 resolve 逃逸均拒绝。
- 同 ID 相同内容幂等；同 ID 不同元数据或文件拒绝。
- 临时目录完成文件 flush/fsync 后原子替换；成功替换后不会清理当前目录。

## 验证

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/release/test_as5pr_signer.py tests/release/test_artifact_store.py -q
# 45 passed（当时检查点）

python -m pytest tests/release -q
# 134 passed（当时检查点）
```

后续任务增加用例后的最新数量以第一阶段总报告为准。

## 关注点

- 本存储是单会话本地一致性边界，不替代操作系统权限、安装包签名或量产密钥设施。
- Windows 符号链接真实集成验证仍依赖具备创建符号链接权限的环境；路径解析逻辑由单元测试和 manifest 既有测试覆盖。
