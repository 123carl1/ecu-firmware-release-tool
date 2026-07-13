# E68 LIN / AS5PR CAN 固件发布与 OTA 工具设计

日期：2026-07-13  
状态：两轮审查后定稿，进入实现

## 1. 目标

在同一 Windows 工具中提供 E68 LIN 与 AS5PR CAN 固件发布能力，并让图形界面、命令行和 BAT 拖拽入口共用同一应用服务、同一身份链和同一 OTA 状态机。

本阶段完成后：

1. AS5PR 可以从单一 `.erel` 发布资源包执行检查、dry-run、完整镜像合并和 TSMaster 实机 OTA。
2. E68 可以完成发布资源包检查、fake/dry-run 和界面流程验证；在缺少可信资源包及 ECU 身份 DID 时禁止真实擦除。
3. App、Boot、FlashDriver、地址布局、认证参数和项目代码必须来自同一次构建，不能手工混搭。
4. 真实擦除前必须证明发布资源包、程序内项目发布配置和在线 ECU 属于同一目标。
5. OTA 完成必须包含 ECU 认证、复位后身份复核和新鲜通信验证，不能以 TransferExit 成功代替最终成功。

## 2. 非目标

- 本阶段不制作 Windows 安装包。
- 本阶段不接入 USB2XXX 实机刷写。
- 本阶段不提供量产密钥设施。
- 本阶段不允许从界面修改 CAN ID、LIN NAD、地址布局、SeedKey 算法或身份 DID。
- 本阶段不为旧外部参数文件和旧分散资源接口保留兼容入口；工具尚未正式发布，直接迁移到新契约。

## 3. 总体架构

```text
PySide6 图形界面 / ecu-release 命令行 / BAT
                       │
                       ▼
               ReleaseApplicationService
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  发布资源包服务   项目发布配置库   命令/事件/结果模型
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                ECU 身份探测服务
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       E68 LIN OTA 状态机   AS5PR CAN OTA 状态机
             │                   │
             └────── TSMaster 总线适配器 ──────┘
```

图形界面、命令行和 BAT 只负责收集输入、显示事件和返回退出码。项目差异全部来自程序内只读项目发布配置；总线适配器只处理硬件和帧，不包含项目业务常量。

## 4. 项目发布配置

### 4.1 数据模型

程序内置 `ProjectReleaseConfig` 不可变对象：

- `project_code`：稳定项目代码，AS5PR 为 `0x41503541`，E68 必须由其 Bootloader 契约给出，禁止从 AS5PR 推导。
- `config_version`：项目发布配置版本。
- `bus`：CAN/LIN 类型、波特率、请求/响应 ID、functional ID、padding 或 NAD。
- `memory`：Boot、AppValid、App、FlashDriver RAM 范围和页大小。
- `authentication`：App/FlashDriver targetId、认证格式版本、算法编号和最小版本。
- `identity_did`：DID、响应长度、项目代码偏移、角色偏移和配置版本偏移。
- `timing`：P2、P2*、轮询超时、帧间隔和复位后验证窗口。
- `workflow`：项目 OTA 服务序列及允许的正响应。
- `communication_check`：复位后的 DID 和周期报文验证规则。

配置摘要使用唯一规范化编码：根节点和嵌套 mapping 的 key 必须是非空 ASCII 字符串且不可重复；字符串值先做 NFC，再要求全部为 ASCII；整数类型与布尔类型严格区分，整数范围固定为 `0..0xFFFFFFFF`；tuple 保持声明顺序；禁止 list、float、负数、null、NaN 和 Infinity。JSON 编码固定使用 UTF-8、key 按 ASCII 字节升序、`ensure_ascii=True`、分隔符 `(',', ':')`、无尾换行；控制字符、引号和反斜杠使用小写 `\u00xx` 或 JSON 固定短转义，`/` 不转义。编码前加 ASCII 域分隔符 `PROJECT_RELEASE_CONFIG_V1\0`，然后计算 `config_digest = SHA-256(domain || canonical_json)`。必须提供 AS5PR 固定输入、规范化字节和期望摘要测试向量。发布资源包携带该摘要；加载时与程序内计算值逐字节比较。

