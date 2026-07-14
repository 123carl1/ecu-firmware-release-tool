# GitHub 自动发布与客户端更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付带统一版本身份、GitHub 标签自动发布、签名更新检查和 Inno Setup 覆盖升级能力的 Windows `0.2.0` 引导版本，并公开发布到 `ecu-firmware-release-tool`。

**Architecture:** `tool_identity.py` 统一读取当前工具版本和正式构建身份；`update/` 只负责严格版本解析、Ed25519 验签、安全 HTTPS、安装包缓存和进程互斥；Qt 层只用 worker 调用更新服务并处理用户选择。构建任务不接触签名私钥，受保护的 GitHub `release` Environment 在独立发布任务中签名并公开不可修改 Release。

**Tech Stack:** Python 3.11.9、PySide6 6.11.0、cryptography 46.0.3、PyInstaller 6.20.0、PowerShell 7、Inno Setup 6.7.3、GitHub Actions、pytest。

## Global Constraints

- 正式公开仓库固定为 `ecu-firmware-release-tool`，源码和 Release 位于同一仓库。
- `pyproject.toml` 的 `[project].version` 是唯一人工维护版本；引导版本为 `0.2.0`。
- 版本严格为三段十进制数字，每段 `0~65535`，禁止前导零、同版本重发和降级自动安装。
- 正式发布仅由默认分支历史中的 `vX.Y.Z` 标签触发；标签、源码版本、PE 版本、安装包版本和更新信息必须一致。
- 正式客户端只接受固化仓库、`channel=stable`、最多4把内置公钥之一验签通过的更新信息。
- “最新 Release”地址只定位标签；JSON 和64字节签名必须从同一标签确定地址重新取得并验签。
- 正式网络只允许 HTTPS 443，最多5次重定向，主机只能是 `github.com` 或 `.githubusercontent.com` 子域，禁止 HTTP 降级和运行时替换仓库地址。
- 扫描、OTA、诊断或 GUI 启动的 CLI 未结束时禁止下载后退出和启动安装器；任何安装流程不得强杀总线业务进程。
- Inno Setup `AppId={{63A6F055-819E-45D3-B84A-47C57B140234}` 保持不变，保留当前用户安装方式和既有安装目录。
- 无 Windows 软件代码签名证书；必须如实接受“未知发布者”提示，不得把 Ed25519 更新签名描述成 Windows 受信任发布者。
- 现有固件开发签名私有种子和 Boot HMAC 是公开开发台架凭据；自动更新私钥必须独立且不得进入 Git、安装包、日志或构建产物。
- USB2XXX 运行库固定官方仓库提交 `d1fd307a72cad0c71aa81db79ab413b8e7a26175`；x64 `USB2XXX.dll` 大小 `538112`、SHA-256 `7857f3c43b5f5f41414da0ce04f2914d45af805a7ad0e14a0aa84b6a16a42d1b`，`libusb-1.0.dll` 大小 `157696`、SHA-256 `a8c91f0ff68fb7802a9f4416728f0eeb4d99af4ceaa4ef7dfe9374e76e375018`。
- 当前官方 USB2XXX 示例仓库未发现明确再分发许可；公开安装包和说明必须保留来源、版权与风险边界。
- 正式构建只允许干净检出和已追踪输入；并行子代理不得同时修改同一文件。
- 每个代码任务先写失败测试、确认失败原因，再做最小实现；每个任务结束时独立评审并白名单提交。

## 文件职责图

- `src/unified_can_lin_host_tool/versioning.py`：严格三段版本解析、比较和 Windows 四段版本转换。
- `src/unified_can_lin_host_tool/tool_identity.py`：读取安装元数据或 PyInstaller 内嵌的 `_tool_build_identity.json`。
- `src/unified_can_lin_host_tool/update/errors.py`：稳定错误码和按网络、元数据、签名、完整性、忙碌状态、安装启动划分的异常。
- `src/unified_can_lin_host_tool/update/metadata.py`：更新信息数据模型、定位标签解析、验签后的字段门禁。
- `src/unified_can_lin_host_tool/update/release_keys.py`：读取和限制内置 Ed25519 公钥集合。
- `src/unified_can_lin_host_tool/update/https_client.py`：HTTPS、重定向、大小和超时边界。
- `src/unified_can_lin_host_tool/update/github_release.py`：最新标签定位、标签确定资源获取、重试一次和签名验证。
- `src/unified_can_lin_host_tool/update/service.py`：版本判定、版本隔离缓存、流式 hash、安装器参数和启动结果。
- `src/unified_can_lin_host_tool/update/runtime_mutex.py`：GUI/CLI 共同持有的 Windows 命名互斥量。
- `src/unified_can_lin_host_tool/ui/update_worker.py`：把阻塞检查/下载放到 QThread，界面只接收信号。
- `scripts/release_signing.py`：一次性生成发布密钥、生成规范更新信息、签名和比对公钥。
- `scripts/release_build.py`：读取唯一版本、生成构建身份/PE 版本资源、审计产物。
- `scripts/build_windows_installer.ps1`：调用同一构建入口、取得受控 USB2XXX DLL、构建 GUI/CLI/安装包。
- `installer/EcuReleaseTool.iss`：版本门禁、父 GUI 等待、运行占用检查、覆盖安装和自动重启新 GUI。
- `.github/workflows/ci.yml`：分支和 Pull Request 测试/构建，不发布。
- `.github/workflows/release.yml`：标签构建与签名发布权限隔离。

---

### Task 1: 收口现有 USB2XXX、扫描和 OTA 基线

**Files:**
- Modify: `scripts/build_windows_installer.ps1`
- Modify: `src/unified_can_lin_host_tool/cli/release.py`
- Modify: `src/unified_can_lin_host_tool/transport/can_isotp.py`
- Modify: `src/unified_can_lin_host_tool/ui/release_workspace.py`
- Create: `src/unified_can_lin_host_tool/adapters/usb2xxx.py`
- Modify: `tests/test_can_isotp_transport.py`
- Modify: `tests/test_release_cli.py`
- Modify: `tests/test_release_workspace.py`
- Create: `tests/test_usb2xxx_adapter.py`

**Interfaces:**
- Produces: `Usb2xxxAdapter.probe_can_devices()`、`Usb2xxxAdapter.open_can()`、CLI `scan --adapter usb2xxx`、GUI 图莫斯 CAN 通道选择。
- Preserves: 同星扫描和 AS5PR OTA 现有命令行参数、退出码和 JSON 事件。

- [ ] **Step 1: 复核暂存边界和格式**

Run:

```powershell
git diff --check
git diff --name-status
git status --short
```

Expected: 只出现本任务列出的9个文件；没有空白错误，没有构建产物进入 Git。

- [ ] **Step 2: 运行能触发原问题的针对性测试**

Run:

```powershell
python -m pip install --no-deps -e .
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_usb2xxx_adapter.py tests/test_can_isotp_transport.py tests/test_release_cli.py tests/test_release_workspace.py -q
```

Expected: `33 passed, 3 subtests passed`。

- [ ] **Step 3: 审核真实 SDK 契约**

逐项对照源码确认：设备句柄由 `USB_ScanDevice` 返回；`DEV_GetHardwareInfo` 提供 CAN 通道数；经典 CAN DLC 固定8；`CAN_SendMsgSynch` 优先且旧 DLL 才回退；CLI JSON 中保留 `adapter=usb2xxx`、序列号、设备索引和每个 SDK 通道。不得把未安装 DLL 误报成“发现0通道”。

补一个先失败的路径测试，要求默认 DLL 查找只允许 `USB2XXX_DLL` 环境变量、PyInstaller 解包目录、EXE 同目录和通用安装位置 `D:\software\USB2XXX\USB2XXX.dll`；删除 `D:\01_WorkProgram\Company_Program` 等个人/公司检出目录绝对回退。测试先对当前源码失败，修正后通过。

- [ ] **Step 4: 运行全量回归**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest -q
```

Expected: `330 passed, 3 subtests passed`。

- [ ] **Step 5: 白名单提交基线**

```powershell
git add -- scripts/build_windows_installer.ps1 src/unified_can_lin_host_tool/cli/release.py src/unified_can_lin_host_tool/transport/can_isotp.py src/unified_can_lin_host_tool/ui/release_workspace.py src/unified_can_lin_host_tool/adapters/usb2xxx.py tests/test_can_isotp_transport.py tests/test_release_cli.py tests/test_release_workspace.py tests/test_usb2xxx_adapter.py
git diff --cached --check
git commit -m "接入 USB2XXX 扫描与 AS5PR OTA 通道"
```

### Task 2: 建立唯一版本与工具构建身份

**Files:**
- Create: `src/unified_can_lin_host_tool/versioning.py`
- Create: `src/unified_can_lin_host_tool/tool_identity.py`
- Modify: `src/unified_can_lin_host_tool/__init__.py`
- Modify: `src/unified_can_lin_host_tool/cli/release.py`
- Modify: `src/unified_can_lin_host_tool/ui/release_workspace.py`
- Modify: `pyproject.toml`
- Create: `tests/test_versioning.py`
- Create: `tests/test_tool_identity.py`
- Modify: `tests/test_release_cli.py`
- Modify: `tests/test_release_workspace.py`

**Interfaces:**
- Produces: `SemanticVersion.parse(text) -> SemanticVersion`、`SemanticVersion.windows_tuple() -> tuple[int, int, int, int]`、`ToolIdentity`、`get_tool_identity() -> ToolIdentity`。
- Produces: `EcuReleaseCLI.exe --version`；所有 CLI JSON 事件加入 `toolVersion` 和 `toolCommit`。

- [ ] **Step 1: 写版本边界失败测试**

```python
@pytest.mark.parametrize("text", ["0.2", "v0.2.0", "01.2.3", "1.65536.0", "1.-1.0", "1.2.3.4"])
def test_semantic_version_rejects_noncanonical_or_windows_overflow(text):
    with pytest.raises(ValueError):
        SemanticVersion.parse(text)

