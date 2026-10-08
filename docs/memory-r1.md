# Looprail R1：自主记忆闭环与小模型筛选

本轮面向 Agent 开发实习面试。在原有 MemoryStore、Session 和 Memory Segment 上补齐自动写入、四类用途分类及小模型候选筛选，不增加独立 Memory Framework，也不实施 R2 上下文压缩。

## 1. 修订前成果与保护

工作区为 `D:\Agent Learning\Pico Agent`；分支 `main`，HEAD 为 `11d160dcd4eef775f3a8a0de278d180b37c4d558`。最初任务开始时工作区干净；收到修订时，5 个文件修改、3 个文件新增，尚未 Commit 或 Push。

修订前的 8 个文件已按原始字节备份到：

`D:\Agent Learning\reports\Looprail-R1\pre-revision-20261008T073056Z.zip`

快照包含 `workspace/` 文件、`changes.patch`、SHA-256 清单和已完成的测试证据。创建后验证了全部文件哈希和 ZIP 完整性。仅建分支不能保存未提交内容，因此本轮使用文件快照；未执行 reset、clean、restore 或 Push。

修订前已实现：真实 Turn 保存后触发 Capture、低成本触发、严格 JSON 候选、用户原句引用与摘要、Runtime 验证、既有 Store 写入与版本替代、跨 Session 召回、配置默认关闭、Backend 共存、单个在途 Capture 与关闭排空。已运行新增测试 27 项、相关回归 257 项及 Runtime baseline 671 项；最近增加的 Backend 共存测试在修订后一起验证。

| 最新能力 | 差异审计 | 本轮处理 |
| --- | --- | --- |
| Turn 后自动提炼、写入、去重 | ALREADY IMPLEMENTED | 保留 Capture 和 AgentLoop 接线 |
| 跨 Session Context 复用 | ALREADY IMPLEMENTED | 增加四类内容和筛选的端到端验证 |
| user | NEEDS EXTENSION | 在偏好之外支持明确的稳定用户信息 |
| feedback / project / reference | MISSING | 增加可选用途分类与来源证明 |
| 小模型辅助检索 | MISSING | 增加既有候选 ID 筛选与回退 |
| 任务队列、数据库、独立 Worker | 无需实现 | 本轮没有引入 |
| 大型 Benchmark | 非必需 | 保留已有固定小样本测试，不扩建 |

没有发现必须删除的明显过度工程化模块。已有超时、输入输出上限、关闭排空及审计记录有测试支撑，直接保留。

## 2. 原始缺口

`AgentLoop._process_message()` 原来在 `_save_turn()` 清理消息、`SessionManager.save()` 保存 Session 后，调用 Context 后处理与外部 `MemoryBackend.store()`。这些操作没有自动调用本地结构化 `MemoryStore.write()`。

`Personalizer.post_learn()` 已能提取用户偏好，但通过 Markdown 分节写入用户画像，缺少结构化条目的来源、作用域和资格闭环。启用 Capture 时，`configure_personalization()` 关闭这条自动偏好写入路径，保留其他配置下的旧行为。

`ContextAssembler.owns_compaction=True` 使主循环跳过旧 Token Consolidation；Curator 的 `after_turn()` 记录主任务轨迹，不负责本轮长期记忆写入。无外部 Backend 时，原 Memory Segment 已支持显式写入的本地 Structured Memory，因此无需另建检索系统。

## 3. 四类用途与原 Schema 的关系

`Memory.category` 是可选的用途轴，不替代 Kind、Scope 或 Verification。仍使用 `looprail.memory.item.v1`；旧记录不含 category 时仍能读取，旧 R1 偏好也能被新 Capture 去重。新增 Kind 只有 `user_fact` 和 `reference`，用于避免把稳定用户信息和资料线索错误称为偏好或环境事实。

| Category | Kind | 默认 Scope | 含义与证据 |
| --- | --- | --- | --- |
| user | user_preference / user_fact | user | 明确长期偏好或明确要求记住的用户信息 |
| feedback | user_preference | user | 用户对今后 Agent 行为的明确纠正；可显式限制为 repo_local |
| project | project_fact | project | 用户声明的项目背景、长期决策；不是已验证仓库观察 |
| reference | reference | repo_local | 用户指定的未来资料线索；明确属于个人时使用 user |