### 4.2 配置边界

- 用户只能选择 E68 或 AS5PR，不能加载外部参数文件。
- 高级设置只允许修改 TSMaster DLL 路径和硬件通道映射。
- 项目业务常量不得进入 TSMaster 适配器、GUI 控件或 BAT。
- 新增项目必须通过新增代码、固定测试向量和评审进入，不允许运行时执行表达式或脚本。

## 5. `.erel` 发布资源包

### 5.1 单文件结构

```text
固定文件头
资源表
4-byte 对齐填充
Boot 数据
App 数据
FlashDriver 数据
签名尾
```

全部整数使用 little-endian。Ed25519 签名输入为“签名字段之前的全部原始字节”，即固定文件头、资源表、填充、三个资源以及签名尾的 `magic || keyId`；只排除最后64字节 signature，因此 keyId 受签名保护。

### 5.2 固定文件头 V1

| 字段 | 长度 | 约束 |
|---|---:|---|
| magic | 4 | ASCII `EREL` |
| schemaVersion | 2 | 固定 `1` |
| headerSize | 2 | V1 固定 `300`，即132字节文件头加3×56字节资源表 |
| packageSize | 4 | 包含签名尾的总长度 |
| projectCode | 4 | 必须等于项目发布配置 |
| configVersion | 2 | 必须等于项目发布配置 |
| entryCount | 2 | 当前固定为 `3` |
| configDigest | 32 | 项目发布配置 SHA-256 |
| buildId | 32 | 同一次受控发布构建生成的随机标识，三个资源必须一致 |
| buildCommit | 40 | 恰好40字符小写 ASCII Git commit；非十六进制拒绝，校验时解码成20原始字节与资源身份块比较 |
| buildTimestamp | 8 | Unix 秒，仅用于审计，不参与新旧版本判断 |

### 5.3 资源表项 V1

每项固定 56 字节：

| 字段 | 长度 | 约束 |
|---|---:|---|
| kind | 2 | `1=Boot, 2=App, 3=FlashDriver` |
| flags | 2 | V1 固定为 `0` |
| targetId | 4 | Boot 使用 projectCode；App/FlashDriver 使用各自认证块 targetId |
| loadAddress | 4 | 必须位于对应内存范围 |
| contentOffset | 4 | 4-byte 对齐，不得指向头部或签名尾 |
| contentLength | 4 | 非零且不得越界 |
| authVersion | 4 | Boot 固定为 `0`；App/FlashDriver 与各自认证块 version 一致 |
| contentSha256 | 32 | 资源原始字节 SHA-256 |

资源表必须按 kind 升序排列，kind 不得重复。V1 必须同时包含 Boot、App、FlashDriver。

V1 唯一布局规则：

1. 实际文件长度必须严格等于 `packageSize`，`packageSize` 不得超过 `0xFFFFFFFF`。
2. 签名尾固定为最后72字节，起始偏移严格等于 `packageSize - 72`；签名尾后禁止任何字节。
3. 第一个资源偏移固定为 `300`；后续资源偏移固定为前一资源末尾向4字节对齐的结果。
4. 资源数据按 Boot、App、FlashDriver 顺序排列；只允许对齐产生的0到3字节间隔，填充值固定为 `0x00`，禁止其它空洞。
5. FlashDriver 末尾同样补 `0x00` 到4字节对齐，随后立即开始签名尾。
6. 解析器对所有 `offset + length` 使用 checked u32 arithmetic，并验证 `offset <= signatureOffset` 且 `length <= signatureOffset - offset`。
7. 先以实际文件尾定位签名尾，校验 magic、已知 keyId 和 Ed25519 签名；签名通过后才能使用资源偏移、地址、hash 或项目字段。
8. 文件头保留位、资源 flags、非规范顺序、非零填充、重复资源、尾随字节和任一非唯一编码全部拒绝。

