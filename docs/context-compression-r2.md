# Looprail R2：五阶段渐进式上下文压缩

## 1. Status

**PASS WITH NOTES**。默认 Runtime 已接入五阶段策略，复用唯一 ContextAssembler、Curator、HistoryTrimmer、RuntimeContextTrimmer 与 CuratorArchiveStore。真实 AgentLoop、模拟编码工具与确定性 Provider 验证了压缩后继续任务。没有声称复刻 Claude Code 私有实现，也没有使用真实付费模型评价摘要质量。

完整 Runtime 对照中保留了原有并发耗时断言的波动证据，详情见测试表；其在未修改 R1 和 R2 都可失败，也有 671 项全通过的运行。没有放宽 150 ms 门槛。验收交付时 R2 未 Commit、未 Push；验收后用户已明确授权 Commit 并 Push 到 GitHub，最终状态以 Git 记录为准。

## 2. Git Baseline

- 分支：`main`。
- 开工 HEAD：`7a8abab5636202f6e528e415b7170289bcd2af55`，`feat: add autonomous memory capture and small-model selection`。
- `git status --porcelain=v1 -uall` 开工输出为空。R1 已提交，不存在需要辨认或代为提交的 R1/用户未提交文件。
- 保护快照：`D:\Agent Learning\tmp\looprail-r2-baseline-7a8abab.zip`，由 `git archive HEAD` 生成。
- 不修改源工作区地解包到 `D:\Agent Learning\tmp\looprail-r2-baseline-7a8abab`，用相同 `.venv` 解释器运行原测试清单。
- 开工保护阶段未执行 reset、clean、restore、强制 Push、Git add 或 Commit。快照不是新 Commit；不需要重复提交已完成的 R1。

R2 文件：

| 文件 | 改动 |
| --- | --- |
| `looprail/context_engine/compression.py` | Curator 的单次压缩策略；五阶段、来源校验、预算校验、投影复用 |
| `looprail/context_engine/segments/curator.py` | 默认路径及当前 Turn 内的同一策略接线；保留旧慢路径 |
| `looprail/context_engine/assembler.py` | 将 live pressure 委托回 Curator，提供原 Archive Reader |
| `looprail/context_engine/curator.py` | 原 Working State 可保存压缩投影；原 Archive 增加分页、路径及 Session 检查 |
| `looprail/context_engine/history_trimmer.py` | 增强 Tool adjacency 校验；Outcome 增加阶段记录 |
| `looprail/agent/loop/main.py` | live 压缩接线、Archive Reader 注册、策略后及收尾调用的最终门禁 |
| `looprail/config/looprail.py` | 五阶段开关及有界参数 |
| `tests/test_context_compression_r2.py` | A–H、真实工具执行、顺序、恢复、投影复用、错误与安全边界 |
| `tests/test_curator_context_engine.py` | 旧 Slow/Fallback 测试显式选择兼容模式，断言保持 |
| `tests/test_context_budget_contract.py` | 原强制 Curator 慢路径案例显式选择兼容模式，断言保持 |
| `scripts/demo_context_compression_r2.py` | 可重跑的真实 Runtime 演示，使用验收测试中的确定性场景 |
| `docs/context-compression-r2-demo.json` | 实际运行产生的原历史、决策、Provider 上下文及继续执行结果 |
| `docs/context-compression-r2.md` | 本报告 |

R1 的 Capture、Selection、Memory Store/Schema、Memory Segment 与 Factory 未修改。

## 3. Source Audit

开工审计后的最小设计：