所有自动候选均保持 `user_provided`。project 正文带有 `User-declared project context` 归属，reference 带有 `User-designated reference` 归属；Runtime 不访问 URL，也不宣称 URL 内容已核验。

原 Store 不允许任意 project_fact 以 user_provided 写入。本轮仅增加一个明确的证据分支：分类和 Kind 必须匹配、source_type 必须为 `explicit_user_declaration`，并带 Runtime 生成的声明标识、Session/Turn、来源引用、用户身份、匹配的原句摘要和证据摘要。未分类的旧 project_fact/user_provided 仍然拒绝；uncertain、derived、原始工具输出和 Recovery 的门禁保留。

## 4. 最终执行流程

```mermaid
flowchart LR
    U[真实用户 Turn] --> T[原 ReAct / Tool Loop]
    T --> S[清理消息并保存 Session]
    S --> B[原 Context 后处理和 Backend store]
    B --> C[Cheap Trigger]
    C --> E[LLM 生成有界候选]
    E --> V[Harness 校验原句、分类、作用域]
    V --> W[MemoryStore.write]
    W --> R[新 Session 的 scoped recall_memory]
    R --> M[可选小模型选择现有 ID]
    M --> H[Harness 验证 ID 子集或回退]
    H --> I[原 Memory Segment / ContextAssembler]
    I --> P[主 Provider 上下文]
```

`capture.py` 复用原协调器、单个在途任务、关闭屏障和 Session 审计，只扩展来源证明与候选分类。LLM 负责选择、分类并复制候选原句；Harness 核对它与已经清理、保存的真实用户消息一致。正文、作用域、冲突键、验证状态和出处由 Runtime 构造，不采用模型提供的 verified 或自选标识。

`select.py` 增加约百行的 `MemorySelector`。Memory Segment 先调用现有 `recall_memory()` 过滤 Repo、Project、User、状态和验证等级，再向现有 Provider 发送一个有界候选池。模型只能返回 Memory ID；Harness 检查数量、唯一性和候选池成员关系，随后使用原 Memory 对象渲染，不使用模型生成的正文。

模型不可用、超时、JSON 非法、ID 不存在、重复 ID 或输入超预算时，使用原确定性结果。合法的空 ID 列表表示当前没有相关记忆，Structured Memory 不注入。外部 Backend 的 opaque recall/store 协议及其错误语义保持原样，小模型筛选目前只处理本地结构化候选。

## 5. 配置与可复现输入

以下是配置片段；将模型占位符替换为现有 Provider 可用的轻量模型名，本轮不增加供应商或写入真实用户配置。

```json
{
  "memory": {
    "backend": null,
    "user_id": "alice",
    "auto_capture": {
      "enabled": true,
      "model": "YOUR_EXISTING_PROVIDER_LIGHT_MODEL",
      "max_input_tokens": 4096
    },
    "rerank": {
      "enabled": true,
      "model": "YOUR_EXISTING_PROVIDER_LIGHT_MODEL",
      "candidate_top_k": 8
    }
  }
}
```

两个本地功能均默认关闭；关闭时没有额外模型调用或自动写入。`backend=null` 仅关闭外部 Backend，不覆盖用户明确启用的本地功能。onboard `--skip-memory` 会关闭 Backend、auto_capture 和 rerank；不会删除已存在的记忆，既有显式本地记忆仍可走原确定性召回。

默认 Capture 上限为 3 个候选、512 个输出 Token、3 秒模型超时；Selector 为 10 条候选、256 个输出 Token、2 秒模型超时。输入使用保守 UTF-8 字节上界并预留消息封装空间；可获得的 Provider usage 进入审计。None 模型配置使用当前 Provider 默认模型，所以希望使用小模型时应明确配置模型名。

可以分别发送以下用户消息，随后在新 Session 提出含 `Python` 的任务：

```text
Remember about me: I build Python developer tools.
Feedback for future tasks: Always use uv for Python tasks.
For this project, remember: This Python application manages local jobs.
Project reference: Python docs https://docs.python.org/3/tutorial/index.html
```