### 5.4 签名尾 V1

| 字段 | 长度 | 约束 |
|---|---:|---|
| magic | 4 | ASCII `SIG1` |
| keyId | 4 | 验证公钥编号 |
| signature | 64 | Ed25519 |

`release_set_id = SHA-256(完整 .erel 文件)`。验证公钥固化在程序中；私钥只通过构建环境变量或显式本机文件传入，不进入 Git、不进入安装目录、不写入日志。

### 5.5 AS5PR 认证块字节契约

App 与 FlashDriver 资源末尾都包含48字节认证块，全部整数为 little-endian：

```text
authHeader = magic:u32 || payloadSize:u32 || targetId:u32 || version:u32
authBlock  = authHeader || hmacSha256:bytes32
hmacSha256 = HMAC-SHA256(developmentKey, normalizedPayload || authHeader)
resource   = normalizedPayload || authBlock
```

`magic` 固定为 `0xA5A5A5A5`；`payloadSize` 必须严格等于认证块前的 payload 字节数；App 和 FlashDriver 分别使用项目配置中的 targetId/version。认证块自身不属于 normalizedPayload，末尾32字节 HMAC 不参与 HMAC 输入。打包、导入、合并和刷写前都必须重新计算并做常量时间比较。共享开发密钥只用于防止开发阶段误操作，不是量产发布安全边界。

### 5.6 构建链

开发用命令行提供 `ecu-release build-package`，由固件仓库发布脚本调用。发布脚本在一次构建开始时生成32字节随机 `buildId`，并生成只供本次构建使用的 `ReleaseBuildIdentity_Generated.h`。Boot、App、FlashDriver 都通过各自链接脚本的 `KEEP(*(.fw_identity))` 放置恰好100字节 `BuildIdentityV1`：Boot/App 位于中断向量表之后、普通代码之前，FlashDriver 位于驱动入口表之后、普通代码之前；链接脚本导出 `__fw_identity_start__` 和 `__fw_identity_end__`，并用链接断言保证长度为100字节且位于对应镜像装载范围内。禁止使用以 `.rel` 开头的节名，因为 GNU assembler 会将其识别为 relocation section。

```text
magic:bytes4 = "RBID"
schemaVersion:u16 = 1
resourceKind:u16
projectCode:u32
configVersion:u16
reserved:u16 = 0
configDigest:bytes32
buildId:bytes32
buildCommit:bytes20
```

三个镜像的 `projectCode`、`configVersion`、`configDigest`、`buildId` 和20字节原始 `buildCommit` 必须逐字节一致，`resourceKind` 必须分别为 Boot、App、FlashDriver。包头40字符提交号必须严格小写十六进制解码后再与20字节原始值比较。App 与 FlashDriver 的 HMAC 覆盖区间包含该身份块；Boot 由整包 Ed25519 签名覆盖。公共布局与项目常量固定在固件仓库 `Shared/ReleaseIdentity/Inc/ReleaseIdentity_Cfg.h`，一次性生成头只包含本次构建身份且不提交 Git。

`build-package` 不接受三个任意文件路径，只接受受控构建输出目录、项目、HMAC 密钥引用、Ed25519 私钥引用和输出路径；三个输入文件名由项目发布配置固定。它完成：

1. 从固定输出目录读取三个 ELF 和三个 BIN，按 ELF 的 `.fw_identity` 节及导出符号定位身份块，验证该节具有装载属性、BIN 对应装载地址的100字节完全相同，并验证同一次构建、正确资源角色和内部项目配置摘要；缺 ELF、节重复、magic 在其它位置重复或 BIN/ELF 不一致均拒绝。
2. 对 App 与 FlashDriver 生成 HMAC 认证块，再重新验证 HMAC、地址、长度、各自 targetId 和版本；Boot 不含 HMAC 认证块，表项 `authVersion=0`。
3. 在输出同目录生成唯一临时文件。
4. 写入文件头、资源表、资源和签名尾。
5. 重新解析、逐资源 hash、构建身份和整包签名复验。
6. 同卷原子替换目标 `.erel`。

