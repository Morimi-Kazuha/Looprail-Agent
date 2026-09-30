# Looprail JEV-R1 Report

Validation date: 2026-09-30, Asia/Shanghai. These are observed local results.
No task-success uplift, optimal threshold, cost reduction or model-accuracy claim
is inferred from a fake corpus run or a single live recovery demonstration.

## 1. Status

**PASS WITH NOTES.** Config isolation, recovery evaluation scaffold, STOP safety
and persistence, public documentation and focused Runtime regression pass.
The existing Windows CRLF RPC snapshot check still fails; broader historical
platform/TUI failures remain outside this polish scope. Jev stays frozen at the
single Adaptive Recovery Advisor integration point.

## 2. Git

Branch: `main`. HEAD before: `59d90bd410229327e8fdd8348a4434b23b99dc2b`
(`feat: add Jev adaptive tool recovery`). Initial working tree was clean.
GitHub main was checked live and still points to parent
`57e160c2dae9dc45287822cd06694e682b83f963`; the existing local feature commit
is consolidated as `feat: add policy-guarded Jev recovery`. Final HEAD and commit receipt are reported after committing
in the completion message; this document cannot contain its own commit hash.
No push, tag or release is part of this task.

## 3. Config Isolation

Production and demo now read only these recovery configuration variables:

| Variable | Contract |
| --- | --- |
| `LOOPRAIL_JEV_ENABLED` | Default false; environment kill switch |
| `LOOPRAIL_JEV_TRANSPORT` | openrouter or typesafe_direct |
| `LOOPRAIL_JEV_MODEL` | OpenRouter default typesafe/jev-1.13; direct explicit |
| `LOOPRAIL_JEV_ENDPOINT` | Direct HTTPS endpoint; no credentials/query/fragment |
| `LOOPRAIL_JEV_TIMEOUT_SECONDS` | Default 2.0, finite, positive, at most 30 |
| `LOOPRAIL_JEV_CONFIDENCE_THRESHOLD` | Default 0.65, finite in [0,1] |
| `LOOPRAIL_OPENROUTER_API_KEY` | OpenRouter only; environment secret |
| `LOOPRAIL_JEV_API_KEY` | Direct TypeSafe only; environment secret |

Environment settings override file defaults. The runtime has no foreign-project
aliases or credential fallback. Negative tests deliberately provide old names
and confirm `missing_key` and unchanged local defaults. Those test inputs are
isolation evidence, not supported configuration. The example contains only
Looprail names, empty key placeholders and default-off settings.

## 4. Secret Handling

A credential from an existing local ignored file was read without modification
and reused under `LOOPRAIL_OPENROUTER_API_KEY` **only in the validation process**.
No persistent shell/user credential setting was created. No new secret file,
.env copy, Git entry, printed value or raw live response was created. A later
shell must supply its own Looprail variable explicitly. Sanitized local evidence
contains only the bounded decision and execution receipt. The credential scan
checks known values and credential-shaped candidates in tracked/candidate files,
Git diff and built archives without printing secret values.

## 5. Recovery Architecture

```text
Same-tool consecutive hard failures
→ eligible trigger, unused-this-turn and remaining Runtime budget
→ DecisionAdvisorPort → JevDecisionAdvisor → structured Choice
→ local schema/enum/distribution/confidence validation
→ Runtime fixed hint (RECHECK_INPUTS / USE_ALTERNATIVE)
  or tools-disabled summary (STOP, interrupted)
```

Jev is the semantic advisor; Policy is the validation boundary; Runtime owns
execution, permissions and budgets. No Router, Planner, Critic, Evolver or other
Jev lane was added. At most one advisor request per turn. Disabled, missing key,
timeout, network error, invalid response/schema and low confidence preserve the
original deterministic recovery. Cancellation continues to propagate.

## 6. STOP Semantics

Jev recommends STOP; strict policy validates it and applies the confidence floor.
Runtime breaks the tool loop and calls final synthesis with `tools=None`.
A summary never changes the internal outcome from `interrupted` to completed.
The normal recovery marker and session path persists that state and the summary.
The current tool batch has already completed before the trigger; no prior effect
is undone. STOP prevents subsequent tools in this turn, not all future turns.

Tests cover accepted HTTP STOP, unchanged tool count, internal interrupted
outcome, a summary that tries to return a write_file call, a future write that
would occur if the tool loop continued, no side-effect file, summary persistence,
a disk-reloaded recovery marker and session, and static streaming-summary fallback.
The existing Scheduler receipt exposes accounting, not an interrupted-status
field; the demo displays the actual durable turn marker instead of inventing one.

## 7. Evaluation Scaffold

