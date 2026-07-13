# 发布资源包与项目发布配置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用程序内不可变项目配置和单一 `.erel` 文件替换旧分散参数/资源入口，并让检查、认证、合并和会话存储绑定同一资源集合。

**Architecture:** `release/project_config.py` 只定义项目常量和规范摘要；`release/package.py` 只负责二进制编解码与 Ed25519；`release/build_identity.py` 只解析三镜像构建身份；`release/application_service.py` 负责导入后的会话生命周期。现有镜像解析器和 AS5PR HMAC 算法保留，但输入改为已验证资源对象。

**Tech Stack:** Python 3.11、dataclasses、struct、hashlib/hmac、cryptography Ed25519、pytest、PowerShell。

## Global Constraints

- 不提供外部项目参数加载入口；用户只能选择 E68 或 AS5PR。
- 新代码、目录、接口、CLI 和 UI 不使用已废弃的两类旧配置概念名称。
- `.erel` V1 固定头300字节，资源顺序 Boot/App/FlashDriver，签名尾72字节。
- E68 在可信资源和身份 DID 完成前禁止真实刷写。
- 所有输出先写同目录临时文件，回读验证后原子替换。
- 每个任务先写失败测试，再写最小实现。

---

### Task 1: 内置项目发布配置与规范摘要

**Files:**
- Create: `src/unified_can_lin_host_tool/release/project_config.py`
- Create: `tests/release/test_project_config.py`

**Interfaces:**
- Produces: `ProjectCode`, `ProjectReleaseConfig`, `get_project_config(project: ProjectCode)`, `canonical_config_bytes(config)`, `compute_config_digest(config)`。

- [ ] **Step 1: 写失败测试**

```python
def test_as5pr_config_has_distinct_app_and_flash_driver_targets():
    cfg = get_project_config(ProjectCode.AS5PR)
    assert cfg.project_code == 0x41503541
    assert cfg.app_target_id == 0x41503541
    assert cfg.flash_driver_target_id == 0x46503541

def test_config_digest_is_stable_and_rejects_bool_as_integer():
    cfg = get_project_config(ProjectCode.AS5PR)
    assert compute_config_digest(cfg).hex() == AS5PR_EXPECTED_CONFIG_DIGEST
    with pytest.raises(TypeError):
        canonical_config_bytes({"value": True})
```

- [ ] **Step 2: 验证测试先失败**

Run: `python -m pytest tests/release/test_project_config.py -q`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现最小不可变模型和规范编码**

```python
class ProjectCode(str, Enum):
    E68 = "E68"
    AS5PR = "AS5PR"

@dataclass(frozen=True)
class ProjectReleaseConfig:
    selection: ProjectCode
    project_code: int
    config_version: int
    ecu_target_id: int
    app_target_id: int
    flash_driver_target_id: int
    real_flash_enabled: bool
```

实现递归类型门禁、ASCII key/string、u32、tuple、固定 JSON 编码和 `PROJECT_RELEASE_CONFIG_V1\0` 域分隔；E68 的 `real_flash_enabled=False`。

- [ ] **Step 4: 验证通过**

Run: `python -m pytest tests/release/test_project_config.py -q`
Expected: PASS。

- [ ] **Step 5: 白名单提交**

```powershell
git add -- src/unified_can_lin_host_tool/release/project_config.py tests/release/test_project_config.py
git commit -m "实现内置项目发布配置摘要"
```

### Task 2: `.erel` 唯一解析与整包签名

**Files:**
- Create: `src/unified_can_lin_host_tool/release/package.py`
- Create: `tests/release/test_release_package.py`

**Interfaces:**
- Consumes: `ProjectReleaseConfig`, `compute_config_digest`。
- Produces: `ResourceKind`, `ReleaseResource`, `VerifiedReleasePackage`, `encode_release_package(resources, config, build_identity, key_id, private_key)`, `load_verified_release_package(path, selected_project, public_keys)`。

- [ ] **Step 1: 写固定向量和负向测试**

```python
def test_round_trip_signed_package(tmp_path, as5pr_resources, ed25519_key_pair):
    path = tmp_path / "as5pr.erel"
    write_release_package(path, as5pr_resources, AS5PR, private_key)
    loaded = load_verified_release_package(path, ProjectCode.AS5PR, {1: public_key})
    assert loaded.release_set_id == hashlib.sha256(path.read_bytes()).hexdigest()
    assert [r.kind for r in loaded.resources] == list(ResourceKind)

@pytest.mark.parametrize("mutation", ["trailing", "nonzero_padding", "offset_overflow", "unknown_key"])
def test_noncanonical_package_is_rejected(tmp_path, valid_package_bytes, mutation):
    damaged = mutate_package(valid_package_bytes, mutation)
    path = tmp_path / "damaged.erel"
    path.write_bytes(damaged)
    with pytest.raises(ValueError):
        load_verified_release_package(path, ProjectCode.AS5PR, TEST_PUBLIC_KEYS)
```