构建命令存在于开发命令行，不出现在图形界面；通用工具不携带私钥。

## 6. 制品身份和会话存储

现有第一阶段身份编码升级为 `ArtifactIdentityV2`：

- schema version。
- project code、config version、config digest。
- release set ID。
- App 原始文件 SHA-256。
- 规范化地址范围、gap fill 和有序段表。
- 规范化 payload SHA-256。
- App targetId、认证版本和签名策略编号。

`ArtifactIdentityV2` 使用以下唯一编码，整数均为 little-endian：

```text
"ARTIFACT_IDENTITY_V2\0"
schemaVersion:u16 (=2)
projectCode:u32
configVersion:u16
configDigest:bytes32
releaseSetId:bytes32
sourceFileSha256:bytes32
normalizationStart:u32
normalizationEnd:u32
gapFill:u8
targetId:u32
authVersion:u32
signPolicyIdLength:u16 + UTF-8 bytes
segmentCount:u32
repeated segmentAddress:u32 + segmentLength:u32 + segmentSha256:bytes32
normalizedPayloadSha256:bytes32
```

`normalizationStart` 和 `normalizationEnd` 表示半开区间 `[start, end)`，必须满足 `end > start` 且 `end - start` 可用 u32 表示。每个段长度必须非零，段区间必须完全位于规范化区间内；段按地址严格递增、不得重叠，`segmentLength` 必须等于实际段数据长度，`segmentSha256` 必须由实际段数据重算。规范化 payload 按地址顺序放置段数据，并以单字节 `gapFill` 填充首尾及段间空洞，最终长度必须严格等于 `end - start`，其 hash 现场重算。`signPolicyIdLength` 是 NFC 后 UTF-8 字节长度且不得超过 `0xFFFF`；hash 必须恰好32字节，不接受大小写不一或带前缀的文本替代原始字节。`ArtifactId = SHA-256(encoded_identity)`，并提供包含空洞和多字节策略名的固定测试向量。

`SignedArtifactId = SHA-256("SIGNED_ARTIFACT_V2\0" || ArtifactId原始32字节 || signedFileSha256 || authBlockSha256 || releaseSetId原始32字节)`。任何字段缺失、长度错误或文本形式 hash 未规范化均拒绝。

会话存储只保存受控相对路径和允许字段。加载时重新读取 `.erel`，复验整包签名、项目发布配置摘要、资源 hash、App 认证块、ArtifactId 和 SignedArtifactId；JSON 只作传输格式，不作为可信输入。

### 6.1 完整镜像与 AppValid

完整镜像合并只消费会话存储中由同一 `.erel` 导入并复验得到的 Boot 资源和 SignedArtifact，不接受松散 Boot/App 文件。AS5PR AppValid 页契约固定为：起始地址 `0x00006A00`、页长512字节、有效值偏移0、little-endian `0x5AA55AA5`；擦除态整页为 `0xFF`，离线有效态除前4字节外的保留字节也固定为 `0xFF`。

合并策略只有两种：

- `ERASED_APP_VALID`：始终写入整页 `0xFF`，可用于 OTA/普通离线镜像。
- `OFFLINE_PREVALIDATED`：仅用于受控离线烧录；必须重新验证 `.erel` 签名、App SignedArtifactId、App 的 HMAC、payloadSize、appTargetId/version、buildId/configDigest 和向量地址，全部通过后才写入有效值。

任何验证失败都不得自动降级后继续写有效 AppValid；命令必须失败。OTA 始终下载 App 认证资源但不预写 AppValid，只有 Boot 的 `$31 FF01` 认证与向量检查成功后由 ECU 提交有效标志。

