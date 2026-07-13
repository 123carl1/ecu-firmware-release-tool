# AS5PR 身份 DID 与 OTA 状态机实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AS5PR App/Boot 增加同源 F1A0 身份，并用自动探测、双入口和安全取消状态机替换人工“已在 Boot”选项。

**Architecture:** 固件公共头提供身份常量和构建身份布局；App/Boot 各自在既有 Dcm/UDS 表注册只读 DID。上位机 `as5pr/ota_state_machine.py` 只消费已验证发布资源、身份探测端口和 UDS 请求端口，所有状态转移返回稳定结果。

**Tech Stack:** FM33HT Cortex-M0 C、GNU Arm Embedded Make、Python 3.11、pytest、TSMaster virtual CAN。

## Global Constraints

- F1A0 数据固定8字节：targetId little-endian、role、protocol、configVersion little-endian。
- 默认/扩展/编程会话均可读 F1A0，无安全等级要求。
- 真实擦除前必须同时确认包、项目配置、资源角色和在线 ECU。
- 取消不强杀线程，不在 UDS 待决响应期间插入其它请求。
- C/H 提交前 MISRA/cppcheck 只能由子代理执行；普通开发阶段不运行。

---

### Task 1: 固件共享身份契约和三镜像身份节

**Files:**
- Create: `../DAU_FM33_HT_AS5PR/Shared/ReleaseIdentity/Inc/ReleaseIdentity_Cfg.h`
- Create: `../DAU_FM33_HT_AS5PR/Shared/ReleaseIdentity/Inc/ReleaseBuildIdentity_Generated.h`
- Create: `../DAU_FM33_HT_AS5PR/Shared/ReleaseIdentity/Src/ReleaseIdentity.c`
- Modify: App、Boot、FlashDriver 的 linker script 与 Makefile
- Modify: `../DAU_FM33_HT_AS5PR/tools/build_ota_release.ps1`

**Interfaces:**
- Produces: `ReleaseIdentity_GetDidData(role, out[8])`，ELF `.fw_identity` 恰好100字节。

- [ ] **Step 1: 写 host test，断言三种 role 的 RBID 布局和 F1A0 字节**
- [ ] **Step 2: 运行 host test 确认失败**
- [ ] **Step 3: 实现公共头、身份源文件、链接节和生成头**

```c
void ReleaseIdentity_GetDidData(uint8_t role, uint8_t data[8]);
extern const ReleaseBuildIdentityType g_ReleaseBuildIdentity;
```

- [ ] **Step 4: 分别构建 App/Boot/FlashDriver，用 objdump 验证节地址、长度和 BIN 内容**
- [ ] **Step 5: 提交前由子代理运行 C/H 静态检查，再白名单提交固件改动**

### Task 2: App F1A0 DID 注册

**Files:**
- Modify: `../DAU_FM33_HT_AS5PR/Application/BSW/Config/AS5PR/Dcm/DcmDsp_Did_Cfg.c`
- Modify: matching headers/tests under `../DAU_FM33_HT_AS5PR/tests/platform/`

**Interfaces:**
- Produces: `$22 F1A0 -> 62 F1 A0 <8 bytes>` in all sessions.

- [ ] **Step 1: 写默认/扩展/编程会话正向测试和错误长度回归**
- [ ] **Step 2: 运行 host test 确认 F1A0 未注册**
- [ ] **Step 3: 增加只读 DID 回调，只调用 `ReleaseIdentity_GetDidData(APP_ROLE, response_data)`**
- [ ] **Step 4: 运行 App build、platform tests 和静态边界检查**

### Task 3: Boot F1A0 DID 注册

**Files:**
- Modify: `../DAU_FM33_HT_AS5PR/Bootloader/App/Src/boot_diag.c`
- Modify: Boot UDS 配置/测试文件

**Interfaces:**
- Produces: 冷启动和交接后的 Boot 均可在全部会话读取 role=2 的 F1A0。