Location: `benchmarks/recovery/` in the existing benchmark tree. **15 cases**.
Schema: `id`, `goal`, `failure_type`, `tool_name`, `failure_count`,
`available_tools`, `remaining_budget`, `acceptable_actions`, `notes`.
IDs, bounds, production failure categories, unique tools and nonempty unique
acceptable action sets are validated. Expected labels and notes are never sent
to the advisor; the context projection matches production.

```powershell
python -m scripts.eval_jev_recovery
python -m scripts.eval_jev_recovery --advisor fake --threshold 0.85 --json
# Explicit paid opt-in; up to 15 decisions, never a default CI operation.
python -m scripts.eval_jev_recovery --advisor live --json
```

Actual fake run at 0.65: **14 / 15 acceptable decisions, 0 fallbacks,
0 invalid responses, mean latency 0.031 ms**. Distribution: RECHECK_INPUTS 5,
USE_ALTERNATIVE 3, STOP 7. This is a rule-based test double with simulated
confidence 0.8, not a Jev measurement. The authorized-local-alternative case
exposes its coarse permission→STOP rule. At 0.85: **0 / 15 acceptable decisions,
15 low-confidence fallbacks, 0 invalid responses**. BASELINE is not scored as
acceptable semantic advice. JSON rows retain both suggested and selected action.

## 8. Confidence Threshold