## 7. 在线 ECU 身份

### 7.1 身份 DID

AS5PR App 与 Boot 新增只读 DID `0xF1A0`，响应数据固定为 8 字节：

```text
targetId       uint32 little-endian
role           uint8   1=App, 2=Boot
protocol       uint8   当前为1
configVersion  uint16 little-endian
```

AS5PR ECU/App `targetId = 0x41503541`。App 和 Boot 必须由固件仓库公共头 `Shared/ReleaseIdentity/Inc/ReleaseIdentity_Cfg.h` 取得 DID、targetId、protocol 和 configVersion，防止两边手工漂移。DID 在默认、扩展和编程会话均可读且不要求安全解锁；App 通过现有项目 Dcm DID 表注册只读回调，Boot 必须新增并注册只读 DID 表，二者返回同一字节契约。

E68 在 Boot/App 同构建实现该 DID 并生成可信发布资源包之前，只允许 fake/dry-run。

### 7.2 探测结果

- `APP_CONFIRMED`：正响应字段全部匹配且 role=App。
- `BOOT_CONFIRMED`：正响应字段全部匹配且 role=Boot。
- `WRONG_TARGET`：DID 正响应存在，但 targetId 或 configVersion 不匹配。
- `AMBIGUOUS`：响应长度、角色或关联关系无法唯一判定。
- `NO_RESPONSE`：在项目发布配置时间窗内无可关联响应。

禁止通过 GUI 复选框或命令行参数强制覆盖探测结果。

## 8. 擦除前身份门禁

真实擦除前必须满足：

```text
用户选择项目
  → 唯一选择程序内项目发布配置
  → .erel projectCode/configVersion/configDigest 必须与该配置一致
  → .erel buildId/buildCommit 必须与三个资源 BuildIdentityV1 一致
  → Boot 表项 targetId 必须等于 projectCode、authVersion 必须为0
  → App认证块 targetId/version 必须等于配置的 appTargetId/appAuthVersion
  → FlashDriver认证块 targetId/version 必须等于配置的 flashDriverTargetId/flashDriverAuthVersion
  → ECU F1A0 targetId/configVersion 必须等于配置的 ecuTargetId/configVersion
```

AS5PR 的 `projectCode`、`ecuTargetId` 和 `appTargetId` 均为 `0x41503541`，但 `flashDriverTargetId` 为 `0x46503541`；不同资源角色按各自配置比较，禁止用一个 targetId 做全链相等判断。

任一字段不一致，结果为 `REJECTED_IDENTITY`，不得发送擦除例程。

## 9. OTA 入口状态机

### 9.1 App 入口

```text
读取F1A0确认App
→ 默认会话
→ 扩展会话
→ Level1安全访问
→ 刷写条件检查
→ $10 02
→ 等待Boot代回50 02
→ 重新读取F1A0确认Boot
→ FBL安全访问
```

AS5PR Boot 从 SRAM 交接上下文恢复编程会话并代回 `50 02`。收到 `50 02` 不能替代 Boot 身份复核。

### 9.2 Boot 入口

```text
读取F1A0确认Boot
→ 首先请求FBL Seed ($27 09)
→ 若正响应则继续FBL安全访问
→ 仅当精确返回7F 27 7F时发送Boot $10 02
→ 收到50 02后重新读取F1A0确认Boot，再重试一次FBL Seed
```

Boot 入口禁止执行 App 默认会话、扩展会话、Level1 安全访问和刷写条件检查。当前共享 UDS 实现对错误会话的既有精确响应为 `7F 27 7F`；只有这一响应允许执行上述一次会话切换，任何其它 NRC、超时或异常响应都直接失败，不得掩盖锁定、时延或身份错误。

### 9.3 编程主链