- [ ] **Step 1: 写 Boot 默认/编程会话 DID 测试**
- [ ] **Step 2: 运行 `Bootloader/tests/run_tests.ps1` 确认失败**
- [ ] **Step 3: 注册共享 UDS DID 表，不增加安全访问门禁**
- [ ] **Step 4: clean build Bootloader 并运行全部 Boot tests**

### Task 4: 上位机身份探测服务

**Files:**
- Create: `src/unified_can_lin_host_tool/release/ecu_identity.py`
- Create: `tests/release/test_ecu_identity.py`

**Interfaces:**
- Produces: `EcuRole`, `IdentityProbeResult`, `probe_identity(transport, config, freshness_clock)`。

- [ ] **Step 1: 写 App/Boot/错误项目/错误版本/陈旧帧/无响应测试**
- [ ] **Step 2: 运行测试确认模块缺失**
- [ ] **Step 3: 实现请求响应关联、长度检查和新鲜时间窗**
- [ ] **Step 4: 运行测试并确认错误身份从不返回 confirmed**

### Task 5: App/Boot 双入口状态机

**Files:**
- Create: `src/unified_can_lin_host_tool/as5pr/ota_state_machine.py`
- Create: `tests/as5pr/test_ota_entry_state_machine.py`

**Interfaces:**
- Consumes: `VerifiedReleasePackage`, `IdentityProbeResult`, UDS request port。
- Produces: `As5prOtaStateMachine.run(command, cancel_token) -> ReleaseResult`。

- [ ] **Step 1: 写 App 入口精确服务序列测试**
- [ ] **Step 2: 写 Boot 冷入口 `27 09 -> 7F 27 7F -> 10 02 -> F1A0 -> 27 09` 测试**
- [ ] **Step 3: 写其它 NRC 禁止切会话测试**
- [ ] **Step 4: 实现最小状态枚举、入口分派和身份门禁**
- [ ] **Step 5: 运行入口测试，确认无人工 Boot 布尔参数**

### Task 6: 编程主链和安全取消

**Files:**
- Modify: `src/unified_can_lin_host_tool/as5pr/ota_state_machine.py`
- Create: `tests/as5pr/test_ota_cancel_state_machine.py`
- Create: `tests/as5pr/test_ota_programming_state_machine.py`

**Interfaces:**
- Produces: `COMPLETED`、`COMPLETED_UNVERIFIED`、`CANCELLED_SAFE`、`ECU_IN_BOOT`、`FAILED_UNKNOWN` 的确定性映射。

- [ ] **Step 1: 为设计表每个取消点写请求序列断言**
- [ ] **Step 2: 写擦除 ResponsePending 期间不得发送 TesterPresent 测试**
- [ ] **Step 3: 写部分传输后禁止 `$37`、恢复时整区重刷测试**
- [ ] **Step 4: 实现阶段化取消挂起和清理 finally**
- [ ] **Step 5: 运行编程、取消、P2/P2*、块序号和认证失败回归**

### Task 7: 虚拟 CAN 和固件重打包门禁

**Files:**
- Modify: `src/unified_can_lin_host_tool/adapters/tsmaster_virtual.py`
- Create: `tests/test_as5pr_ota_virtual_can.py`
- Modify: `../DAU_FM33_HT_AS5PR/tools/build_ota_release.ps1`

- [ ] **Step 1: 扩展虚拟 ECU 支持 F1A0、双入口、ResponsePending 和故障注入**
- [ ] **Step 2: 执行完整虚拟 OTA、错误项目、断电和取消测试**
- [ ] **Step 3: 重建 App/Boot/FlashDriver，生成新的同构建 `.erel`**
- [ ] **Step 4: 运行上位机全量 pytest 和固件三工程验证**
- [ ] **Step 5: C/H 静态检查由子代理完成后，分别对白名单固件和工具文件提交**