| Stage | 已有实现 | 原缺口 | R2 最小改动 |
| --- | --- | --- | --- |
| 1 Tool Result Offloading | RuntimeTrimmer 精简输出并返回原文，Session 有独立持久化上限 | 无恢复引用，跨 Turn 前可能截断 | 复用精简算法及 Archive 保存原结果，Provider 副本引用原记录 |
| 2 History Snip | HistoryTrimmer 的完整 Turn 分组、相关性与保护规则 | 裁剪可能一次删到预算达标，缺少后续阶段空间 | 只裁低相关或重复的完整普通交互；原用户文本保留为历史证据 |
| 3 Microcompaction | RuntimeTrimmer 的重复观察与错误摘录 | 历史组装没有同样的策略，职责未与 Stage 1 区分 | 按年龄/重复性处理小于单结果上限的旧观察，跳过已压缩标记 |
| 4 Context Collapse | Manifest、Working State、Archive | 没有确定性的旧交互折叠 | 折叠已闭合交互，保留用户原文、修改调用/结果、错误、结论与引用 |
| 5 Auto Compaction | Curator Provider 调用、Working State、预算门禁 | 模型主要选择 message IDs，没有结构化任务摘要 | 来源可校验的八字段候选，Harness 保留强制证据并决定采用 |

源码入口：`compression.py:CuratorCompression.run/compact_tools/snip/collapse/auto_compact`，`segments/curator.py:build/fit_runtime`。

八个审计问题的实际答案：

1. **在哪里组装？** `AgentLoop._process_message` 在 Turn 开始调用 `_assemble_context_messages`；Assembler Phase A 并行建立 segments 1–5，Phase B 的 Curator 独占 history 与 segment 6。每次工具迭代原来只用 RuntimeTrimmer 处理增长，不重新调用整套 Memory/Skill 组装。R2 同样保持这些生命周期边界。
2. **原 Fast/Slow/Fallback？** Fast 根据 history 与 `available_history * fast_path_threshold` 比较，仍通过 CuratorAssembler 的固定开销和配对校验。Slow 在 timeout/max_steps 内调用内部模型，通过 Curator Tools 提交 ContextPlan。Fallback 确定性选择 protected/relevant/recent，再按预算裁剪。旧实现仍可用 `progressive_compression=false` 运行；R2 默认 Fast 根据实际完整预算判断，无压力时不会仅因比例阈值调用模型。
3. **原 RuntimeTrimmer 如何压工具结果？** `history_trimmer.py:RuntimeContextTrimmer.fit` 先按重复、年龄、失败与最近性排序，保留失败摘录，再删除非保护的历史组；仅修改 Provider 副本，返回 `original_tool_contents`。AgentLoop 在持久化前还原；原 Session 独立的 16,000 字符上限保持。
4. **原 HistoryTrimmer 如何删除？** `canonical_ids` 完成 Tool 配对闭包；`_turn_groups` 按 User 边界分组；`trim` 删除不含 protected IDs 且组内最高相关性最低的组，时间只作为平分时的次级顺序。它不是按单条消息 FIFO 删除。
5. **Archive 如何工作？** `curator.py:CuratorArchiveStore` 使用现有 JSON manifest、JSONL archive、Working State 与 trace 文件。归档更新 message ID、relevance 与 reference；原 Session log 保留。R2 给原 reader 增加分页与执行 Session 校验，不创建新数据库。
6. **是否有 Tool Result Lifecycle？** 有。`ToolRegistry` 做参数、超时、ToolResult.failed、effect 与 trace；AgentLoop 记录 failed/fingerprint；ContextBuilder 给结果加不可信数据边界。R2 复用这些机制，并在精简后重新给证据加匹配边界。
7. **哪些原能力在生产链？** Factory 返回统一 Assembler，Memory/Skill/Curator 都实接；HistoryTrimmer 用于 Curator 计划门禁；RuntimeTrimmer 用于每次工具迭代；Session 保存、错误恢复、Call Efficiency 与策略钩子实接。Curator 归档/恢复原先主要由内部慢路径工具按需调用，不能把“类存在”等同于每轮执行。
8. **哪些能力缺少？** 组件大多已存在，缺少固定渐进顺序、单结果恢复引用、历史观察复用、闭合交互折叠及八字段自动摘要。R2 增加的是 Curator 的压缩策略，不是第二个 Context Engine。

## 4. Before / After

Before：