**0.65 is a conservative configurable default, not a benchmarked optimum.**
[TypeSafe confidence](https://docs.typesafe.ai/confidence) summarizes probability
distribution concentration, not task correctness or permission to execute.
The scaffold supports failure-case regression, distribution analysis, threshold
comparisons and fallback observation. Future calibration needs representative
live decisions, independently reviewed acceptable sets and held-out cases.
Task-success claims additionally need real execution and a baseline comparison.

## 9. README / Docs

README now presents Policy-Guarded Semantic Recovery with Trigger, Advisor,
Guard and Authority, a bounded-runtime diagram, explicit fallback conditions,
STOP interruption and independent credential names. Integration documentation
adds cross-project isolation, honest confidence semantics and evaluation commands.
The env example uses only placeholders. The demo remains fake by default and
now verifies persisted sessions with a fresh manager and displays durable status.
`benchmarks/recovery/README.md` explains scoring, limitations and live-call cost.

## 10. Brand Scan

Production, README, new configuration and integration docs contain no foreign
project brand/config dependency. Old credential-name literals remain only in
`tests/test_jev_decision.py` as deliberate rejection/isolation fixtures.
The case-insensitive literal scan found 7 old credential-prefix occurrences,
all in those rejection fixtures, zero other-project brand occurrences, and 63
retired-brand/substrings across 17 files (including the explanatory POSIX editor
note in this report). Retired-brand hits elsewhere are retained for actual external Memory integration
module/protocol compatibility, negative migration/packaging tests, third-party
npm package names, the POSIX pico editor and incidental substrings in exception
names. No public README brand residue remains. No compatibility field was renamed.

## 11. Tests

**30 additional collected deterministic tests**, beyond the prior 53:
7 config/endpoint isolation cases, 1 hostile STOP/persistence test, and 22 eval
cases/checks. Existing integration assertions were strengthened without adding
redundant tests. Focused selection: **83 passed**. Additional public-tree and
product-identity checks bring the expanded selection to **105 passed**.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jev_decision.py tests/integration/test_jev_recovery_runtime.py tests/test_jev_recovery_eval.py -q
.\scripts\run_runtime_baseline.ps1 -Python .\.venv\Scripts\python.exe
```

Windows Runtime baseline: **671 passed in 129.46s**. The frozen manifest is
unchanged; additional Jev/eval tests are counted separately rather than hidden.

## 12. Quality Checks

| Check | Current result |
| --- | --- |
| Ruff lint: looprail, tests, scripts | PASS |
| Ruff format: changed Python and recovery policy | PASS |
| Focused mypy, follow-imports=skip, check-untyped-defs | PASS, 4 modules |
| TUI TypeScript typecheck | PASS |
| TUI ESLint | 0 errors, 17 existing warnings |
| RPC surface | PASS, 32 calls / 24 registered methods |
| RPC generated-file check | FAIL, existing Windows CRLF snapshot mismatch |
| Wheel / sdist, build --no-isolation | PASS; wheel built from sdist |
| Public-tree / known-key and key-shape / archive checks | PASS |

The RPC file matches original HEAD after newline normalization. Original HEAD's
normalized sha256 prefix `69b95c6b042b` equals freshly generated content. No TUI
source or generated snapshot changed. Extracted wheel recovery modules import successfully and the builder remains default-off;
eval scripts/cases are documented as source-checkout tooling, not an installed CLI.

## 13. Existing Failures

The previous broad-suite evidence is retained as historical evidence, not claimed
as a rerun: Python **3,981 passed, 82 failed, 15 errors, 55 skipped, 1 blocked**,
reconstructed from separate receipts after the Windows pipe test hung. It was
not a successful uninterrupted full-suite run. Original-source comparison
reproduced platform/CLI/Evolver failures; 11 pricing/usage cases passed alone but
failed in the larger test order. The deliberately broken calculator fixture
also participates in broad collection.

Previous TUI evidence: **801 passed, 29 failed**, including Windows socket
listen EACCES. Windows pipe/AF_UNIX, symlink/chmod restrictions, pricing-order
behavior and CRLF snapshot issues remain outside this recovery-polish scope.
No full-suite or TUI-test PASS claim is made. Current required focused/baseline
checks were rerun; unrelated broad suites were not repeated to force a clean result.

## 14. Real Jev Validation

One live request with the new process-only Looprail key contract:

| Observation | Value |
| --- | --- |
| Transport | openrouter |
| Model | typesafe/jev-1.13 |
| Trigger | Two nonexistent-file reads |
| Decision | RECHECK_INPUTS |
| Confidence | 0.99 |
| Fallback | false |
| Advisor latency | 1456.576 ms |
| Tools | read_file → read_file → list_dir → read_file |
| Runtime result | Sentinel input read; completed marker and session persisted |

Jev was real; the main text provider was explicitly scripted. The receipt proves
new configuration/transport/policy/execution wiring for one observation. It does
not demonstrate comparative reliability, model accuracy, performance or savings.
Only a sanitized receipt is kept locally under ignored `.looprail/evidence/jev-r1/`.

## 15. Demo Commands

```powershell
python -m scripts.jev_recovery_demo --case normal --advisor fake
python -m scripts.jev_recovery_demo --case recover --advisor fake
python -m scripts.jev_recovery_demo --case recover --advisor fake --action STOP
```

Observed: normal reads once, no advisor call, completed; recover fails twice,
RECHECK_INPUTS, directory check, corrected read, completed; STOP fails twice,
no later tools, a summary and interrupted marker. Defaults remain deterministic
and offline. No new STOP case or expanded demo framework was needed.

## 16. Interview Story

### 15 seconds

Looprail 用确定性逻辑检测连续工具失败，Jev 只建议恢复策略，本地 Policy 校验，
Runtime 保留执行权。不可用时回退，STOP 后总结但保持 interrupted。

### 30 seconds

我在既有工具失败边界接入一个语义顾问，每轮最多调用一次。Jev 返回检查输入、
换方法或停止的结构化 Choice，本地校验 schema、概率和置信度，Runtime 管预算、
权限和工具。配置独立、缺 key 或超时回退，STOP 的副作用阻止和持久化都有测试。
15 个多答案 eval case 为未来校准提供基础，但我不把 fake 结果当模型准确率。

### 2 minutes

先审计真实 Runtime：CLI、TUI 和 Gateway 共用装配，Spine 串行化会话，AgentLoop
负责模型、工具和持久化。原逻辑已经能检测同一工具连续硬失败，因此只在这个点
加入 DecisionAdvisorPort，未扩展 Router、Planner、Critic 或 Evolver。Jev 接收
受限目标、失败类别、可用工具和预算，返回闭集 Choice，不生成命令。Adapter 和
Policy 双重校验类型、枚举、有限概率、分布、最高概率选项和置信度；Runtime 控制
每轮最多一次以及剩余迭代。RECHECK 和 ALTERNATIVE 只提供本地固定提示，后续仍走
Registry 和 effect journal；STOP 则禁用后续工具、生成总结并持久化 interrupted。
即使总结模型夹带写文件调用，也不会执行。缺 key、超时、网络错误、非法响应和
低置信度都回退原逻辑，取消继续传播。本轮去掉跨项目配置耦合，secret 只在验证
进程内复用，没有复制文件或提交。83 项 focused 测试和 671 项 Runtime 基线通过，
真实 Jev 验证了新配置端到端。15 个多答案 case 记录策略分布和 fallback，fake
默认不联网。0.65 是可配置保守默认值；未来需要代表性真实样本、独立标注、留出集
和实际任务对照，才能谈校准、恢复率或性能提升。

## 17. Final Assessment

Suitable for a public repository, honest résumé description and Agent internship
interview demonstration within the documented scope. The authority boundary,
configuration isolation, failure safety, test receipts and reproducible demos
support that assessment. Existing optional-platform/TUI failures remain visible.

Limits: tiny hand-authored corpus; no live corpus benchmark or held-out threshold
calibration; scripted main model in live demo; real model can ignore hints or
stop too early; OpenRouter alpha endpoint; one extra paid call and bounded latency
on eligible failure turns; best-effort goal redaction; incomplete broad-platform
acceptance. Freeze Jev at this single integration until evidence justifies change.
