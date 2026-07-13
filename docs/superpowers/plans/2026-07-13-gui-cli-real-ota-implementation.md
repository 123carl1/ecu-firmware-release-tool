# GUI、CLI、BAT 与实机 OTA 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GUI、命令行和 BAT 共用同一应用服务，并以一次 AS5PR 实机 OTA、复位身份和新鲜通信证据完成交付。

**Architecture:** `release/application_service.py` 是唯一业务入口，命令层只把参数转换为不可变 Command；GUI worker 只调用应用服务并转发 Event。硬件访问只存在于 TSMaster 适配器，离线模式完全不枚举硬件。

**Tech Stack:** Python 3.11、PySide6、argparse、pytest-qt/fake Qt、TSMaster/TC1016、PowerShell BAT。

## Global Constraints

- OFFLINE_DRY_RUN 不扫描硬件、不发送帧。
- READ_ONLY_PROBE 只允许 `$22 F1A0`。
- REAL_FLASH 必须同时通过包身份、ECU 身份和双重危险确认。
- GUI 主线程不做文件 hash、总线请求或刷写。
- E68 真实刷写保持关闭，直到具备同构建资源与在线身份能力。

---

### Task 1: 公共 Command/Event/Result 和应用服务

**Files:**
- Create: `src/unified_can_lin_host_tool/release/commands.py`
- Create: `src/unified_can_lin_host_tool/release/results.py`
- Create: `src/unified_can_lin_host_tool/release/application_service.py`
- Create: `tests/release/test_application_service.py`

**Interfaces:**
- Produces: `InspectReleaseCommand`, `FlashReleaseCommand`, `MergeReleaseCommand`, `DiagnoseReleaseCommand`, `ReleaseEvent`, `ReleaseResult`, `ReleaseApplicationService.execute(command)`。

- [ ] **Step 1: 写导入路径到 releaseSetId、Flash 只消费 ID 的测试**
- [ ] **Step 2: 写 selectedProject、executionMode 和错误码必填测试**
- [ ] **Step 3: 实现不可变命令变体和单调事件序号**
- [ ] **Step 4: 实现应用服务分派、诊断独占锁和稳定退出结果**
- [ ] **Step 5: 运行服务测试及阶段 A/B 回归**

### Task 2: 统一命令行

**Files:**
- Create: `src/unified_can_lin_host_tool/cli/release.py`
- Modify: `pyproject.toml`
- Create: `tests/test_release_cli.py`

**Interfaces:**
- Produces: `ecu-release inspect/build-package/flash/merge/diagnose`。

- [ ] **Step 1: 写三种执行模式互斥、项目必填和退出码测试**
- [ ] **Step 2: 写 `flash <file>` 内部先 Inspect 再 Flash 的调用测试**
- [ ] **Step 3: 实现 argparse 子命令和 JSON 结果输出**
- [ ] **Step 4: 实现 REAL_FLASH 的 `--confirm-project AS5PR` 加交互 `YES`**
- [ ] **Step 5: 运行 CLI 全量测试并确认离线模式无硬件调用**

### Task 3: 固件发布 GUI 工作区和 worker

**Files:**
- Create: `src/unified_can_lin_host_tool/ui/release_workspace.py`
- Create: `src/unified_can_lin_host_tool/ui/release_worker.py`
- Modify: `src/unified_can_lin_host_tool/ui/main_window.py`
- Create: `tests/test_release_workspace.py`
- Create: `tests/test_release_worker.py`

**Interfaces:**
- Consumes: `ReleaseApplicationService`。
- Produces: 项目选择、`.erel` 导入、资源摘要、身份状态、三种执行按钮、取消和阶段日志。

- [ ] **Step 1: 写项目先选、错误包拒绝、危险按钮门禁测试**
- [ ] **Step 2: 写 worker 不阻塞主线程、关闭窗口只请求取消测试**
- [ ] **Step 3: 实现独立工作区控件，不在控件内写业务常量**
- [ ] **Step 4: 实现 QThread worker，只转发 Command/Event/Result**
- [ ] **Step 5: 运行无头 UI 测试和现有 UI 回归**

### Task 4: BAT 和诊断包

**Files:**
- Create: `scripts/ecu-release.bat`
- Create: `src/unified_can_lin_host_tool/release/diagnostics.py`
- Create: `tests/release/test_diagnostics.py`

- [ ] **Step 1: 写允许清单、路径/用户名/27/36 数据遮蔽测试**
- [ ] **Step 2: 实现诊断 ZIP 临时写入、回读和原子替换**
- [ ] **Step 3: 实现 BAT 仅定位程序和转发 `.erel`；无参数启动 GUI**
- [ ] **Step 4: 运行诊断测试并人工审计 ZIP 条目**

### Task 5: 虚拟硬件端到端门禁

**Files:**
- Create: `tests/test_release_end_to_end.py`
- Modify: `tests/test_ui_fake_backend.py`
- Modify: `tests/test_tsmaster_virtual.py`

- [ ] **Step 1: 同一 `.erel` 通过 GUI 服务和 CLI 得到同一 releaseSetId**
- [ ] **Step 2: 验证 OFFLINE_DRY_RUN 零硬件调用、READ_ONLY_PROBE 只有 F1A0**
- [ ] **Step 3: 验证 AS5PR REAL_FLASH 完整序列和 E68 擦除前拒绝**
- [ ] **Step 4: 运行 `python -m pytest -q`，要求全部通过**

### Task 6: AS5PR 实机 OTA

**Files:**
- Runtime evidence only under ignored `artifacts/ota_logs/`; do not commit logs or binaries.

- [ ] **Step 1: 记录硬件枚举、通道、包 releaseSetId 和 ECU F1A0 App 身份**
- [ ] **Step 2: 先执行 OFFLINE_DRY_RUN，再执行 READ_ONLY_PROBE**
- [ ] **Step 3: 确认线束、电源和可恢复条件后执行 REAL_FLASH**
- [ ] **Step 4: 复位后读取 F1A0 role=App 和 `$22 3000`**
- [ ] **Step 5: 在配置的新鲜时间窗内捕获预期 CAN ID 的新周期帧并校验请求响应关联**
- [ ] **Step 6: 若任一证据缺失，结果标记未完成，不提交“可用”结论**

### Task 7: 最终提交门禁

**Files:**
- All files from Tasks 1-5; runtime evidence remains ignored.

- [ ] **Step 1: `git diff --check` 和禁止词扫描**
- [ ] **Step 2: 全量 pytest、CLI smoke、无头 GUI smoke、虚拟 CAN OTA**
- [ ] **Step 3: 由子代理对 C/H 相关固件最终 diff 运行 MISRA/cppcheck**
- [ ] **Step 4: 白名单提交工具与固件改动，不加入 artifacts/tests/tools/build 生成物**
- [ ] **Step 5: 报告每个 commit、验证命令、实机证据路径和未覆盖风险**