```text
Turn → Assembler Phase A → Curator manifest
     → 比例阈值 Fast / 内部工具循环 Slow / deterministic Fallback
     → CuratorAssembler + HistoryTrimmer → Assembler 校验 → Main Provider
工具增长 → RuntimeTrimmer 精简工具 → 删除旧历史 → Main Provider
结束 → 原结果恢复 → Session 保存 → R1 Capture
```

After（默认）：

```text
Turn → 原 Assembler Phase A → 原 Curator Phase B
     → 全局输入预算 + 单工具结果大小检查
     → 1 Offloading → 2 Snip → 3 Microcompaction → 4 Collapse → 5 Auto Compaction
       每一步按条件触发；满足预算就不进入后续阶段
     → 原 segment 6 / history slots → Assembler 结构、预算校验
工具增长 → Assembler 将压力委托给同一 Curator 策略，活动交换不折叠
     → before_llm_call 策略 → 最终 Provider 配对及预算校验 → Main Provider
结束 → 原工具结果恢复 → 合成恢复标记清理 → 原 Session 保存 → 原 R1 Capture
```

`CuratorCompression` 保存一次调用的 provider projection，没有独立 history 所有权或存储后端。原组合无法表达“只裁低价值内容后继续微压缩/折叠，再决定是否调用模型”，因此策略集中在 Curator 内；Assembler 只接线和校验。Factory、Memory lane、Skill lane 不承担新职责。

## 5. Five-Stage Design

| Stage | 触发 | 行为 / 退出 | 回退 |
| --- | --- | --- | --- |
| 1 | 单个未压缩 Tool Result 超过默认 12,000 字符上限；全局预算也会检查 | 先归档原文，复用 RuntimeTrimmer 摘录，保留 name/call ID/result ID、失败证据和 Archive/message ID。该单结果上限也在全局有余量时执行，避免 Session 截断丢掉唯一原文 | 精简不减少估算 Token 时不采用；原文不改；后续预算门禁决定能否发送 |
| 2 | 完整输入仍超预算，存在 relevance ≤ 0.45 或重复的旧普通交互 | 复用 Turn 分组；避开保护头、最近交互与工具交换；归档后保留用户原文并删除对应完整组。按价值排序，采用结果必须减少 Token | 没有合适组就进入 3；失败工具和修改证据不在这里清理 |
| 3 | 仍超预算，存在旧的未压缩工具观察 | 复用 RuntimeTrimmer 的 duplicate/excerpt 语义，记录原文引用；历史路径保护最近两 Turn，live 路径保护最近三条 Tool Result。调用关系保持 | 跳过已有 marker；不能缩小时不采用，不调用模型 |
| 4 | 仍超预算，存在非最近且以无 tool_calls 的 Assistant 结束的旧完整交互 | 归档完整组，将原用户要求、write/edit 路径与结果、失败摘录、明确 Task State 行、最终结论放入 Working State。结束只表示协议闭合，不代表任务已成功 | 不折叠当前 live Turn、未闭合调用、多模态记录；折叠不省 Token 时保留原结构 |
| 5 | 前四步后仍超预算，有可重组的闭合交互 | 同一 Curator Provider/model 生成八字段 JSON；每项为 `{message_id, quote}`。Harness 要求 quote 是实际发送源池及原始消息中的完整非空原文行；强制用户原文、修改/失败/明确 Task State 证据在模型之外保留。最终上下文更小且预算、配对通过才采用 | timeout、Provider error、length 截断、工具调用、非法字段、伪造来源、空摘要、不减小或仍超预算都拒绝。保留已采用的确定性投影；仍放不下则显式 ContextBudgetError，主模型不接收超预算上下文 |

结构化字段为 Current Goal、User Constraints、Important Decisions、Changed Files、Test Results、Current Blockers、Next Steps、Evidence References。未知字段用空列表，渲染为 unknown；真实 Archive References 由 Harness 加入，不靠模型猜测。