```text
FlashDriver RequestDownload/TransferData/TransferExit
→ 启动FlashDriver
→ 擦除App
→ App RequestDownload/TransferData/TransferExit
→ 项目认证例程
→ ECU Reset
→ App身份与通信验证
```

AS5PR `$31 FF01` 同时完成认证、向量校验和 AppValid 提交，不增加虚构的独立激活服务。

## 10. 安全取消状态机

| 阶段 | 取消行为 | 结果 |
|---|---|---|
| 包校验、硬件预检、身份探测、App预编程 | 立即停止 | `CANCELLED_SAFE` |
| `$10 02` 已接受、Boot未确认 | 在交接超时内只等待 `50 02` 和 Boot `F1A0`，禁止开始下载 | 确认后 `ECU_IN_BOOT`，超时 `FAILED_UNKNOWN` |
| FlashDriver `$34` 已接受但传输未完成 | 等待当前 `$36` 最终响应，不再发新块且不得发送 `$37`；发送 `$10 01` 中止传输，收到 `50 01` 后读取 Boot `F1A0` | 确认后 `ECU_IN_BOOT`，否则 `FAILED_UNKNOWN` |
| FlashDriver 已完成 `$37`、尚未执行 | 发送 `$10 01` 并复核 Boot 身份，不启动驱动 | 确认后 `ECU_IN_BOOT`，否则 `FAILED_UNKNOWN` |
| FlashDriver已执行、App未擦除 | 不再启动例程，读取 Boot `F1A0` | 确认后 `ECU_IN_BOOT`，否则 `FAILED_UNKNOWN` |
| App擦除例程已接受 | 挂起取消，等待擦除最终响应；ResponsePending 期间不得插入 TesterPresent 或其它请求 | 擦除成功且 Boot 身份可确认时 `ECU_IN_BOOT`，超时/断链为 `FAILED_UNKNOWN` |
| App `$34` 已接受但传输未完成 | 等待当前 `$36` 最终响应，不发新块且不得发送 `$37`；发送 `$10 01` 中止传输，再用 `$10 02` 回到编程会话并复核 Boot 身份 | 确认后 `ECU_IN_BOOT`，否则 `FAILED_UNKNOWN` |
| App 已完成 `$37`、认证未开始 | 不发送认证例程，复核 Boot 身份 | 确认后 `ECU_IN_BOOT`，否则 `FAILED_UNKNOWN` |
| App认证已开始 | 不可取消；成功时继续 ECU Reset 和复位后身份/通信验证，认证负响应时复核 Boot 身份，超时或断链不再发送请求 | 验证通过 `COMPLETED`；认证成功但验证未完成 `COMPLETED_UNVERIFIED`；认证失败且 Boot 可确认 `ECU_IN_BOOT`；无法确认 `FAILED_UNKNOWN` |
| 复位后验证 | 停止继续探测 | `COMPLETED_UNVERIFIED` 或 `FAILED_UNKNOWN` |

擦除请求以收到 ECU 对 `$31 FF00` 的首个合法响应为“已接受”；收到 `7F 31 78` 后只按 P2* 等待同一请求的最终响应。擦除成功返回点是收到最终 `71 01 FF 00`，随后才允许身份复核。若适配器维持链路必须发送 TesterPresent，只能在没有待决 UDS 请求的阶段发送。

`ECU_IN_BOOT` 的下一次恢复不从块序号续传：先读取 Boot `F1A0`，执行 `$10 01` 清除残留传输状态，再以 `$10 02` 进入编程会话、完成 FBL 安全访问、重新下载并执行 FlashDriver、重新整区擦除 App，随后从 App 第一个块开始。任何退出路径必须停止新请求、清理接收队列、释放诊断独占锁、关闭日志并按硬件适配器约定收口。清理失败追加到结果，不覆盖主失败原因。

## 11. 公共命令、事件和结果

### 11.1 命令