也支持 `Please remember that I ...`、`请记住，我...`、`记住我的信息：...`、`纠正你的长期行为：...`、`项目长期决策：...` 等明确声明。普通聊天、假设、无法定位的模型总结、敏感来源与合成恢复内容不会自动写入。

## 6. 验证与审核

最新相关回归：**297 passed、1 skipped**。其中包含 `test_memory_auto_capture.py` 与 `test_memory_revision.py` 的全部 **58 项** R1 测试。跳过的是 Windows 上的旧 `fcntl POSIX-only` 测试；跨平台 Store 锁竞争的自动捕获测试已通过。

最后修正筛选诊断，保证 selected、selected_count 和 selected_ids 都反映最终注入，而非筛选前候选池；两个 R1 测试文件重跑为 **58 passed（36.29 秒）**，诊断断言覆盖非空选择与空选择。

覆盖四类中英文声明和作用域、真实 run_turn 自动写入、跨 Session 注入、模型调用实接、非法/重复 ID、空选择、超时回退、源引用不符、伪造 verified、旧记录读取/去重、偏好被 feedback 替代、跨 Repo/User 的候选池过滤、Backend 共存、关闭排空及配置关闭。

关键端到端测试 `test_session_a_capture_session_b_small_model_changes_selection` 在 Session A 自动写入四类记忆，创建新的 Agent 与 Session B，再调用配置为 fake-small 的 Provider 从四条真实候选中选择 user 记忆。它验证选择不同于原确定性首选、只有被选正文进入主 Provider、旧 Session 原对话不被传入。这里真实执行的是调用链，Provider 为 deterministic fake；没有声称已验证真实小模型的质量或付费推理成本。

已完成全仓 `ruff check .`、13 个改动 Python 文件的 `ruff format --check` 和 `git diff --check`，均通过。测试在沙箱外运行，因为本机沙箱内 Windows asyncio 会卡在本地 socket pair；使用隔离临时状态与 fake Provider，没有真实 API 调用。

最初 HEAD 和最初版 R1 的完整 Runtime baseline 均为 671 passed。修订后的首次完整 baseline 为 670 passed、1 failed；完整串行重跑仍为 **670 passed、1 failed（210.64 秒）**。失败是原 `TestTwoTrackConcurrency.test_skill_and_memory_run_concurrently` 的 156 ms 超过 150 ms 墙钟门槛。同一测试单独运行修订版为 156 ms，未修改 HEAD 对照为 187 ms，说明该失败也能在未修改代码上复现；未放宽门槛或修改压缩策略。功能回归通过，完整基线保留这一未解决的耗时断言，交付状态为 PASS WITH NOTES。

命令：

```powershell
$env:PYTEST_ADDOPTS = '-p no:cacheprovider'
.\scripts\run_runtime_baseline.ps1
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_memory_revision.py tests/test_memory_auto_capture.py
.\.venv\Scripts\python.exe -m ruff check .
git diff --check
```

详细日志在 `D:\Agent Learning\tmp\r1-revision-regression-final.log`、`r1-revision-runtime-final.log`、`r1-revision-runtime-isolated.log` 和 `r1-final-diagnostics.log`。相关回归包含 Memory、AgentLoop、Backend、Curator、Context factory、Segments、Session、配置和 onboarding 测试。

## 7. 两个简历亮点

1. 在 Local-first Coding Agent 的真实 Turn 生命周期接入自主记忆闭环：LLM 提取四类用途候选，Harness 校验已持久化用户来源与作用域，复用 MemoryStore 完成去重、版本替代和跨 Session 复用；通过源伪造与验证等级回归保护写入边界。
2. 实现小模型辅助的结构化记忆检索：在现有 scoped recall 后仅选择候选 Memory ID，验证子集并注入原正文，模型不可用时回退到确定性召回；端到端测试证明筛选实际改变新 Session 的 Provider 上下文。

## 8. 面试官的五个连续追问