- [ ] **Step 2: 验证失败**

Run: `python -m pytest tests/release/test_release_package.py -q`
Expected: FAIL，编解码接口不存在。

- [ ] **Step 3: 实现固定 struct 和先签名后信任流程**

```python
HEADER = struct.Struct("<4sHHIIHH32s32s40sQ")
ENTRY = struct.Struct("<HHIIIII32s")
SIGNATURE_PREFIX = struct.Struct("<4sI")
HEADER_SIZE = 300
SIGNATURE_SIZE = 72
```

编码器严格生成唯一对齐布局；解析器先检查实际长度和尾部位置，再用 `raw[:-64]` 验签，随后校验项目配置、表项、padding、hash、角色 targetId/authVersion。

- [ ] **Step 4: 运行固定与边界测试**

Run: `python -m pytest tests/release/test_release_package.py -q`
Expected: PASS，包含 u32 溢出、截断、重排、重复角色和尾随字节。

- [ ] **Step 5: 白名单提交**

```powershell
git add -- src/unified_can_lin_host_tool/release/package.py tests/release/test_release_package.py
git commit -m "实现单文件发布资源包契约"
```

### Task 3: 三镜像同构建身份和开发打包器

**Files:**
- Create: `src/unified_can_lin_host_tool/release/build_identity.py`
- Create: `src/unified_can_lin_host_tool/cli/build_release_package.py`
- Create: `tests/release/test_build_identity.py`
- Create: `tests/release/test_build_release_package.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `BuildIdentity`, `read_build_identity(elf_path, bin_path)`, `validate_release_build(resources)`, CLI `ecu-release-build-package`。

- [ ] **Step 1: 写 ELF/BIN 一致、角色错误和混搭失败测试**
- [ ] **Step 2: 运行测试确认因接口缺失失败**

Run: `python -m pytest tests/release/test_build_identity.py tests/release/test_build_release_package.py -q`

- [ ] **Step 3: 实现100字节 `RBID` 解析和受控文件名门禁**

```python
BUILD_IDENTITY = struct.Struct("<4sHHIHH32s32s20s")

def validate_release_build(items: tuple[BuildIdentity, BuildIdentity, BuildIdentity]) -> None:
    shared = {(x.project_code, x.config_version, x.config_digest, x.build_id, x.build_commit) for x in items}
    if len(shared) != 1 or {x.resource_kind for x in items} != set(ResourceKind):
        raise ValueError("release resources do not come from one controlled build")