def test_semantic_version_compares_numerically_and_builds_windows_tuple():
    assert SemanticVersion.parse("0.10.0") > SemanticVersion.parse("0.2.9")
    assert SemanticVersion.parse("0.2.0").windows_tuple() == (0, 2, 0, 0)
```

Run: `python -m pytest tests/test_versioning.py -q`

Expected: FAIL，`versioning` 模块不存在。

- [ ] **Step 2: 实现严格版本值对象**

```python
_VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")

@dataclass(frozen=True, order=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> "SemanticVersion":
        match = _VERSION_RE.fullmatch(text)
        if match is None:
            raise ValueError("版本必须为无前导零的三段数字")
        parts = tuple(int(value) for value in match.groups())
        if any(value > 65535 for value in parts):
            raise ValueError("版本段超出 Windows 文件版本范围")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def windows_tuple(self) -> tuple[int, int, int, int]:
        return self.major, self.minor, self.patch, 0
```

- [ ] **Step 3: 写构建身份失败测试**

```python
def test_official_identity_requires_matching_installed_version():
    raw = json.dumps({
        "version": "0.2.0",
        "commit": "01" * 20,
        "buildTimeUtc": "2026-07-14T12:00:00Z",
        "repository": "owner/ecu-firmware-release-tool",
        "officialBuild": True,
    }).encode()
    assert load_tool_identity(raw, installed_version="0.2.0").official_build is True
    with pytest.raises(ValueError, match="版本"):
        load_tool_identity(raw, installed_version="0.2.1")

def test_development_identity_never_claims_official_repository():
    identity = load_tool_identity(None, installed_version="0.2.0")
    assert identity == ToolIdentity("0.2.0", "development", "", "", False)
```

Run: `python -m pytest tests/test_tool_identity.py -q`

Expected: FAIL，`tool_identity` 模块不存在。

- [ ] **Step 4: 实现构建身份读取**

`tool_identity.py` 必须定义以下不可变数据，并拒绝重复 JSON key、未知字段、非布尔 `officialBuild`、错误提交号、非 `Z` 结尾 UTC 时间、错误仓库格式和与安装元数据不一致的版本：

```python
@dataclass(frozen=True)
class ToolIdentity:
    version: str
    commit: str
    build_time_utc: str
    repository: str
    official_build: bool

    @property
    def short_commit(self) -> str:
        return self.commit[:7] if len(self.commit) == 40 else self.commit

```

接口固定为 `load_tool_identity(raw: bytes | None, *, installed_version: str) -> ToolIdentity` 和带单项缓存的 `get_tool_identity() -> ToolIdentity`。`get_tool_identity()` 用 `importlib.metadata.version("unified-can-lin-host-tool")` 读取安装版本；找不到元数据时返回 `0+unknown` 开发身份。正式 PyInstaller 构建从包资源 `_tool_build_identity.json` 读取，其它情况下返回开发身份。

- [ ] **Step 5: 切换到 `0.2.0` 并接入 CLI/GUI**

在 `pyproject.toml` 只修改 `[project].version = "0.2.0"`；`__init__.py` 改为 `__version__ = get_tool_identity().version`，不保留第二份字符串常量。CLI parser 加入：

```python
identity = get_tool_identity()
parser.add_argument(
    "--version",
    action="version",
    version=f"EcuReleaseCLI {identity.version} (commit {identity.short_commit})",
)
```

`_print_json()` 在每个事件顶层写入 `toolVersion` 和 `toolCommit`。GUI 标题固定为 `ECU Firmware Release Tool {identity.version}`，启动日志写版本和短提交号。

- [ ] **Step 6: 验证并提交**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_versioning.py tests/test_tool_identity.py tests/test_release_cli.py tests/test_release_workspace.py tests/test_ui_smoke.py -q
python -m unified_can_lin_host_tool.cli.release --version
```

Expected: 测试通过；版本输出包含 `0.2.0`，开发态提交显示 `development`。

```powershell
git add -- pyproject.toml src/unified_can_lin_host_tool/__init__.py src/unified_can_lin_host_tool/versioning.py src/unified_can_lin_host_tool/tool_identity.py src/unified_can_lin_host_tool/cli/release.py src/unified_can_lin_host_tool/ui/release_workspace.py tests/test_versioning.py tests/test_tool_identity.py tests/test_release_cli.py tests/test_release_workspace.py
git commit -m "统一工具版本与构建身份"
```

### Task 3: 实现签名更新信息与发布密钥

**Files:**
- Create: `src/unified_can_lin_host_tool/update/__init__.py`
- Create: `src/unified_can_lin_host_tool/update/errors.py`
- Create: `src/unified_can_lin_host_tool/update/metadata.py`
- Create: `src/unified_can_lin_host_tool/update/release_keys.py`
- Create: `src/unified_can_lin_host_tool/update/release_public_keys.json`
- Create: `scripts/release_signing.py`
- Modify: `pyproject.toml`
- Create: `tests/update/test_metadata.py`
- Create: `tests/update/test_release_keys.py`
- Create: `tests/test_release_signing.py`

**Interfaces:**
- Consumes: `SemanticVersion`。
- Produces: `InstallerAsset`、`UpdateInfo`、`parse_locator_tag(raw)`、`verify_signed_update(raw, signature, public_keys, expected_repository)`、`load_release_public_keys()`。
- Produces: `UpdateError` 及 `UpdateMetadataError`、`UpdateSecurityError`、`UpdateNetworkError`、`UpdateIntegrityError`、`UpdateBusyError`、`UpdateInstallerError`。
- Produces: `release-v1` 独立私钥位于 `D:\software\EcuReleaseTool\release-keys\release-v1.pem`，Git 只保存32字节公钥。

- [ ] **Step 1: 写规范 JSON、重复 key 和签名失败测试**

```python
def signed_update(private_key, **changes):
    payload = {
        "schemaVersion": 1,
        "repository": "owner/ecu-firmware-release-tool",
        "version": "0.2.1",
        "tag": "v0.2.1",
        "commit": "01" * 20,
        "generatedAt": "2026-07-14T12:00:00Z",
        "channel": "stable",
        "releaseNotes": "修复更新检查。",
        "installer": {
            "name": "EcuReleaseTool_Setup_0.2.1.exe",
            "size": 123,
            "sha256": "ab" * 32,
        },
        "keyId": "test-v1",
    } | changes
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return raw, private_key.sign(raw)

def test_signature_is_verified_before_key_id_is_trusted(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(private_key)
    info = verify_signed_update(raw, signature, {"test-v1": public_key}, "owner/ecu-firmware-release-tool")
    assert str(info.version) == "0.2.1"
    assert info.verified_key_id == "test-v1"

def test_bad_signature_is_rejected(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw, signature = signed_update(private_key)
    with pytest.raises(UpdateSecurityError, match="签名"):
        verify_signed_update(raw + b" ", signature, {"test-v1": public_key}, "owner/ecu-firmware-release-tool")

def test_duplicate_json_key_is_rejected(ed25519_key_pair):
    private_key, public_key = ed25519_key_pair
    raw = b'{"schemaVersion":1,"schemaVersion":1}'
    with pytest.raises(UpdateMetadataError, match="重复"):
        verify_signed_update(raw, private_key.sign(raw), {"test-v1": public_key}, "owner/ecu-firmware-release-tool")
```

同一测试文件还要分别构造并拒绝：`keyId` 与实际公钥不一致、仓库不一致、`channel` 非 `stable`、说明超过16 KiB、安装包名/版本不一致、未知字段和 bool 冒充整数。

Run: `python -m pytest tests/update/test_metadata.py -q`

Expected: FAIL，更新模块不存在。

- [ ] **Step 2: 实现严格数据模型与验签顺序**

`errors.py` 先定义稳定错误码；每个子类构造时固定对应 code，调用方只补充中文 detail：

```python
class UpdateError(RuntimeError):
    code = "UPDATE_FAILED"

class UpdateMetadataError(UpdateError):
    code = "UPDATE_METADATA_INVALID"

class UpdateSecurityError(UpdateError):
    code = "UPDATE_SIGNATURE_INVALID"

class UpdateNetworkError(UpdateError):
    code = "UPDATE_NETWORK_UNAVAILABLE"

class UpdateIntegrityError(UpdateError):
    code = "UPDATE_INSTALLER_INTEGRITY_FAILED"

class UpdateBusyError(UpdateError):
    code = "UPDATE_TOOL_BUSY"

class UpdateInstallerError(UpdateError):
    code = "UPDATE_INSTALLER_START_FAILED"
```

```python
MAX_UPDATE_JSON_BYTES = 64 * 1024
MAX_RELEASE_NOTES_BYTES = 16 * 1024
MAX_RELEASE_KEYS = 4

@dataclass(frozen=True)
class InstallerAsset:
    name: str
    size: int
    sha256: str

@dataclass(frozen=True)
class UpdateInfo:
    repository: str
    version: SemanticVersion
    tag: str
    commit: str
    generated_at: str
    channel: str
    release_notes: str
    installer: InstallerAsset
    verified_key_id: str

```

函数接口固定为 `parse_locator_tag(raw: bytes) -> str` 和 `verify_signed_update(raw: bytes, signature: bytes, public_keys: Mapping[str, bytes], expected_repository: str) -> UpdateInfo`。实现必须先检查 JSON 最大64 KiB和签名恰好64字节，再逐把验证最多4把唯一公钥；只有恰好一把通过后才解析可信字段。顶层字段和 `installer` 字段必须精确匹配设计；拒绝 bool 冒充 int、重复 key、未知字段、非 UTF-8、`channel != stable`、仓库不一致、标签/版本/安装包名不一致、非40位小写提交号、非64位小写 SHA-256、非正大小和超长说明。`keyId` 必须等于实际验签成功的 key。

- [ ] **Step 3: 实现发布密钥工具并写测试**

`scripts/release_signing.py` 必须提供 `generate_release_key(private_output: Path, public_output: Path, key_id: str) -> None`、`load_signing_key_from_environment() -> Ed25519PrivateKey`、`assert_public_key_matches(private_key: Ed25519PrivateKey, public_keys_path: Path, key_id: str) -> None` 和 `sign_update_file(input_path: Path, signature_path: Path) -> None`。私钥只从环境变量 `UPDATE_SIGNING_KEY_PEM` 读取，签名命令不得接受私钥路径或私钥文本参数。

`generate-key` 拒绝覆盖现有私钥；Windows 上用 `icacls` 移除继承并只授予当前用户完全控制，权限设置失败时删除刚生成的私钥。公钥 JSON 固定为：

```json
{
  "release-v1": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
}
```

上述值只是格式测试向量；Step 4 会用新生成的真实公钥原子替换整个 JSON 文件。

- [ ] **Step 4: 生成正式 `release-v1` 密钥对**

Run:

```powershell
python scripts/release_signing.py generate-key --private-output D:\software\EcuReleaseTool\release-keys\release-v1.pem --public-output src\unified_can_lin_host_tool\update\release_public_keys.json --key-id release-v1
```

Expected: 私钥只在 `D:\software\EcuReleaseTool\release-keys\`，Git 状态只出现公钥 JSON；再次运行因拒绝覆盖而失败。

- [ ] **Step 5: 配置包数据并验证**

在 `pyproject.toml` 增加包数据规则，使 `update/release_public_keys.json` 被安装；PyInstaller 构建任务后续仍显式审计该文件。运行：

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m pytest tests/update/test_metadata.py tests/update/test_release_keys.py tests/test_release_signing.py -q
rg -n "BEGIN .*PRIVATE KEY|UPDATE_SIGNING_KEY_PEM" src tests README.md
```

Expected: 测试通过；源码没有私钥内容，环境变量名只出现在签名脚本和对应测试。

- [ ] **Step 6: 白名单提交**

```powershell
git add -- pyproject.toml scripts/release_signing.py src/unified_can_lin_host_tool/update/__init__.py src/unified_can_lin_host_tool/update/errors.py src/unified_can_lin_host_tool/update/metadata.py src/unified_can_lin_host_tool/update/release_keys.py src/unified_can_lin_host_tool/update/release_public_keys.json tests/update/test_metadata.py tests/update/test_release_keys.py tests/test_release_signing.py
git commit -m "实现签名更新信息与发布公钥"
```

### Task 4: 实现 GitHub 安全读取与跨 Release 竞态防护

**Files:**
- Create: `src/unified_can_lin_host_tool/update/https_client.py`
- Create: `src/unified_can_lin_host_tool/update/github_release.py`
- Create: `tests/update/test_https_client.py`
- Create: `tests/update/test_github_release.py`

**Interfaces:**
- Consumes: `parse_locator_tag()`、`verify_signed_update()`。
- Produces: `validate_github_https_url(url)`、`SafeHttpsClient.read_bytes()`、`SafeHttpsClient.iter_bytes()`、`GitHubReleaseSource.fetch() -> UpdateInfo`。

- [ ] **Step 1: 写 URL 与重定向失败测试**

```python
@pytest.mark.parametrize("url", [
    "http://github.com/a/b/releases/x",
    "https://github.com:444/a/b/releases/x",
    "https://user@github.com/a/b/releases/x",
    "https://github.com.evil.example/a/b",
    "https://evilgithubusercontent.com/a/b",
])
def test_github_url_gate_rejects_unsafe_targets(url):
    with pytest.raises(UpdateNetworkError):
        validate_github_https_url(url)

def test_github_url_gate_accepts_release_asset_hosts():
    validate_github_https_url("https://github.com/a/b/releases/download/v1.2.3/update.json")
    validate_github_https_url("https://objects.githubusercontent.com/path")
```

Run: `python -m pytest tests/update/test_https_client.py -q`

Expected: FAIL，网络模块不存在。

- [ ] **Step 2: 实现有界 HTTPS 客户端**

`SafeHttpsClient` 暴露两个固定接口：`read_bytes(url: str, *, max_bytes: int, connect_timeout_s: float = 5.0, read_timeout_s: float = 15.0, no_cache: bool = False) -> bytes`，以及 `iter_bytes(url: str, *, max_bytes: int, connect_timeout_s: float = 5.0, read_timeout_s: float = 60.0) -> Iterator[bytes]`。

使用系统证书验证 TLS；请求前、每次重定向和最终地址都调用 URL 门禁。自定义 redirect handler 最多允许5跳；Content-Length 超限立即失败，未知长度时只读取 `max_bytes + 1` 后拒绝。`no_cache=True` 写 `Cache-Control: no-cache` 和 `Pragma: no-cache`。异常映射为稳定的 `UpdateNetworkError.code`，日志不得包含 URL 用户信息。

- [ ] **Step 3: 写最新标签切换和缓存竞态测试**

```python
def test_latest_is_only_a_locator_and_tag_resources_are_paired(fake_http, signed_release):
    fake_http.add("https://github.com/o/ecu-firmware-release-tool/releases/latest/download/update.json", signed_release.locator)
    fake_http.add("https://github.com/o/ecu-firmware-release-tool/releases/download/v0.2.1/update.json", signed_release.raw)
    fake_http.add("https://github.com/o/ecu-firmware-release-tool/releases/download/v0.2.1/update.json.sig", signed_release.signature)
    info = GitHubReleaseSource("o/ecu-firmware-release-tool", fake_http).fetch(signed_release.keys)
    assert info.tag == "v0.2.1"
    assert all("latest/download/update.json.sig" not in url for url in fake_http.requested_urls)

def test_mismatched_pair_relocates_once_then_stops(fake_http, release_1, release_2):
    fake_http.queue_latest(release_1.locator, release_2.locator)
    fake_http.add_tag_pair("v0.2.1", release_1.raw, release_2.signature)
    fake_http.add_tag_pair("v0.2.2", release_2.raw, b"x" * 64)
    with pytest.raises(UpdateSecurityError):
        GitHubReleaseSource("o/ecu-firmware-release-tool", fake_http).fetch(release_1.keys)
    assert fake_http.latest_request_count == 2

def test_stale_locator_cache_is_retried_once_with_no_cache(fake_http, release_1, release_2):
    fake_http.queue_latest(release_1.locator, release_2.locator)
    fake_http.add_tag_pair("v0.2.1", release_1.raw, b"x" * 64)
    fake_http.add_tag_pair("v0.2.2", release_2.raw, release_2.signature)
    info = GitHubReleaseSource("o/ecu-firmware-release-tool", fake_http).fetch(release_2.keys)
    assert info.tag == "v0.2.2"
    assert fake_http.latest_headers == [{"Cache-Control": "no-cache", "Pragma": "no-cache"}] * 2
```

Run: `python -m pytest tests/update/test_github_release.py -q`

Expected: FAIL，GitHub 读取类不存在。

- [ ] **Step 4: 实现两阶段读取**

`GitHubReleaseSource` 的构造接口固定为 `GitHubReleaseSource(repository: str, http: SafeHttpsClient)`，读取接口固定为 `fetch(public_keys: Mapping[str, bytes]) -> UpdateInfo`。

`fetch()` 每次尝试都执行：读取 `/releases/latest/download/update.json` 最大64 KiB，只取合法标签；再从 `/releases/download/{tag}/update.json` 和 `.sig` 取得一对资源；验签后要求 `repository`、`tag` 和 `version` 一致。签名或配对失败时重新定位一次，第二次仍失败则返回安全错误；网络超时不无限重试。

- [ ] **Step 5: 验证并提交**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
python -m pytest tests/update/test_https_client.py tests/update/test_github_release.py tests/update/test_metadata.py -q
git add -- src/unified_can_lin_host_tool/update/https_client.py src/unified_can_lin_host_tool/update/github_release.py tests/update/test_https_client.py tests/update/test_github_release.py
git commit -m "实现 GitHub 更新信息安全读取"
```

### Task 5: 实现安装包缓存、更新服务和运行互斥

**Files:**
- Create: `src/unified_can_lin_host_tool/update/service.py`
- Create: `src/unified_can_lin_host_tool/update/runtime_mutex.py`
- Modify: `src/unified_can_lin_host_tool/cli/release.py`
- Modify: `src/unified_can_lin_host_tool/ui/app.py`
- Create: `tests/update/test_service.py`
- Create: `tests/update/test_runtime_mutex.py`
- Modify: `tests/test_release_cli.py`
- Modify: `tests/test_ui_smoke.py`

**Interfaces:**
- Consumes: `ToolIdentity`、`GitHubReleaseSource`、`UpdateInfo`、内置公钥。
- Produces: `UpdateService.check() -> UpdateInfo | None`、`UpdateService.download()`、`installer_arguments()`、`product_run_mutex()`。

- [ ] **Step 1: 写版本与缓存失败测试**

```python
def test_check_only_returns_strictly_newer_stable_release(fake_source, fake_http, official_identity, tmp_path):
    service = UpdateService(official_identity, fake_source, fake_http, tmp_path, {})
    fake_source.info = update_info("0.2.0")
    assert service.check() is None
    fake_source.info = update_info("0.2.1")
    assert service.check().version == SemanticVersion.parse("0.2.1")

def test_download_writes_part_then_atomically_renames(fake_http, update_info, tmp_path):
    identity = ToolIdentity("0.2.0", "01" * 20, "2026-07-14T12:00:00Z", "o/ecu-firmware-release-tool", True)
    service = UpdateService(identity, Mock(), fake_http, tmp_path, {})
    path = service.download(update_info)
    assert path.name == "EcuReleaseTool_Setup_0.2.1.exe"
    assert path.read_bytes() == fake_http.payload
    assert not list(path.parent.glob("*.part"))

def test_wrong_hash_never_leaves_executable_cache(fake_http, update_info, tmp_path):
    damaged = replace(
        update_info,
        installer=replace(update_info.installer, sha256="00" * 32),
    )
    identity = ToolIdentity("0.2.0", "01" * 20, "2026-07-14T12:00:00Z", "o/ecu-firmware-release-tool", True)
    service = UpdateService(identity, Mock(), fake_http, tmp_path, {})
    with pytest.raises(UpdateIntegrityError, match="SHA-256"):
        service.download(damaged)
    assert not list(tmp_path.rglob("*.exe"))
    assert not list(tmp_path.rglob("*.part"))
```

同一测试文件还要用具体 payload 分别覆盖截断、超过声明大小和 `cancelled()` 返回 `True`，三种情况都断言无 `.exe`、无 `.part`。

Run: `python -m pytest tests/update/test_service.py -q`

Expected: FAIL，更新服务不存在。

- [ ] **Step 2: 实现更新服务与原子缓存**

`ProgressCallback` 固定为 `Callable[[int, int], None]`。`UpdateService` 构造参数依次为 `identity: ToolIdentity`、`source: GitHubReleaseSource`、`http: SafeHttpsClient`、`cache_root: Path`、`public_keys: Mapping[str, bytes]`；公开 `check() -> UpdateInfo | None` 和 `download(info: UpdateInfo, *, progress: ProgressCallback | None = None, cancelled: Callable[[], bool] = lambda: False) -> Path`。安装参数函数固定为 `installer_arguments(*, parent_pid: int, log_path: Path) -> list[str]`。

开发身份或仓库为空时 `check()` 返回 `None` 且不访问网络；GUI 根据 `ToolIdentity.official_build` 显示“开发构建，不检查正式更新”。缓存路径固定 `%LOCALAPPDATA%\EcuReleaseTool\updates\{version}\`；先写随机后缀 `.part`，流式累计大小和 SHA-256，`flush + fsync` 后 `os.replace`。已有正式文件也必须重新校验后才复用；任何失败删除 `.part` 和错误正式文件。安装参数精确包含 `/SILENT`、`/NORESTART`、`/NOCLOSEAPPLICATIONS`、`/AUTO_UPDATE`、以十进制进程号拼成的 `/PARENT_PID=1234` 和以实际绝对日志路径拼成的 `/LOG=D:\software\EcuReleaseTool\updates\installer.log`；测试示例值不得硬编码到运行实现。

- [ ] **Step 3: 写命名互斥量测试**

```python
def test_product_mutex_exists_while_context_is_active():
    with product_run_mutex():
        assert is_product_mutex_present() is True
    assert is_product_mutex_present() is False

def test_multiple_process_handles_keep_mutex_present():
    with product_run_mutex():
        with product_run_mutex():
            assert is_product_mutex_present() is True
        assert is_product_mutex_present() is True
    assert is_product_mutex_present() is False
```

Run: `python -m pytest tests/update/test_runtime_mutex.py -q`

Expected: FAIL，运行互斥模块不存在。

- [ ] **Step 4: 实现并接入 Windows 命名互斥量**

```python
PRODUCT_RUN_MUTEX = r"Local\EcuFirmwareReleaseTool.Run"

@contextmanager
def product_run_mutex() -> Iterator[None]:
    handle = _create_mutex(PRODUCT_RUN_MUTEX) if sys.platform == "win32" else None
    try:
        yield
    finally:
        if handle is not None:
            _close_handle(handle)
```

用 `CreateMutexW` 和 `CloseHandle`；不因 `ERROR_ALREADY_EXISTS` 拒绝第二个业务实例，因为安装器需要判断“是否还有任一实例”，不是强制单实例。GUI 在 `QApplication` 生命周期外层持有互斥量；CLI 只在 `scan`/`ota` 业务执行期间持有，`--version` 不持有。

- [ ] **Step 5: 验证并提交**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/update/test_service.py tests/update/test_runtime_mutex.py tests/test_release_cli.py tests/test_ui_smoke.py -q
git add -- src/unified_can_lin_host_tool/update/service.py src/unified_can_lin_host_tool/update/runtime_mutex.py src/unified_can_lin_host_tool/cli/release.py src/unified_can_lin_host_tool/ui/app.py tests/update/test_service.py tests/update/test_runtime_mutex.py tests/test_release_cli.py tests/test_ui_smoke.py
git commit -m "实现更新缓存与业务进程互斥"
```

### Task 6: 接入 GUI 更新提醒、下载和退出协议

**Files:**
- Create: `src/unified_can_lin_host_tool/ui/update_worker.py`
- Modify: `src/unified_can_lin_host_tool/ui/release_workspace.py`
- Modify: `src/unified_can_lin_host_tool/ui/app.py`
- Create: `tests/test_update_worker.py`
- Modify: `tests/test_release_workspace.py`
- Modify: `tests/test_ui_smoke.py`

**Interfaces:**
- Consumes: `UpdateService.check()`、`UpdateService.download()`、`installer_arguments()`。
- Produces: 帮助菜单“检查更新/关于”、启动后异步检查、立即更新/稍后提醒、安装器启动成功后正常退出。

- [ ] **Step 1: 写 worker 非阻塞和错误信号测试**

```python
def test_worker_emits_result_without_touching_widgets():
    worker = UpdateWorker(lambda progress: "result")
    signal = QSignalSpy(worker.succeeded)
    worker.run()
    assert signal.count() == 1
    assert signal.at(0) == ["result"]

def test_worker_maps_exception_to_failed_signal():
    worker = UpdateWorker(lambda _progress: (_ for _ in ()).throw(RuntimeError("network down")))
    signal = QSignalSpy(worker.failed)
    worker.run()
    assert signal.count() == 1
    assert signal.at(0) == ["network down"]
```

Run: `python -m pytest tests/test_update_worker.py -q`

Expected: FAIL，Qt 更新 worker 不存在。

- [ ] **Step 2: 实现通用 Qt 更新 worker**

```python
class UpdateWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, operation: Callable[[Callable[[int, int], None]], object]) -> None:
        super().__init__()
        self._operation = operation

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation(self.progress.emit))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
```

窗口统一通过 `_start_update_worker()` 创建 QThread，`finished` 后 `quit/deleteLater`，不得在主线程网络读取或 hash 大文件。

- [ ] **Step 3: 写 GUI 门禁和安装启动测试**

```python
def test_immediate_update_is_disabled_while_scan_or_ota_process_exists(window):
    window._process = object()
    assert window._can_start_update_install() is False

def test_installer_launch_success_freezes_tasks_and_quits(window, mocker, tmp_path):
    start = mocker.patch(
        "unified_can_lin_host_tool.ui.release_workspace.QProcess.startDetached",
        return_value=(True, 1234),
    )
    quit_app = mocker.patch.object(QApplication.instance(), "quit")
    window._launch_verified_installer(tmp_path / "setup.exe")
    assert window.scan_button.isEnabled() is False
    start.assert_called_once()
    quit_app.assert_called_once()

def test_installer_launch_failure_unfreezes_without_closing(window, mocker, tmp_path):
    mocker.patch(
        "unified_can_lin_host_tool.ui.release_workspace.QProcess.startDetached",
        return_value=(False, 0),
    )
    quit_app = mocker.patch.object(QApplication.instance(), "quit")
    window._launch_verified_installer(tmp_path / "setup.exe")
    assert window.scan_button.isEnabled() is True
    quit_app.assert_not_called()
```

另写一个完整用例：显示窗口后处理事件循环两次，断言自动检查只调用一次；再次 `show()` 不重复调用，手动触发菜单后调用次数增加为2。

Run: `python -m pytest tests/test_release_workspace.py -q`

Expected: FAIL，菜单和更新流程尚不存在。

- [ ] **Step 4: 实现帮助菜单、关于和检查更新**

`ReleaseMainWindow.__init__` 接收 `update_service: UpdateService | None = None` 与 `auto_check: bool = True`，便于测试；正式默认值由 `build_default_update_service(get_tool_identity())` 创建。帮助菜单包含“检查更新”和“关于”。关于对话框显示版本、完整提交、构建时间和固化仓库；开发构建明确显示“开发构建，不自动检查正式更新”。窗口首次显示后只自动检查一次；手动检查可重复。

- [ ] **Step 5: 实现提示、下载和退出状态机**

发现高版本时显示当前/目标版本、更新说明和字节大小，按钮为“立即更新/稍后提醒”。“稍后提醒”只抑制本进程后续弹窗。立即更新前和下载完成后都重新确认 `_process is None`、没有更新 worker 和未冻结；下载期间若扫描/OTA开始，只保留已验证缓存，不启动安装器。

安装器通过：

```python
started, _pid = QProcess.startDetached(str(installer), installer_arguments(
    parent_pid=os.getpid(),
    log_path=default_installer_log_path(info.version),
))
```

只有 `started is True` 才设置 `_update_exit_requested=True`、禁用项目/文件/扫描/OTA入口并调用 `QApplication.instance().quit()`。`closeEvent` 在该标志下正常退出；OTA 仍运行时继续拒绝退出；普通扫描可按现有规则终止。

- [ ] **Step 6: 验证并提交**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_update_worker.py tests/test_release_workspace.py tests/test_ui_smoke.py tests/test_release_cli.py -q
git add -- src/unified_can_lin_host_tool/ui/update_worker.py src/unified_can_lin_host_tool/ui/release_workspace.py src/unified_can_lin_host_tool/ui/app.py tests/test_update_worker.py tests/test_release_workspace.py tests/test_ui_smoke.py
git commit -m "接入 GUI 自动更新交互"
```

### Task 7: 实现 Inno Setup 父进程等待和覆盖升级门禁

**Files:**
- Modify: `installer/EcuReleaseTool.iss`
- Create: `installer/check_running_processes.ps1`
- Create: `tests/test_installer_contract.py`
- Create: `tests/test_installer_process_guard.py`

**Interfaces:**
- Consumes: `/AUTO_UPDATE`、`/PARENT_PID`、`Local\EcuFirmwareReleaseTool.Run`。
- Produces: 手工安装旧版占用拒绝、自动更新等待指定 GUI 退出、剩余 GUI/CLI 拒绝、自动静默安装成功后启动新 GUI。

- [ ] **Step 1: 写安装脚本文本契约失败测试**

```python
def test_installer_requires_build_supplied_version():
    text = Path("installer/EcuReleaseTool.iss").read_text(encoding="utf-8")
    assert "#ifndef MyAppVersion" in text
    assert "#error MyAppVersion" in text
    assert "ignoreversion" not in text

def test_auto_update_run_entry_is_not_skipped_when_silent():
    text = Path("installer/EcuReleaseTool.iss").read_text(encoding="utf-8")
    assert "Check: IsAutoUpdate" in text
    assert "skipifnotsilent" in text
    assert "CheckForMutexes" in text
    assert "PARENT_PID" in text
```

Run: `python -m pytest tests/test_installer_contract.py -q`

Expected: FAIL，当前脚本仍硬编码 `0.1.0`、含 `ignoreversion` 和 `skipifsilent` 单一路径。

- [ ] **Step 2: 实现旧版进程路径检查脚本**

`check_running_processes.ps1` 接收 `-InstallDir` 和可选 `-ExcludedPid`，只枚举 `EcuReleaseTool`、`EcuReleaseCLI`；使用 `Get-Process` 的绝对 `Path` 与规范化安装目录比较。发现匹配进程返回10并输出 PID/文件名；无进程返回0；无法查询返回11。不得 Stop-Process。

测试启动当前 Python 作为带伪造名称不可行，因此把纯函数 `Test-EcuReleaseProcessPath` 独立后用 PowerShell 子进程传入路径表测试：安装目录内返回10，目录外同名程序返回0，大小写和尾部反斜杠不影响判断。

- [ ] **Step 3: 改造安装脚本版本和运行项**

安装脚本开头改为：

```pascal
#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build
#endif
#define MyAppName "ECU Firmware Release Tool"
#define MyAppPublisher "Internal Engineering"
```

`[Setup]` 写 `VersionInfoVersion={#MyAppVersion}.0`、`UninstallDisplayName={#MyAppName}`、`CloseApplications=no`、`RestartApplications=no`；`[Files]` 删除 `ignoreversion`。两个 `[Run]` 条目分别为：

```pascal
Filename: "{app}\EcuReleaseTool.exe"; Description: "Launch EcuReleaseTool"; Flags: nowait postinstall skipifsilent; Check: not IsAutoUpdate
Filename: "{app}\EcuReleaseTool.exe"; Flags: nowait skipifnotsilent; Check: IsAutoUpdate
```

- [ ] **Step 4: 实现父 PID 等待和互斥检查**

在 `[Code]` 声明 `OpenProcess(PROCESS_SYNCHRONIZE)`、`WaitForSingleObject`、`CloseHandle`。初始化时严格解析 `/AUTO_UPDATE` 与正整数 `/PARENT_PID=`；自动更新缺父 PID 直接失败。`PrepareToInstall` 顺序固定：

1. 自动模式等待父 PID 最多60000 ms，超时返回中文错误。
2. 调用 `CheckForMutexes('Local\EcuFirmwareReleaseTool.Run')`，仍存在则拒绝。
3. 提取并运行 `check_running_processes.ps1`，任何安装目录内 GUI/CLI 或脚本查询失败都拒绝。
4. 从现有卸载注册表或 EXE 文件版本读取已安装版本；高版本始终拒绝，自动模式同版本拒绝，手工同版本允许修复安装。

卸载入口同样用命名互斥量和进程路径检查拒绝运行中卸载。

- [ ] **Step 5: 编译和验证安装器行为**

Run:

```powershell
$iscc=(Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
& $iscc /DMyAppVersion=0.2.0 installer\EcuReleaseTool.iss
python -m pytest tests/test_installer_contract.py tests/test_installer_process_guard.py -q
```

Expected: Inno 编译成功；文本契约和进程路径测试通过。

- [ ] **Step 6: 白名单提交**

```powershell
git add -- installer/EcuReleaseTool.iss installer/check_running_processes.ps1 tests/test_installer_contract.py tests/test_installer_process_guard.py
git commit -m "实现安装器安全覆盖升级协议"
```

### Task 8: 建立可复现 Windows 构建和产物审计

**Files:**
- Create: `requirements-release.in`
- Create: `requirements-release.lock`
- Modify: `pyproject.toml`
- Create: `release_toolchain.json`
- Create: `third_party/usb2xxx_runtime_source.json`
- Create: `THIRD_PARTY_NOTICES.txt`
- Create: `scripts/release_build.py`
- Create: `scripts/bootstrap_release_tools.ps1`
- Modify: `scripts/build_windows_installer.ps1`
- Create: `tests/test_release_build.py`
- Create: `tests/test_build_windows_installer.py`
- Modify: `tests/test_installer_contract.py`
- Modify: `tests/release/test_build_identity.py`

**Interfaces:**
- Produces: `prepare-build`、`fetch-usb2xxx`、`audit-build` 子命令；GUI/CLI PE 版本资源；带 `_tool_build_identity.json` 的正式产物；`EcuReleaseTool_Setup_0.2.0.exe`。

- [ ] **Step 1: 写发布构建失败测试**

```python
def test_prepare_build_writes_identity_and_pyinstaller_version_files(tmp_path):
    outputs = prepare_build(
        version="0.2.0",
        commit="01" * 20,
        repository="owner/ecu-firmware-release-tool",
        tag="v0.2.0",
        build_time_utc="2026-07-14T12:00:00Z",
        official=True,
        output_dir=tmp_path,
    )
    assert json.loads(outputs.identity.read_text())["officialBuild"] is True
    assert "filevers=(0, 2, 0, 0)" in outputs.gui_version.read_text()

```

同一测试文件必须创建临时 Git 仓库验证三种失败：`v0.2.1` 对应源码 `0.2.0`、标签提交不在默认分支祖先链、工作树存在未追踪发布输入；还要用本地假 SDK 目录把 `USB2XXX.dll` 改动1字节，断言在复制前以 SHA-256 不一致失败。

Run: `python -m pytest tests/test_release_build.py -q`

Expected: FAIL，构建自动化脚本不存在。

- [ ] **Step 2: 锁定 Python 和 Inno Setup 工具链**

`requirements-release.in` 固定直接依赖：

```text
pip==26.1.2
setuptools==82.0.1
wheel==0.47.0
pyinstaller==6.20.0
PySide6==6.11.0
PyYAML==6.0.3
cryptography==46.0.3
pytest==9.0.3
```

生成 Windows x64/Python 3.11 hash 锁：

```powershell
uv pip compile requirements-release.in --python-version 3.11 --python-platform x86_64-pc-windows-msvc --generate-hashes --output-file requirements-release.lock
```

`release_toolchain.json` 固定：Python `3.11.9`、官方 Windows x64 安装介质 URL `https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe`、大小 `26216840`、SHA-256 `5ee42c4eee1e6b4464bb23722f90b45303f79442df63083f05322f1785f5fdde`；Inno Setup `6.7.3`、官方安装介质 URL `https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe`、大小 `10592232`、SHA-256 `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`，以及 `ISCC.exe` SHA-256 `0a8757031b33777e4c9cbffee40f11a5062b36d25cbe144c1db73b6102b80ad7`。同时固定 Gitleaks `8.30.1` Windows x64 压缩包 URL `https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_windows_x64.zip` 和 SHA-256 `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`。

`bootstrap_release_tools.ps1` 只下载到 `D:\Temp\ecu-release-toolchain-bootstrap\`，逐项校验大小/hash和 Authenticode 后，把 Python 安装到 `D:\software\Python311\`、Inno Setup 安装到 `D:\software\InnoSetup\6.7.3\`、Gitleaks 解压到 `D:\software\gitleaks\8.30.1\`。已有工具也要复验版本/hash；不得静默使用 C 盘其它版本。GitHub runner 可跳过本地 Python 安装，但 Inno/Gitleaks 仍走同一 hash 校验入口。

把 `pyproject.toml` 构建依赖固定为 `setuptools==82.0.1` 和 `wheel==0.47.0`；运行时 cryptography 范围调整为 `>=46,<47`，与发布锁一致。正式安装当前包时必须带 `--no-build-isolation`，禁止重新解析浮动构建依赖。

- [ ] **Step 3: 清除全量测试对本机 ARM 编译器的隐式依赖**

`tests/release/test_build_identity.py` 当前在测试中调用 `arm-none-eabi-gcc/objcopy`。改成由 Python `struct.pack` 构造固定 ELF32 little-endian fixture：一个 `PT_LOAD`，节表含空节、`.shstrtab`、100字节 `.fw_identity` 和1字节 `.text`；`.fw_identity` 的 VMA/LMA 为 `0x1000`，二进制以该节起始。保留原来的 ELF/BIN 交叉校验和损坏 BIN 负向用例，不再调用外部进程。

核心 fixture 入口固定为：

```python
def write_identity_fixture(tmp_path: Path, identity: bytes) -> tuple[Path, Path]:
    assert len(identity) == 100
    elf_header = struct.Struct("<16sHHIIIIIHHHHHH")
    program_header = struct.Struct("<IIIIIIII")
    section_header = struct.Struct("<IIIIIIIIII")
    names = b"\0.shstrtab\0.fw_identity\0.text\0"
    identity_name = names.index(b".fw_identity")
    text_name = names.index(b".text")
    shstr_name = names.index(b".shstrtab")
    phoff, data_offset, names_offset, shoff = elf_header.size, 0x100, 0x180, 0x200
    image = bytearray(shoff + 4 * section_header.size)
    ident = b"\x7fELF" + bytes([1, 1, 1]) + b"\0" * 9
    elf_header.pack_into(
        image, 0, ident, 2, 40, 1, 0x1064, phoff, shoff, 0,
        elf_header.size, program_header.size, 1, section_header.size, 4, 1,
    )
    program_header.pack_into(
        image, phoff, 1, data_offset, 0x1000, 0x1000, 101, 101, 5, 4,
    )
    image[data_offset:data_offset + 100] = identity
    image[data_offset + 100] = 0
    image[names_offset:names_offset + len(names)] = names
    sections = [
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (shstr_name, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0),
        (identity_name, 1, 2, 0x1000, data_offset, 100, 0, 0, 4, 0),
        (text_name, 1, 6, 0x1064, data_offset + 100, 1, 0, 0, 1, 0),
    ]
    for index, values in enumerate(sections):
        section_header.pack_into(image, shoff + index * section_header.size, *values)
    elf_path = tmp_path / "identity.elf"
    bin_path = tmp_path / "identity.bin"
    elf_path.write_bytes(image)
    bin_path.write_bytes(identity + b"\0")
    assert len(image) == shoff + 4 * section_header.size
    assert bin_path.read_bytes()[:100] == identity
    return elf_path, bin_path
```

实现中必须把 `elf_path` 和 `bin_path` 写成 `tmp_path` 下的实际文件，并在返回前断言 ELF 长度覆盖完整节表、BIN 前100字节等于 `identity`。运行 `python -m pytest tests/release/test_build_identity.py -q`，预期不依赖 PATH 中的 ARM 工具仍全部通过。

- [ ] **Step 4: 固定 USB2XXX 来源与第三方说明**

`third_party/usb2xxx_runtime_source.json` 写官方仓库、提交、x64 相对路径、两个文件大小和既定 SHA-256。`fetch-usb2xxx` 只允许克隆该 URL、检出该提交、逐文件验证大小/hash，再复制到 `build/third_party/usb2xxx/d1fd307a72cad0c71aa81db79ab413b8e7a26175/`；本机缓存也必须复验。`THIRD_PARTY_NOTICES.txt` 写明来源、提交、hash、版权归原权利人、未发现明确再分发许可及项目负责人决定公开分发的风险边界。同星 DLL 不复制。

- [ ] **Step 5: 实现构建身份、PE 资源和审计脚本**

`scripts/release_build.py` 必须实现 `read_project_version(pyproject_path: Path) -> SemanticVersion`、`validate_release_git_state(repo: Path, tag: str, default_branch_ref: str) -> None`、`write_tool_identity(output_dir: Path, identity: ToolIdentity) -> Path`、`write_pyinstaller_version_file(output: Path, identity: ToolIdentity, description: str) -> None`、`fetch_usb2xxx_runtime(source_file: Path, output_dir: Path) -> tuple[Path, Path]` 和 `audit_windows_build(dist_dir: Path, installer: Path, identity: ToolIdentity) -> dict[str, object]`。

同时定义 `BuildOutputs(identity: Path, gui_version: Path, cli_version: Path)` 和 `prepare_build(*, version: str, commit: str, repository: str, tag: str, build_time_utc: str, official: bool, output_dir: Path) -> BuildOutputs`，与本任务前面的测试签名保持一致。

正式模式只接受 `GITHUB_ACTIONS=true`、`GITHUB_REPOSITORY`、`GITHUB_SHA`、`GITHUB_REF_NAME` 和 Actions 查询到的默认分支；完整 fetch 后用 `git merge-base --is-ancestor` 门禁。开发模式写 `officialBuild=false`，不得伪造仓库。

- [ ] **Step 6: 改造统一 Windows 构建入口**

`build_windows_installer.ps1` 顺序固定：校验 Python 3.11.9和 `pip check`；用 `pip install --no-deps --no-build-isolation .` 安装当前干净检出并让 PyInstaller `--copy-metadata unified-can-lin-host-tool`；调用 `release_build.py prepare-build`；取得并验证 USB2XXX DLL；为 GUI/CLI 传各自 `--version-file`、身份 JSON 和公钥 JSON；构建后用 Windows `VersionInfo` 审计两个 EXE；以 `/DMyAppVersion=0.2.0` 调用已验证 ISCC；审计安装包名、大小、hash和版本；输出 `dist/release-audit.json`。任何依赖、DLL、版本或 hash 不一致立即失败。

- [ ] **Step 7: 运行可复现构建验证**

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap_release_tools.ps1
& 'D:\software\Python311\python.exe' -m pip install --require-hashes -r requirements-release.lock
$env:PYTHONPATH=(Resolve-Path 'src').Path
& 'D:\software\Python311\python.exe' -m pytest tests/test_release_build.py tests/test_build_windows_installer.py tests/test_installer_contract.py -q
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows_installer.ps1 -PythonPath 'D:\software\Python311\python.exe' -IsccPath 'D:\software\InnoSetup\6.7.3\ISCC.exe'
& .\dist\windows\EcuReleaseCLI.exe --version
(Get-Item .\dist\windows\EcuReleaseTool.exe).VersionInfo | Select-Object FileVersion,ProductVersion,ProductName,FileDescription
```

Expected: CLI 和两个 PE 属性均为 `0.2.0`/`0.2.0.0`；安装包名为 `EcuReleaseTool_Setup_0.2.0.exe`；审计 JSON 记录同一提交与 hash。

- [ ] **Step 8: 白名单提交**

```powershell
git add -- pyproject.toml requirements-release.in requirements-release.lock release_toolchain.json third_party/usb2xxx_runtime_source.json THIRD_PARTY_NOTICES.txt scripts/bootstrap_release_tools.ps1 scripts/release_build.py scripts/build_windows_installer.ps1 tests/release/test_build_identity.py tests/test_release_build.py tests/test_build_windows_installer.py tests/test_installer_contract.py
git commit -m "建立可复现 Windows 发布构建"
```

### Task 9: 建立 GitHub CI、签名发布和公开说明

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Create: `.gitleaks.toml`
- Modify: `scripts/release_signing.py`
- Create: `docs/releases/0.2.0.md`
- Modify: `README.md`
- Create: `tests/test_github_workflows.py`
- Modify: `tests/test_release_signing.py`

**Interfaces:**
- Produces: 普通提交只测试/构建；标签构建任务无私钥；`release` Environment 发布任务签名并公开 Release。
- Produces: `update.json`、64字节 `update.json.sig`、`SHA256SUMS.txt`、安装包和第三方说明。

- [ ] **Step 1: 写工作流权限和固定 Action 提交测试**

```python
PINNED_ACTIONS = {
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
}

def test_release_build_job_has_no_secret_and_publish_job_uses_environment():
    text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    build_text, publish_text = text.split("  publish:", 1)
    assert "secrets." not in build_text
    assert "environment: release" in publish_text
    assert "UPDATE_SIGNING_KEY_PEM: ${{ secrets.UPDATE_SIGNING_KEY_PEM }}" in publish_text

def test_all_actions_are_pinned_to_expected_full_commit():
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in [".github/workflows/ci.yml", ".github/workflows/release.yml"]
    )
    for action, commit in PINNED_ACTIONS.items():
        assert f"uses: {action}@{commit}" in text
    assert not re.search(r"uses:\s+actions/[^@]+@v[0-9]", text)

def test_manual_dispatch_cannot_publish():
    text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" not in text
    assert "tags:" in text and "v*.*.*" in text
```

Run: `python -m pytest tests/test_github_workflows.py -q`

Expected: FAIL，工作流不存在。

- [ ] **Step 2: 实现 CI 工作流**

`.github/workflows/ci.yml` 触发默认分支/其它分支 push 和 Pull Request，顶层 `permissions: contents: read`。Windows job 的显示名固定为 `Windows test and build`，与 Task 11 分支保护状态名一致；它使用 Python 3.11.9、`pip install --require-hashes -r requirements-release.lock`、固定 hash 的 Gitleaks 8.30.1、全量 pytest、统一安装包构建和产物审计；不创建 Release，不读取任何 secret。Gitleaks 对当前树和完整 Git 历史运行，只有 `.gitleaks.toml` 的精确开发台架常量豁免可通过。

- [ ] **Step 3: 实现标签发布工作流**

`.github/workflows/release.yml` 只触发 `v*.*.*` 标签。`build` job 权限只读，完整检出历史，运行标签/默认分支/版本门禁、测试、构建和审计，再上传已审计安装包与 audit 文件。`publish` job：

```yaml
needs: build
environment: release
permissions:
  contents: write
env:
  UPDATE_SIGNING_KEY_PEM: ${{ secrets.UPDATE_SIGNING_KEY_PEM }}
```

它下载同一 workflow run 的产物，复算 SHA-256，从私钥推导公钥并对比 `release-v1`，生成规范 `update.json`、签名和 SHA256SUMS；先确认标签 Release 不存在，再用 `gh release create --draft --verify-tag`，上传资源，使用 GitHub Release API 的 asset `digest`/大小回读核对，最后 `gh release edit --draft=false --latest`。失败时不得公开草稿。

- [ ] **Step 4: 完成签名生成和固定发布说明**

`release_signing.py` 增加 `build_update_json(*, repository: str, version: str, commit: str, generated_at: str, release_notes: str, installer: Path, key_id: str) -> bytes` 和 `write_sha256sums(files: Sequence[Path], output: Path) -> None`。核心编码固定为：

```python
def build_update_json(*, repository, version, commit, generated_at, release_notes, installer, key_id):
    parsed = SemanticVersion.parse(version)
    payload = {
        "schemaVersion": 1,
        "repository": repository,
        "version": str(parsed),
        "tag": f"v{parsed}",
        "commit": commit,
        "generatedAt": generated_at,
        "channel": "stable",
        "releaseNotes": release_notes,
        "installer": {
            "name": installer.name,
            "size": installer.stat().st_size,
            "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
        },
        "keyId": key_id,
    }
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

def write_sha256sums(files, output):
    rows = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in files]
    output.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
```

JSON 用 UTF-8 无字节顺序标记、固定字段顺序、`separators=(",", ":")` 和末尾单个换行；`generatedAt` 是签名任务生成时间，`channel` 固定 `stable`。发布说明来自 `docs/releases/0.2.0.md`，不得从未审计的网络文本写入已签名字段。

- [ ] **Step 5: 写公开仓库安全边界**

README 增加安装、`--version`、GitHub 更新、未知 Windows 发布者、USB2XXX 来源与再分发风险、同星 DLL 不随包发布、公开固件开发凭据只限台架、量产禁止使用等说明。仓库不新增开源许可证文件；明确“源码公开可查看不等于授予额外开源许可，第三方组件服从其各自权利”。`.gitleaks.toml` 只对 `development_keys.py` 中已列明的两个开发台架常量做精确路径和正则豁免，其它私钥/令牌继续失败。

- [ ] **Step 6: 验证并提交**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
& 'D:\software\Python311\python.exe' -m pytest tests/test_github_workflows.py tests/test_release_signing.py -q
& 'D:\software\Python311\python.exe' -m pytest -q
git diff --check
git add -- .github/workflows/ci.yml .github/workflows/release.yml .gitleaks.toml scripts/release_signing.py docs/releases/0.2.0.md README.md tests/test_github_workflows.py tests/test_release_signing.py
git commit -m "建立 GitHub 标签签名发布流程"
```

### Task 10: 安装升级、自动更新和设备扫描集成验证

**Files:**
- Create: `tests/integration/test_update_closed_loop.py`
- Runtime evidence only: `D:\software\EcuReleaseTool\upgrade-baselines\0.1.0\`
- Runtime evidence only: `D:\Temp\ecu-release-update-validation\`

**Interfaces:**
- Consumes: 已知 `0.1.0` 安装包、正式 `0.2.0` 构建、测试密钥和注入式本地响应。
- Produces: 覆盖安装、自动更新退出协议、安装失败重试和真实设备扫描证据。

- [ ] **Step 1: 长期保存已知 `0.1.0` 基线**

先向实际分发人员核对是否还有其它 `0.1.0` 文件；对每个可取得样本计算大小和 SHA-256，并以 hash 命名保存到 `D:\software\EcuReleaseTool\upgrade-baselines\0.1.0\`。当前已确认样本从 `dist/installer/EcuReleaseTool_Setup_0.1.0.exe` 复制为 `ea76908b0ee07e8a6c7b3d5a81d0eba2daa151bb77bc1f287d3d55c6089674b3.exe`，复制前后都验证大小 `64654967` 和既定 SHA-256。不得加入 Git；后续覆盖安装对每个已取得样本重复执行。

- [ ] **Step 2: 写注入式闭环集成测试**

`tests/integration/test_update_closed_loop.py` 用测试密钥和内存 HTTPS 客户端完成：`0.2.0` 定位 `0.2.1`、标签确定 JSON/签名、下载 `.part`、hash、原子改名、安装参数。覆盖最新标签切换、缓存陈旧、签名错误、hash错误、取消、同版本、降级、多个运行互斥句柄和安装器启动失败；正式服务构造函数不得接收可由用户配置的仓库 URL。

Run: `& 'D:\software\Python311\python.exe' -m pytest tests/integration/test_update_closed_loop.py -q`

Expected: 全部通过，网络和安装进程均由测试替身承接。

- [ ] **Step 3: 备份现有安装状态并做真实 `0.1.0 -> 0.2.0` 覆盖**

先记录 HKCU 卸载项、`D:\software\EcuReleaseTool` 文件清单、用户配置和日志 hash 到 `D:\Temp\ecu-release-update-validation\before\`。确认 GUI/CLI 未运行后执行正式 `0.2.0` 安装包；验证 `AppId` 对应同一卸载项、InstallLocation 未变化、DisplayVersion 为 `0.2.0`、用户配置/日志未删除、两个 EXE 版本一致。安装失败立即停止，不删除旧安装或备份。

- [ ] **Step 4: 验证运行占用和自动模式**

分别启动新 GUI、直接 CLI 和两个 GUI 实例，确认手工安装器与自动安装器在非指定进程仍存在时拒绝且不强杀。用测试专用副本和独立测试 `AppId` 验证 `/AUTO_UPDATE /PARENT_PID` 会等待父进程退出、静默成功后启动新 GUI、写入失败可再次运行；测试副本不得覆盖真实卸载项。

- [ ] **Step 5: 验证安装版命令与设备扫描**

Run:

```powershell
& 'D:\software\EcuReleaseTool\EcuReleaseCLI.exe' --version
& 'D:\software\EcuReleaseTool\EcuReleaseCLI.exe' scan --project AS5PR --adapter usb2xxx
& 'D:\software\EcuReleaseTool\EcuReleaseCLI.exe' scan --project AS5PR --adapter tsmaster
```

Expected: `--version` 显示 `0.2.0` 与正式提交；扫描命令能直接执行而不依赖系统 `PATH`。有设备时列出 SDK 设备/通道；无设备时稳定返回退出码3和明确“0通道/SDK原因”，不得把环境问题误报成更新失败。

- [ ] **Step 6: 全量门禁与白名单提交**

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:QT_QPA_PLATFORM='offscreen'
& 'D:\software\Python311\python.exe' -m pytest -q
git diff --check
git status --short
git add -- tests/integration/test_update_closed_loop.py
git commit -m "验证自动更新与覆盖安装闭环"
```

### Task 11: 建立公开 GitHub 仓库并发布 `v0.2.0`

**Files:**
- Git and GitHub external state only; no source edits expected after release gate.

**Interfaces:**
- Produces: 公开 `ecu-firmware-release-tool`、默认分支保护、`release` Environment 私钥、不可修改 Release、正式 `v0.2.0`。

- [ ] **Step 1: 最终公开前审计**

确认工作树干净、任务提交均在当前 HEAD、当前提交是本地默认分支可快进后继。下载 Gitleaks 8.30.1 Windows x64 到 `D:\software\gitleaks\8.30.1\` 前先核对压缩包 SHA-256 `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`，随后运行 `gitleaks git . --log-opts="--all" --config=.gitleaks.toml --redact`；只允许配置中明确列出的开发台架常量。检查 Git 对象中无 `D:\software` 私钥、`.erel`、固件私密资源、安装包、DLL、个人令牌和构建目录。

- [ ] **Step 2: 安装/确认 GitHub CLI 身份并创建仓库**

GitHub CLI 必须安装到 `D:\software`。运行 `gh auth status` 和 `gh api user --jq .login`；未认证时停止外部发布并要求用户完成网页登录，不尝试读取或打印凭据。认证后：

```powershell
$owner=(gh api user --jq .login).Trim()
$repo="$owner/ecu-firmware-release-tool"
gh repo create $repo --public --description 'ECU firmware release, CAN device scan and OTA tool for Windows'
git remote add origin "https://github.com/$repo.git"
git push origin HEAD:master
gh repo edit $repo --default-branch master
```

若账号策略创建的默认分支不是 `master`，读取 `gh repo view $repo --json defaultBranchRef` 后使用实际默认分支，工作流和标签门禁不得硬编码分支名。

- [ ] **Step 3: 配置保护、Environment 和不可修改 Release**

对实际默认分支启用禁止强推/删除、需要 CI 状态通过的保护。创建 `release` Environment，并从本机私钥文件写入 Environment secret，命令不得回显内容：

```powershell
$branch=(gh repo view $repo --json defaultBranchRef --jq .defaultBranchRef.name).Trim()
$protection=@{
  required_status_checks=@{strict=$true;contexts=@('Windows test and build')}
  enforce_admins=$true
  required_pull_request_reviews=$null
  restrictions=$null
  required_linear_history=$false
  allow_force_pushes=$false
  allow_deletions=$false
  block_creations=$false
  required_conversation_resolution=$false
  lock_branch=$false
  allow_fork_syncing=$true
} | ConvertTo-Json -Depth 6
$protection | gh api --method PUT -H 'X-GitHub-Api-Version: 2026-03-10' "repos/$repo/branches/$branch/protection" --input -
Get-Content -Raw -LiteralPath D:\software\EcuReleaseTool\release-keys\release-v1.pem | gh secret set UPDATE_SIGNING_KEY_PEM --env release --repo $repo
gh api --method PUT -H 'X-GitHub-Api-Version: 2026-03-10' "repos/$repo/immutable-releases"
gh api -H 'X-GitHub-Api-Version: 2026-03-10' "repos/$repo/immutable-releases"
```

Expected: 不可修改 Release 查询成功；私钥只在本机受限文件和 GitHub Environment secret。

- [ ] **Step 4: 验证默认分支 CI 后推送标签**

等待默认分支 CI 完整通过，再确认远程没有 `v0.2.0` 标签或 Release：

```powershell
git ls-remote --tags origin refs/tags/v0.2.0
gh release view v0.2.0 --repo $repo
```

两者都应为空/未找到。随后执行：

```powershell
git tag -a v0.2.0 -m 'ECU Firmware Release Tool 0.2.0'
git push origin v0.2.0
```

- [ ] **Step 5: 审计实际 GitHub Release**

等待 release workflow 成功；核对 Release 非 draft、非 prerelease、immutable，资源名/大小/digest与本地审计一致。匿名下载：

```powershell
$base="https://github.com/$repo/releases/latest/download"
Invoke-WebRequest "$base/update.json" -OutFile D:\Temp\ecu-release-update-validation\github\update.json
Invoke-WebRequest "$base/update.json.sig" -OutFile D:\Temp\ecu-release-update-validation\github\update.json.sig
```

用内置 `release-v1` 公钥验签并确认 `repository=$repo`、`version=0.2.0`、`channel=stable`、安装包 SHA-256 一致。

- [ ] **Step 6: 发布后安装与报告**

从 GitHub Release 下载正式安装包，复算签名更新信息中的大小/hash，再对现有 `0.1.0` 做最后一次人工覆盖安装。报告仓库链接、Release 链接、标签提交、安装包 hash、测试/构建命令、真实扫描结果、Windows 未签名警告和 USB2XXX 再分发风险；不得声明未执行的 `0.2.0 -> 0.2.1` 真实 Release 更新已经验证。

## 规格覆盖复核

| 设计要求 | 实施任务与证据 |
|---|---|
| 仓库、标签和版本唯一性 | Task 2、8、9、11；版本单元测试、Git 门禁、PE/安装包审计、远程标签审计 |
| GUI/CLI 可见版本和日志身份 | Task 2、6、8；CLI `--version`、窗口标题、JSON 事件、PE 属性 |
| 构建与签名权限隔离 | Task 3、8、9；无私钥 build job、受保护 Environment publish job、公钥反推比对 |
| USB2XXX 固定来源与权利边界 | Task 1、8、9；提交/hash 门禁、第三方说明、README 风险说明 |
| 固件开发凭据公开边界 | Task 9、11；README 警告、精确秘密扫描豁免、全历史扫描 |
| 最新标签定位与标签确定资源 | Task 3、4；先验签后信任、Release 切换/缓存/重定向测试 |
| 下载缓存和安装包完整性 | Task 5；大小、SHA-256、原子替换、中断清理和复用校验 |
| 扫描/OTA/CLI 与更新互斥 | Task 5、6、7、10；命名互斥量、GUI 双重门禁、安装进程占用验证 |
| `0.1.0` 覆盖升级和自动重启 | Task 7、10；父 PID 等待、旧版路径检查、同 AppId/目录/卸载项验证 |
| 可复现构建与 Windows 安装包 | Task 8；Python/Inno/Gitleaks 固定介质、hash 锁、统一构建入口和审计 JSON |
| 正式 Release 不可修改和匿名下载 | Task 9、11；GitHub 设置、草稿后公开、API digest、latest 资源匿名验签 |
| 断网与安全失败不阻塞 ECU 业务 | Task 4、5、6、10；稳定错误码、异步 worker、失败不提供绕过安装 |
| 非目标：无服务、无强制更新、无自动回滚 | Task 5~7 只实现用户确认、一次性进程和失败重试，不新增常驻进程或回滚框架 |

## 最终自审清单

- [ ] 设计说明第1~16节每项都有对应任务或明确非目标。
- [ ] 计划没有未定义接口、临时占位、同名不同签名或运行时可替换正式仓库的入口。
- [ ] 子代理文件边界顺序无重叠：Task 1先清理脏基线；Task 2后才能修改 CLI/GUI；Task 3~5按依赖顺序；Task 6在服务稳定后修改 GUI；Task 7/8在运行接口稳定后修改安装/构建；Task 9~11最后执行。
- [ ] 每个提交前运行对应测试与 `git diff --check`，只白名单暂存本任务文件。
- [ ] 正式标签前全量测试、Windows 构建、PE 审计、覆盖安装、进程互斥、秘密扫描和 GitHub 设置全部有可追溯证据。