预算含 System、Memory、Skill、当前用户、多模态封装、history 和工具定义，输入上限是 `context_limit - reserved_output - runtime_margin`。最终检查在 `before_llm_call` 策略之后，防止策略扩大请求或改变目标模型后绕过门禁。迭代耗尽的 tools-disabled 收尾调用也检查预算；不合法时走原静态未完成回复。

配置默认 `progressive_compression=true`，`compression_recent_turns=2`，摘要输入最多 8,192 估算 Token、输出最多 2,048 Token，timeout 复用原 `curator_timeout_seconds`。`curator_model` 仍由原配置选择；没有写真实用户配置或增加供应商。`fast_path_threshold` 仅在旧兼容路径决定是否进入内部工具循环。

已采用的历史投影保存在原 Curator Working State；通过原始 provider-safe source 的 SHA-256 与 source_count 校验后复用。新消息追加在投影后；源前缀变化、格式非法时重新构建；复用后仍按当前 System/Tools/User 预算校验，不缓存旧预算结论。Stage 1/3 marker 避免重复精简同一 live result，已有 manifest 引用避免重复归档同一历史源。

## 6. Task State Preservation

- 当前用户及 segments 1–5 不交给摘要模型删除。全部历史用户原文留在 history 或 Harness-owned historical evidence 中，不凭关键词猜测哪些句子是约束。
- write/edit 保存调用 ID、path 与原工具结果；标签明确区分调用和结果，不把“尝试写文件”自动当作“修改已完成”。
- failed 复用 Runtime 分类；live metadata 优先于文本推断。失败保留有界摘录和原文引用，模型不能将失败证据替换为成功。
- Stage 4 保留完整最终结论；明确的 `Decision: / Next: / TODO: / Blocked:` 行作为 Harness 证据保留，Stage 5 同样不能删除这些行。
- 摘要候选只能选完整原文行，不能删掉行内否定词；字段语义和事实真实性并未被形式化证明，见 Remaining Risks。
- live Turn 不折叠或摘要掉当前工具交换。只改变观察内容，原 `original_tool_contents` 恢复逻辑和 Session 截断策略不改。
- live 投影保留 `_recovery_synthetic` 标记，Harness 提示/思考预填仍由原逻辑清理，不能伪装成真实用户消息或进入 R1 Capture。
- Working State 和归档恢复都标记为历史数据；摘录重建不可信边界。`read_context_archive` 从 ToolExecutionContext 取 Session，检查 archive 根目录、后缀与 header Session；跨 Session/越界拒绝。可按 message_ids、offset_chars 读取 600 字符页，读取原调用结构用 exact 模式。返回仍为 Tool Result。
- 不把全部压缩摘要写入长期 Memory；不以 MemoryStore 替代 Archive，不清理或重写 Memory Segment。

## 7. Test Results

以下为实际测试记录。日志均位于 `D:\Agent Learning\tmp`，使用同一仓库 `.venv\Scripts\python.exe`。

| 测试 | 结果 | 证据 |
| --- | --- | --- |
| 未修改 R1 完整 Runtime 清单 | 670 passed、1 failed，276.93s | `r2-baseline-final.log`；唯一失败为原并发耗时断言，156 ms ≥ 150 ms |
| R2 首次完整 Runtime 对照 | 670 passed、1 failed，244.28s | `r2-final-runtime.log`；同一原断言，187 ms ≥ 150 ms |
| 压缩 / R1 / Session / 配置 / 安全相关回归 | 364 passed、1 skipped、1 warning，70.89s | `r2-delivery-regression.log`；其后的原文完整行和计数细化另有下面专项回归 |
| 最终原文来源、Context 与 Trace 回归 | 84 passed，16.43s | `r2-final-metrics.log`，包含全部 26 项 R2 用例 |
| 恢复及收尾门禁回归 | 48 passed，10.29s | `r2-recovery-guard.log` |
| 最终完整 Runtime 清单 | **671 passed，230.73s** | `r2-verified-runtime.log`；源码冻结摘要为 `r2-verified-source-hashes.json`，运行后核对一致 |
| Ruff、format、diff check | PASS | `ruff check .`；11 个 Python 文件 format check；`git diff --check` |
| 实际工具执行演示 | PASS；7769 → 3023；摘要调用 1 次 | `context-compression-r2-demo.json`、`r2-final-demo.log` |