```

使用 pyelftools 读取 `.release_identity`；CLI 只接收 `--build-dir`、项目、两个密钥引用和输出，输入文件名来自项目配置。

- [ ] **Step 4: 验证打包器不接受任意资源路径且混搭必失败**

Run: `python -m pytest tests/release/test_build_identity.py tests/release/test_build_release_package.py -q`
Expected: PASS。

- [ ] **Step 5: 白名单提交**

```powershell
git add -- pyproject.toml src/unified_can_lin_host_tool/release/build_identity.py src/unified_can_lin_host_tool/cli/build_release_package.py tests/release/test_build_identity.py tests/release/test_build_release_package.py
git commit -m "绑定三镜像同次发布构建"
```

### Task 4: ArtifactIdentityV2 和会话存储迁移

**Files:**
- Modify: `src/unified_can_lin_host_tool/release/models.py`
- Modify: `src/unified_can_lin_host_tool/release/artifact_identity.py`
- Modify: `src/unified_can_lin_host_tool/release/inspector.py`
- Modify: `src/unified_can_lin_host_tool/release/artifact_store.py`
- Modify: `tests/release/test_artifact_identity.py`
- Modify: `tests/release/test_artifact_store.py`
- Modify: `tests/release/test_inspector.py`

**Interfaces:**
- Produces: `ArtifactIdentityV2`, `SignedArtifactId` 复算，检查上下文只消费 `VerifiedReleasePackage` 和 App 资源。

- [ ] **Step 1: 把测试改为 V2 固定 little-endian 向量并加入段越界/空洞测试**
- [ ] **Step 2: 运行测试，确认旧 V1 编码不满足新向量**
- [ ] **Step 3: 实现 V2 编码、半开区间、payload 重建和 releaseSetId 绑定**
- [ ] **Step 4: 会话存储导入 `.erel` 后只保存受控副本和内容 ID；加载时复验整包**
- [ ] **Step 5: 运行回归**

Run: `python -m pytest tests/release/test_artifact_identity.py tests/release/test_artifact_store.py tests/release/test_inspector.py -q`
Expected: PASS。

- [ ] **Step 6: 白名单提交**

```powershell
git add -- src/unified_can_lin_host_tool/release/models.py src/unified_can_lin_host_tool/release/artifact_identity.py src/unified_can_lin_host_tool/release/inspector.py src/unified_can_lin_host_tool/release/artifact_store.py tests/release/test_artifact_identity.py tests/release/test_artifact_store.py tests/release/test_inspector.py
git commit -m "升级发布制品身份与会话存储"
```

### Task 5: AS5PR HMAC 和完整镜像合并接入资源集合

**Files:**
- Modify: `src/unified_can_lin_host_tool/release/as5pr_signer.py`
- Modify: `src/unified_can_lin_host_tool/release/composer.py`
- Modify: `tests/release/test_as5pr_signer.py`
- Modify: `tests/release/test_composer.py`

**Interfaces:**
- Consumes: `VerifiedReleasePackage`, `ArtifactIdentityV2`。
- Produces: 仅通过认证资源生成 `ERASED_APP_VALID` 或 `OFFLINE_PREVALIDATED` 完整镜像。

- [ ] **Step 1: 写 payload||authHeader 固定向量和错误 target/version/HMAC 负向测试**
- [ ] **Step 2: 写 AppValid 512字节精确内容及未认证拒绝测试**
- [ ] **Step 3: 运行测试确认旧分散资源接口失败**
- [ ] **Step 4: 改造 signer/composer，删除自动降级路径**
- [ ] **Step 5: 运行测试并回读 HEX/S19 段**

Run: `python -m pytest tests/release/test_as5pr_signer.py tests/release/test_composer.py -q`
Expected: PASS。

- [ ] **Step 6: 白名单提交**

```powershell
git add -- src/unified_can_lin_host_tool/release/as5pr_signer.py src/unified_can_lin_host_tool/release/composer.py tests/release/test_as5pr_signer.py tests/release/test_composer.py
git commit -m "接入认证资源合并门禁"
```

### Task 6: 清退旧分散配置入口并完成阶段门禁

**Files:**
- Delete: `src/unified_can_lin_host_tool/release/manifest.py`
- Delete: `src/unified_can_lin_host_tool/profile.py`
- Delete: `tests/release/test_manifest.py`
- Delete: `tests/test_profile.py`
- Modify: all imports reported by `rg` under `src/` and `tests/`
- Modify: `README.md`

**Interfaces:**
- Produces: 全仓只通过 `ProjectReleaseConfig` 和 `VerifiedReleasePackage` 获取项目/资源数据。

- [ ] **Step 1: 用 `rg` 建立旧入口引用清单并逐调用方改失败测试**

Run: `rg -ni "mani(fest)|pro(file)" src tests README.md`
Expected: 迁移前有命中。

- [ ] **Step 2: 修改工作流、测试 fixture 和 README，删除旧文件及外部参数目录**
- [ ] **Step 3: 运行全量测试**

Run: `python -m pytest -q`
Expected: 全部 PASS。

- [ ] **Step 4: 验证禁止词、diff 和工作区**

Run: `rg -ni "mani(fest)|pro(file)" src tests README.md; git diff --check`
Expected: 无命中，`git diff --check` 成功。

- [ ] **Step 5: 白名单提交**

```powershell
git add -- src tests README.md pyproject.toml
git commit -m "清退旧分散发布配置入口"
```

### Task 7: 阶段 A 总验证

**Files:**
- Verify only; no source changes unless a failing test identifies a root cause.

- [ ] **Step 1: 安装项目并执行全量测试**

Run: `python -m pip install -e .`
Expected: 安装成功。

Run: `python -m pytest -q`
Expected: 全部 PASS。

- [ ] **Step 2: 运行包破坏测试和命令行 smoke**

Run: `python -m pytest tests/release -q`
Expected: 全部 PASS。

- [ ] **Step 3: 审计暂存范围和提交链**

Run: `git status --short; git log --oneline -8`
Expected: 只有后续阶段尚未提交的计划文件；阶段 A 源码无未提交残留。