| 问题 | 可以展示的源码和实验 |
| --- | --- |
| 四类与 Kind/Scope 为什么分开？旧数据怎么兼容？ | backend.py 的 MemoryCategory 与可选 Memory.category；Store 的 decoder；test_legacy_records_without_category_still_recall_and_dedupe |
| 怎么证明模型没有把幻觉写成 verified？ | capture.py 的 _proof、原句/source_ref 比对和 Runtime 构造；Store 的 category_contract 与 user_provided 声明分支；伪造 verified 测试 |
| 用户纠正旧偏好后会不会保留两个相反事实？ | _proof 的有限偏好槽位与共享 auto-preference 键；Store 原 conflict/superseded 机制；test_feedback_updates_existing_preference_without_parallel_active_claims |
| 小模型怎么参与，能否编造记忆？失败怎么办？ | select.py 的 Provider.chat、by_id 和子集校验；MemorySegmentBuilder.build；新 Session 改变首选、非法 ID、空选择与超时测试 |
| 生命周期和跨仓库安全怎么保证？最重要的局限是什么？ | AgentLoop Session 保存后的接线与 close；先 scoped recall 再发候选给模型；Repo/User 隔离和关闭排空测试；下面列出的局限 |

## 9. 明确的局限

- Capture 是保守的 extractive 路径，只支持明确的长期偏好/纠正/记忆声明，不宣称能够理解任意对话隐含事实。
- user_provided 证明用户说过，不证明项目外部事实正确；reference 保存线索，不验证网页存在或内容。
- Python/JavaScript 工具、回复风格和语言等有限槽位支持确定性版本替代；一般项目声明只做规范化精确去重，没有语义冲突推断。
- 小模型只能筛选原词法召回池，无法找回未进入候选池的同义记忆；本轮未测真实模型质量。
- 记忆文件与 Session 审计不是一个原子事务。优雅关闭会 drain；强制杀进程仍有 Store 写入与审计之间的窗口，不能宣传崩溃条件下的 exactly-once。
- processed-ID 摘要随 Session 长度增长；本轮保留该简单机制，没有新增队列、数据库或生产级索引。
- 多用户部署需要正确配置 user_id；不实现新的身份认证。外部 Backend 的旧异常语义保持原样。

本轮没有改 Curator/ContextAssembler 压缩策略、SkillForge 或 Evolver，也没有实现 R2 的五阶段压缩。验收后用户已明确授权 Commit 和 Push；以下 Git 状态保留为提交前的审计快照。

## 10. Git 状态与文件清单

提交前审计：分支 `main`，HEAD `11d160dcd4eef775f3a8a0de278d180b37c4d558`；9 个已跟踪文件修改、6 个新增文件，当时均未提交。该审计时点本地保存的 upstream `github/main` 与 HEAD 的 ahead/behind 均为 0，尚未远程刷新。最终提交与推送状态以 Git 记录为准。

| 状态 | 文件 | 用途 |
| --- | --- | --- |
| 修改 | looprail/agent/loop/main.py | Turn 后 Capture、启停与关闭屏障 |
| 修改 | looprail/cli/onboard_commands.py | skip-memory 关闭自动捕获与筛选 |
| 修改 | looprail/config/looprail.py | Capture / Rerank 配置 |
| 修改 | looprail/config/update.py | 配置更新兼容 |
| 修改 | looprail/context_engine/factory.py | 注入已有 Provider 和配置 |
| 修改 | looprail/context_engine/segments/memory.py | scoped recall 后筛选和注入 |
| 修改 | looprail/memory_engine/__init__.py | 导出分类枚举 |
| 修改 | looprail/memory_engine/backend.py | 可选 category 与新增 Kind |
| 修改 | looprail/memory_engine/consolidate/consolidator.py | 来源门禁、兼容读写、非阻塞锁与筛选诊断 |
| 新增 | looprail/memory_engine/capture.py | 保留原 R1 并扩展四类捕获 |
| 新增 | looprail/memory_engine/select.py | 小模型 ID 选择与确定性回退 |
| 新增 | tests/fixtures/memory_capture_r1.json | 保留最初固定小样本 |
| 新增 | tests/test_memory_auto_capture.py | 保留并验证原 R1 功能 |
| 新增 | tests/test_memory_revision.py | 四类、门禁、小模型与跨 Session 验证 |
| 新增 | docs/memory-r1.md | 差异审计、验证、简历与面试源码依据 |