验收覆盖：

| 要求 | 测试证据（`tests/test_context_compression_r2.py`） |
| --- | --- |
| A Fast | `test_a_fast_path_never_calls_summary_even_above_old_threshold` |
| B Offloading | `test_b_single_result_offloaded_recoverable_and_pair_intact`；单结果独立上限、长错误、live 归档分页恢复 |
| C Snip | `test_c_snip_low_relevance_complete_turn_preserves_user_constraints` |
| D Microcompaction | `test_d_old_observations_microcompact_keep_recent_and_errors` |
| E Collapse | `test_e_closed_turn_collapse_preserves_decision_and_archive` |
| F Auto | `test_f_auto_summary_structured_grounded_and_within_budget` |
| G 连续编码质量 | `test_g_tools_actually_execute_then_compacted_agent_continues`；实际执行 fake read/write/exec 后跨 Turn 继续 |
| H 失败与兼容 | 五种摘要失败、保护内容超预算、来源否定词、Session 隔离、缓存校验、合成恢复标记、策略后和收尾预算门禁；旧 Fast/Slow/Fallback 断言仍执行 |
| 全阶段顺序 | `test_all_stages_follow_priority_order_and_each_adopted_stage_reduces_tokens` |

命令（PowerShell；parent-only `--basetemp`，不能放入被 verifier 子进程继承的 PYTEST_ADDOPTS）：