- `InspectReleaseCommand`：commandId、schemaVersion、releaseFile、selectedProject、输出目录；成功后把已复验包放入当前进程受控会话存储并返回 releaseSetId。
- `BuildReleaseCommand`：仅开发命令行使用，包含受控构建输出目录、selectedProject、HMAC 密钥引用、Ed25519 私钥引用和输出路径。
- `FlashReleaseCommand`：commandId、schemaVersion、releaseSetId、selectedProject、executionMode、硬件选择、危险确认和输出目录。
- `MergeReleaseCommand`：commandId、schemaVersion、releaseSetId、selectedProject、AppValid 策略和输出目录。
- `DiagnoseReleaseCommand`：commandId、schemaVersion、目标 commandId 或 sessionId。

### 11.2 事件

`ReleaseEvent` 包含 commandId、单调递增序号、阶段码、进度、稳定消息码和脱敏参数。界面文案可本地化，阶段码和消息码不可变化。

### 11.3 结果

`ReleaseResult` 包含 commandId、操作类型、最终状态、错误码、ECU 最终状态、releaseSetId、ArtifactId、SignedArtifactId、输出 hash 和诊断包路径。

稳定结果至少包括：

- `COMPLETED`
- `COMPLETED_UNVERIFIED`
- `CANCELLED_SAFE`
- `ECU_IN_BOOT`
- `REJECTED_IDENTITY`
- `FAILED_RECOVERABLE`
- `FAILED_UNKNOWN`

CLI exit code：`0` 完成、`2` 输入/配置拒绝、`3` 身份拒绝、`4` 安全取消、`5` ECU 留在 Boot、`6` 通信/协议失败、`7` ECU 状态未知、`8` 内部错误。

执行模式只有三种且互斥：

- `OFFLINE_DRY_RUN`：不扫描、不连接硬件且不发送任何帧；只验证包、项目配置、资源身份、地址和服务计划。
- `READ_ONLY_PROBE`：连接硬件后只允许发送 `$22 F1A0`；禁止 `$10/$27/$31/$34/$36/$37/$11`。
- `REAL_FLASH`：满足身份门禁和危险确认后执行完整状态机。

CLI 的文件路径只属于导入步骤：`flash <release.erel>` 在同一进程内先创建并执行 `InspectReleaseCommand`，取得 releaseSetId 后再创建 `FlashReleaseCommand`；GUI 选择文件时走同一调用链。Flash 应用服务永远不重新接收任意文件路径。

## 12. 命令行和 BAT

```text
ecu-release inspect --project AS5PR <release.erel>
ecu-release build-package --project AS5PR ...
ecu-release flash --project AS5PR <release.erel>
ecu-release merge --project AS5PR <release.erel>
ecu-release diagnose --session <id>
```

- `flash` 默认 `OFFLINE_DRY_RUN`；`--probe-only` 选择 `READ_ONLY_PROBE`；`--no-dry-run` 选择 `REAL_FLASH`，三者不得组合。
- 实机刷写必须同时提供 `--no-dry-run` 和 `--confirm-project AS5PR`，随后交互输入 `YES`。
- 执行前回显 projectCode、releaseSetId、App hash、构建提交、总线、寻址和目标身份。
- E68 缺少可信资源包或身份 DID 时返回退出码 `3`，不得访问擦除服务。

BAT 只定位程序并传递 `.erel` 路径；无参数时打开图形界面文件选择窗口。BAT 不包含签名、合并、SeedKey 或刷写逻辑。

## 13. 图形界面

现有 PySide6 主窗口新增“固件发布”工作区：

1. 先选择 E68 或 AS5PR，再拖入或选择 `.erel`；导入时必须匹配所选项目。
2. 显示项目、构建提交、projectCode、releaseSetId、资源 hash 和认证结论。
3. 扫描 TSMaster/TC1016，显示 CAN/LIN 通道和连接状态。
4. 自动显示 App/Boot 身份探测结果；该字段只读。
5. 提供“检查”“dry-run”“实机 OTA”“合并完整镜像”“取消”。
6. 实机 OTA 前勾选项目和线束确认并输入 `YES`。
7. 阶段日志与脱敏总线日志分区显示。