```powershell
$env:PYTEST_ADDOPTS = '-p no:cacheprovider'
.\.venv\Scripts\python.exe -m pytest -q --basetemp='D:\Agent Learning\tmp\r2-check-stage' tests/test_context_compression_r2.py

# 完整 baseline 从原脚本抽取原清单，测试及断言不改：
$baselineFiles = Get-Content './scripts/run_runtime_baseline.ps1' |
    Select-String '^    "(tests/[^"]+)"' |
    ForEach-Object { $_.Matches[0].Groups[1].Value }
.\.venv\Scripts\python.exe -m pytest -q --basetemp='D:\Agent Learning\tmp\r2-check-runtime' @baselineFiles
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

回归清单：R2、context_budget_contract、context_invariants、history_trimmer、curator_context_engine、context_engine_factory、agent_loop_memory_pipeline、memory_auto_capture、memory_revision、memory_backend_contract/protocol、memory_store_lt_additions、memory_lifecycle_contract、memory_locking、session_manager/export、security_untrusted_context、config_looprail_loader、config_cfg1、config_update。完整 baseline 使用原 44 个文件、671 项测试清单，未将新增测试偷偷计入原 baseline。

相关回归的唯一 SKIP 是原 `test_memory_locking.py` 的 `fcntl POSIX-only`；Windows 的原跨平台 R1 Capture/Store 锁测试通过。唯一 warning 是旧 `skill_forge.mass_library_db` 已弃用配置的预期提醒。原 150 ms 并发断言在 R1 为 156 ms、R2 首次完整对照为 187 ms；另一次 R2 对照为 156 ms（`r2-final-source-runtime.log`），最终这一轮通过。保留这些对照，不能用一次通过宣称消除了墙钟计时波动。

首次沙箱执行因 pytest 临时目录无法建立而产生 671 setup errors；改成 parent CLI 的工作区临时目录后，异步测试仍受 Windows 沙箱限制。因此最终 Fake Provider 测试获自动审批后在沙箱外运行，使用隔离状态，没有真实 API 调用。另一次错误地把 basetemp 放进继承环境，导致原 verifier 子进程共用目录；修正运行方式后的未改 R1 对照只剩原耗时断言。这些环境失败未算成 R2 功能通过，也未改 Session 或安全测试掩盖。

## 8. Demonstration

复跑：

```powershell
.\.venv\Scripts\python.exe -m scripts.demo_context_compression_r2 --state 'D:\Agent Learning\tmp\r2-interview-demo'
```

真实 Harness 执行的场景：

1. 用户要求实现 retry，同时禁止新增依赖及改变 public API。
2. 主模型通过实际 ToolRegistry 调用模拟 `read_file(app.py)`、`write_file(app.py)`、`exec`；exec 明确返回 failed=True 与 `ERROR: pytest failed: retry count is 3, expected 2. Exit code: 1`。
3. Agent 保存调试历史，明确 `Next: fix retry count in app.py and rerun pytest.`，没有宣布整个任务完成。
4. 下一 Turn 的受控输入预算为 5,000 Token。该案例的四个轻量阶段没有满足使用条件，实际只触发 Auto Compaction；这不是为了展示而强制跑满五步。
5. 压缩前估算 **7769**，压缩后 **3023**，摘要模型实际调用 **1** 次。最终 Provider gate 为 `ok=true`、`structural_errors=[]`。
6. 主 Agent 继续输出：`Continue fixing app.py; tests remain failed; do not add dependencies.`。Session 原历史不被替换成摘要，当前请求及回复正常持久化。

JSON 保存了 original_history、executed_tools、stages（before/after/reason）、provider_context、final_validation 与 continuation。归档和 trace 实际位于 demo 指定的隔离 state 目录；JSON 中 UUID/nonce 是当次运行的真实值，重新运行可以不同，state 路径改变也会改变 System 固定开销及估算数字。原 Assembler 的 before 会把新增 Working State 算入原历史；R2 的指标已经使用 Curator 压缩前的实际估算，并有一致性断言。另一个确定性测试在同一历史中展示五阶段严格顺序及每步 Token 减少。

## 9. Interview Highlights

可写简历的两个事实：

1. **在统一 Coding Agent Runtime 中实现预算驱动的五阶段上下文压缩**：复用原 Curator/Trimmer/Archive，从单结果卸载到结构化自动摘要逐层处理，预算达标提前结束；通过完整 Turn、Tool 配对与策略后门禁保证 Provider 上下文可用。证据：`compression.py:run`、`segments/curator.py:fit_runtime`、全阶段顺序与 live 结果恢复测试。
2. **建立模型摘要候选与 Harness 状态保护的边界**：八字段摘要只能引用已发送源池的完整原文行，用户原要求、失败/修改证据与明确 Task State 在模型之外保护；来源不符、超时、无收益或仍超预算拒绝，原 Archive 支持受控恢复，已验证投影可跨 Turn 复用。实际模拟编码工具执行后 **7769 → 3023**，继续 Agent 仍知道测试失败和下一步。证据：`validate_summary/receipts/auto_compact`、`run_coding_scenario`、demo JSON。

面试自审：

| 问题 | 回答与源码 / 案例 |
| --- | --- |
| 1. 为什么需要压缩？ | 编码工具和调试过程使窗口增长，挤占任务状态并触发 Provider overflow；预算同时含工具 schema。`budget.py`、demo 的 7769/5000。 |
| 2. 为什么不直接 LLM 总结？ | 归档、价值裁剪和观察精简更便宜且确定；只有这些不足时付出模型调用和摘要风险。`run`、Test A/全顺序测试。 |
| 3. 五阶段区别？ | 单结果大小、低价值完整交互、旧观察累积、闭合交互结构、模型重组状态各有一条职责。`compact_tools/snip/collapse/auto_compact`、Test B–F。 |
| 4. 哪些不需 LLM？ | 1–4 和最终门禁都不需要；有 Archive I/O 但不是模型请求。Test A–E 摘要调用计数为 0。 |
| 5. 何时下一阶段？ | 用完整 messages + tools 重新估算；只有仍超输入上限才下一步；Stage 1 还处理独立单结果上限。`over_budget/run`、全顺序测试。 |
| 6. 为什么 Tool 配对不能破坏？ | Provider 协议要求 assistant tool_calls 和匹配结果邻接、完整；删一个结果可能构成 orphan/dangling。`HistoryTrimmer.structural_errors`、配对测试。 |
| 7. 如何保留原用户约束？ | 当前用户不交给摘要删除，历史原用户文本逐字进入历史证据；不是让模型重新措辞。`receipts`、Test C/F/G。 |
| 8. Collapse 与 Auto 区别？ | Collapse 确定性保留结论及证据、消除旧交互展开；Auto 在这还太大时用模型选结构化原文行，随后 Harness 校验。Test E/F。 |
| 9. 为什么 Memory 与 State 分开？ | R1 是跨 Session 长期记忆；压缩 State 绑定当前 Session 的源前缀和 Archive。默认不自动 Capture 摘要，原 Memory Segment 不删。R1 回归及 source_digest。 |
| 10. 摘要错或超时？ | 拒绝候选，保留确定性投影；受保护内容仍放不下就显式失败，不把成功措辞当验证。五种 Test H；收尾超限走原静态未完成回复。 |
| 11. Archive 为什么存在、何时读？ | Session 有原持久化上限，摘要本来也有损；需要遗漏细节时按 reference/ID/offset 取回数据。原 Archive Reader + live 中段恢复测试。 |
| 12. 如何证明还能继续？ | 实际 fake 工具经过真实 ToolRegistry、失败状态进入 Session，下一 Turn 触发真实摘要调用，再把通过门禁的上下文交给 Main Provider，断言限制/文件/失败/Next。`run_coding_scenario` 与 demo。 |

## 10. Remaining Risks

- 抽取式摘要比任意改写保守，能校验“原文来源”，不能证明每个字段的语义归类、完整覆盖或外部事实正确。原 Assistant 也可能说错；没有声称信息完全无损或防止一切幻觉。
- Harness 对明确 `Decision/Next/TODO/Blocked` 行有确定性保护，对未标记且散落在中间助手文本中的所有隐含决策没有形式化识别。完整最终结论或 Archive 可恢复；不引入另一个语义状态数据库。
- 所有原用户文本、关键失败摘录、活动工具交换和固定 Memory/Skill 本身太大时会显式拒绝。当前 live Turn 的调用骨架不被 Auto 丢弃；不实现无限长单 Turn 的有损重写。
- 阈值包含字符大小启发式，最终 Token 值仍是 Provider/本地估算，保留 runtime margin；不等于真实服务端精确 Token 计数。摘要输入池有界，可能看不到所有旧细节。
- 原 Session 超过 16,000 字符的工具结果仍按原策略截断。Stage 1 超长结果先归档，Archive 使用原持久化 sanitizer；媒体等原机制已清理的内容不能宣称逐字恢复。
- Working State、Archive、Session 不是一个跨文件原子事务；源 digest 防止复用错历史，但没有新增数据库、锁调度或崩溃 exactly-once 保证。
- Archive 使用原文件存储，读取时仍加载 JSONL 文件；没有建设大规模流式索引、向量检索或 Benchmark。默认 reader 限 Session，并尊重原工具禁用机制。
- 未评价真实摘要模型的召回率/费用/跨语言语义覆盖。Fake Provider 验证的是调用链、结构与 Harness 边界。
- 原 150 ms 并发耗时断言在未修改 R1 和 R2 都曾失败；最终完整基线为 671 passed。并发 lane 没改，阈值没放宽，一次通过不表示计时波动已经修复。

以上限制与本轮“真实代码、可解释、能追问”的目标相符，继续加入新 Memory Schema、多 Agent 压缩或摘要数据库没有必要。

## 11. Git Status

验收交付时 HEAD before / after 均为 `7a8abab5636202f6e528e415b7170289bcd2af55`。R1 Commit 保留；当时 R2 在工作区等待验收，未暂存、未 Commit、未 Push。文件清单见 Git Baseline，交付时 `git status --short` 与 `git diff --check` 已核对。

验收后用户明确要求“R2 Commit 且 Push”，授权将以上 R2 文件提交至 `main` 并正常推送 `github` 远程。此段记录授权与提交范围，提交哈希和推送结果以 Git 记录为准。