GUI 主线程不得执行总线请求、文件 hash 或刷写。后台 worker 只调用 `ReleaseApplicationService`；取消只设置线程安全请求，不强杀线程或进程。

## 14. 错误和诊断包

错误必须包含阶段、分类、稳定错误码、底层错误码、可恢复性和建议动作。禁止只返回“刷写失败”。

诊断 ZIP 采用允许清单，只包含：

- 工具版本、Windows 版本、TSMaster DLL 版本和硬件枚举摘要。
- projectCode、configVersion、releaseSetId 和脱敏资源摘要。
- ReleaseEvent、ReleaseResult 和脱敏总线日志。

默认遮蔽 UDS `$27` seed/key、`$36` payload、用户名、本地绝对路径和设备序列号个人标识。开发密钥、私钥、敏感内存内容不得进入诊断包。

## 15. 验证策略

### 15.1 发布资源包

- 固定二进制测试向量、整包签名、资源 hash、资源重排、截断、重复 kind、偏移重叠和路径原子替换。
- projectCode、configVersion、configDigest、targetId、认证版本任一不匹配均拒绝。
- 构建后重新解析 `.erel`，逐字节比对三个资源。

### 15.2 身份和 OTA

- App、Boot、错误 targetId、错误 configVersion、错误 role、无响应和陈旧响应。
- App入口与Boot入口分别验证允许的服务序列。
- NRC、ResponsePending、P2/P2*、错误块序号、TransferExit失败和认证失败。
- 表 10 中每个取消点均有自动化测试。
- E68 实机请求在身份能力缺失时必须在擦除前拒绝。

### 15.3 图形界面和命令行

- GUI worker 不阻塞主线程，关闭窗口时只请求安全取消。
- 危险按钮在身份未确认或后台任务运行时不可用。
- 命令行 dry-run、双重危险确认、稳定退出码和 JSON 结果一致性。
- GUI、命令行和 BAT 对同一 `.erel` 得到相同 releaseSetId 和结果分类。

### 15.4 构建与实机

修改 AS5PR 身份 DID 后必须验证：

- App：`make PROJECT=AS5PR`、平台 host tests、静态项目边界检查。
- Bootloader：clean build 和 Bootloader tests。
- FlashDriver ABI 未修改时只做现有构建回归；若公共地址或认证契约变化则重新构建。
- 工具全量 pytest、TSMaster 虚拟 CAN OTA。
- 最终执行一次 AS5PR 实机 OTA，以复位后 `F1A0`、`22 3000` 和连接后新鲜周期报文收口。

## 16. 实施分解

### 阶段 A：发布资源包和项目发布配置

完成 `.erel` 编解码、整包签名、内置项目配置、ArtifactIdentityV2、会话存储迁移、开发构建命令及 AS5PR 构建脚本接入；把现有检查、签名、合并和 fake 流程全部迁移到新应用服务，并保持现有自动化测试通过。

### 阶段 B：ECU 身份和 OTA 状态机

完成 AS5PR App/Boot `F1A0`、自动入口探测、App/Boot 双入口、取消结果状态机、CAN 适配器接入和 fake/虚拟 CAN 回归。身份 DID 或任何固件源码变化后，必须重新执行 Boot/App/FlashDriver 同次构建并重新生成 `.erel`；阶段 A 生成的旧包不得沿用。

### 阶段 C：图形界面、命令行和实机门禁

完成统一命令行、固件发布工作区、worker、BAT、诊断包、AS5PR 实机 OTA 和复位后通信验证。

每个阶段独立编写实施计划、测试、评审和提交；这里的独立是指软件门禁可单独验证，不表示固件变化后可以继续复用旧发布包。阶段 A、B 未通过时不得开始实机擦除。
