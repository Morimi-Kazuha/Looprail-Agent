# PICO Medium+ Reconstruction
## Luna MAX Execution Report — Phase 0

**Report date:** 2026-09-15  
**Repository:** `<repo-root>`<br>
**Branch:** `feat/durable-execution-phase0`  
**HEAD:** `0ae70289b282bdc808e668bd267c7370ffd0e5b8` (`feat(runtime): add durable tool effect journal and recovery semantics`)

## 1. Task

按 Phase 0 “Repository Reconnaissance & Reconstruction Baseline” 协议完成只读勘察、真实主链路追踪、基线测试和证据报告。目标是明确当前实现中哪些能力是真实主路径、哪些仅存在于测试/旧代码/实验代码，并审计 Evolver 与 CLI 的可交付边界。

本阶段不重写架构、不修改应用源码、不修复发现的失败；完成报告后停止，等待 Web Reviewer。

## 2. Status

**PARTIAL — WEB REVIEW REQUIRED**

仓库勘察、主路径映射、CLI/Evolver 审计和多组基线测试已经完成。核心 Agent Loop、Turn、Tool Runtime、Context/Memory/Session/Trace 的正向测试组通过；完整收集和更宽的分组测试仍受 Windows/可选依赖/UI 构建环境及现有行为失败影响。未执行真实模型 Evolver run，也没有把未跟踪的 recovery 草稿接入主运行时。

本报告之后停止 Phase 0；不宣称可进入下一阶段。

## 3. Scope

本次检查覆盖：

- 包、依赖、脚本入口、CLI、TUI RPC、Turn、Scheduler、Agent Loop、Provider。
- Repo Context、文件工具、Tool Registry/Executor、Context Engine、Session、Checkpoint/Resume、Memory、Tracing。
- PicoBench、Verifier、Evolver（含 Candidate Label、Manifest、Evidence、激活和运行产物）。
- 单元测试、运行时/工具/上下文/会话恢复/Trace/PicoBench/Evolver、TUI 与 UI 构建入口；同时区分 docs、implementation、main path、test-only、legacy 和 experimental。

明确不在范围内：改写应用源码、修复失败测试、改变依赖、执行需要外部模型/外部服务的真实 Evolver 运行、删除或覆盖用户已有改动。

## 4. Baseline Inspected

### 4.1 仓库和工程基线

| 项目 | 观测结果 |
|---|---|
| Python package | `pico/`，356 个文件 |
| Tests | `tests/`，272 个文件 |
| Benchmarks | `benchmarks/`，179 个文件 |
| UI | `ui-tui/`，337 个文件；本地没有 `node_modules`，也没有 `dist/entry.js` |
| Python constraint | `pyproject.toml` 要求 `>=3.12,<3.13`；基线环境为 Python 3.12.14 |
| Package/script | project `pico-harness` 0.1.7；`pico = "pico.cli.commands:run"` |
| Default pytest selection | `testpaths=["tests"]`；默认排除 `real_llm`, `llm_judge`, `real_vm`, `real_channel`, `external_runtime`, `e2e` |
| Current tracked diff | 无 tracked diff |
| Pre-existing untracked files | `pico/agent/recovery/`、`tests/test_phase0b1_recovery.py`；本次未修改 |

依赖包括 Typer、LiteLLM、Pydantic、HTTPX、Loguru、Rich、croniter、PyYAML、prompt-toolkit、tiktoken、questionary、MCP、orjson、numpy、Pillow 等；channel extras 另依赖 `lark_oapi`、`botpy`、`wecom_aibot_sdk`。系统 Python 3.13.3 不具备 pytest；使用已有的 `<parent-root>\.pico-baseline-venv`，未安装或更换依赖。

### 4.2 真实入口和主运行路径

入口事实：

- `pico/__main__.py` 导入 `pico.cli.commands:run`；pyproject console script 指向相同函数。
- `pico.cli.commands` 建立 Typer app，注册 `run`、`evolve`、`tracing`、sessions、provider、channels、cron 等命令；无子命令时进入 `launch_tui()`。
- `pico run` 的确定性 CLI 入口在 `pico/cli/agent_commands.py`；它加载配置/provider/session，调用 `assemble_runtime()`，再通过 REPL spine 提交 `TurnRequest`。
- TUI 入口在 `pico/cli/tui_commands.py`；它要求 packaged/source `ui-tui/dist/entry.js`，建立认证的 loopback RPC 后使用同一 runtime assembly。

主路径（裸 `pico`，TUI）：

```text
pico/__main__.py or console script
  -> pico.cli.commands.run()
  -> launch_tui()
  -> ui-tui/dist/entry.js + authenticated loopback RPC
  -> _build_tui_runtime()
  -> assemble_runtime()
  -> TuiRpcServer / Dispatcher / TUI spine
  -> TuiTurnRunner -> Scheduler -> AgentTurnRunner
  -> AgentLoop.run_turn()
  -> ContextAssembler -> Provider -> ToolRegistry/Executor (0..N iterations)
  -> Hub events -> TuiOutlet -> RPC message/token/tool completion
```

主路径（`pico run`，CLI REPL/one-shot）：

```text
pico run -m <message> [--session/--continue/--resume]
  -> agent_commands.run()
  -> config + provider + SessionManager + assemble_runtime()
  -> build_repl(): DeliveryHub + CliOutlet + Scheduler
  -> Scheduler.submit(TurnRequest(origin=USER, source=cli/direct/user/DM))
  -> AgentTurnRunner -> AgentLoop.run_turn()
  -> context/provider/tool loop -> session/checkpoint/trace
  -> CliOutlet + handle.result() + hub.wait_idle()
  -> close runtime
```

当前判断：两条入口都落到同一个 `AgentLoop`/`Scheduler`/`ToolRegistry` 核心；`pico run` 是可直接测试的 CLI 主链路，裸 `pico` 是产品默认 TUI 主链路。TUI 的前端构建产物缺失，因而本环境只能审计 Python RPC/runtime 代码，不能完成可执行 TUI 端到端验证。

### 4.3 分层和代码归类

| 区域 | 当前实现 | 归类 | 结论 |
|---|---|---|---|
| `pico/cli/commands.py`, `agent_commands.py`, `_repl_spine.py` | 命令分发、CLI session/turn、outlet | main path | 保留并加固边界测试 |
| `pico/cli/tui_commands.py`, `pico/tui_rpc/` | TUI 启动、认证 RPC、turn/event wire | main path（裸 `pico`） | 实现存在，运行验证受 UI artifact 阻塞 |
| `pico/spine/turn.py`, `scheduler.py`, `pico/agent/spine_runner.py` | Turn contract、lane、origin pool、AgentLoop bridge | main path | 核心运行时 |
| `pico/agent/loop/main.py` | bounded loop、provider/tool/recovery orchestration | main path | 核心运行时 |
| `pico/agent/tools/registry.py`, `execution.py`, `filesystem.py`, `file_search.py` | schema、timeout、effect journal、文件/exec 工具 | main path | 核心运行时 |
| `pico/context_engine/` | `ContextAssembler` 及 segment builders | main path | shipping context path |
| `pico/agent/context/builder.py` | 旧式低层 renderer、memory/skill 兼容接口 | legacy/support | 仍被 token estimate/consolidator 等使用；不是当前 turn assembler |
| `pico/agent/loop/checkpoint.py` | 每 turn shadow-Git 文件快照 | main path when policy enabled | 不是完整对话 crash recovery |
| `pico/agent/recovery/` | Phase 0B1 草稿 scanner/planner/observations/models | test-only / untracked experimental | 目前未被 AgentLoop 引用；不得当作已接入能力 |
| `pico/evolver/` + `benchmarks/appworld/evolve/` | Candidate pipeline、gates、activation、AppWorld adapter | implementation + gated main path | CLI wiring 存在；真实 run 产物尚不存在 |
| `benchmarks/picobench/` | PicoBench budget/artifact/campaign | implementation/test path | Windows 缺 `fcntl`，smoke 未能启动 |
| `tests/` | 单元/合同/集成/可选/外部测试 | tests | 测试不能单独证明未接入代码是主路径 |
| `docs/` | 架构、Evolver、examples、操作约定 | docs | 与实现对应关系需以代码和测试为准；未把历史 digest 当本地证据 |

### 4.4 Capability matrix

推荐动作严格使用协议允许的单一标签。

| Capability | 实现位置/真实状态 | Main path | Test evidence | Recommendation |
|---|---|---:|---|---|
| CLI | Typer commands、`pico run` REPL、bare TUI launcher | 是 | `pico --version`, `pico --help`, evolve help 通过；宽 CLI 组有失败 | HARDEN |
| Turn | immutable `TurnRequest`、USER/CRON/SUBAGENT、busy policy | 是 | core spine tests；正向组通过 | KEEP |
| Agent Loop | bounded iterations、provider/tool loop、empty/overflow/max-iteration recovery | 是 | 422 个 core runtime tests 通过 | HARDEN |
| Provider | LazyProvider/config/model routing、retry/error translation、LiteLLM boundary | 是 | positive provider/config group 通过；真实 provider 未测 | HARDEN |
| Repo Context | workspace/allowed-dir resolution、file search/read/write/edit | 是 | runtime/tool/security tests 通过 | HARDEN |
| Tool Runtime | registry schema/timeout/error/effect journal/parallel read policy | 是 | registry/effects/tool tests 通过 | HARDEN |
| Context Engine | `ContextAssembler`，parallel segments + Curator + trimmer | 是 | context positive group 通过 | HARDEN |
| Session | SessionManager、history、continue/resume selectors | 是 | session/turn tests通过；跨进程 crash proof 不足 | KEEP |
| Checkpoint | per-turn shadow Git filesystem snapshot，best effort | 条件式 | checkpoint tests部分受 Windows 权限影响 | HARDEN |
| Resume | CLI selectors、session reload、`_stash_recovery` in-memory block | 部分 | recovery draft test only；main integration 未证明 | NEEDS REVIEW |
| Memory | local Markdown store/consolidator + optional MemoryBackend | 是/双路径 | memory positive group 通过；durability/effectiveness 未证明 | HARDEN |
| Trace | contextvar spans、scheduler/provider/tool/memory/context events | 是 | trace positive group 通过 | KEEP |
| PicoBench | budget/artifact/semantic campaign implementation | 非主 turn path | smoke blocked by Windows `fcntl` | NEEDS REVIEW |
| Verifier | Evolver candidate gates, manifest/evidence, sealed evaluator | Evolver path | broad Evolver tests 297 pass；23 fail | HARDEN |
| Evolver | CLI run/check/status/finalize、AppWorld launch/selection/activation | 独立命令主路径 | 297 pass/23 fail；no real local run artifact | NEEDS REVIEW |

## 5. Current Behavior

### 5.1 CLI and turn behavior

`pico run -m ...` creates a runtime and a user turn. A one-shot waits for the turn handle and then drains the delivery hub before closing resources. Interactive mode reuses the same scheduler and turn runner. `--session`, `--continue` and `--resume` select session/recovery behavior; they do not create a second agent implementation.

`CliOutlet` renders text and failed tool events. Successful tool events and media are intentionally not rendered in the current CLI outlet. Tool results still travel through the runtime/session/trace path; the outlet is a presentation boundary, not the source of truth for tool execution.

The Scheduler owns per-conversation lanes, origin pools, lifecycle event ownership, cancellation and shutdown. `TurnRequest` is frozen and carries origin/source/conversation/text. `AgentTurnRunner` delegates to `AgentLoop.run_turn`.

### 5.2 Agent Loop/provider/tool behavior

`AgentLoop` owns the ToolRegistry, Context Engine, SessionManager, subagent manager, sandbox executor, MCP integration, memory consolidation and effect journal. `_run_agent_loop` bounds iterations, calls the provider, executes tool calls, translates provider/tool errors, handles context overflow with emergency shrinking, handles empty responses and synthesizes a max-iteration result. A checkpoint commit is attempted where policy/interactive configuration enables it.

The ToolRegistry validates input schema, returns a failed result for unknown tools, applies timeouts, normalizes errors, and records effect states `PREPARED -> RUNNING -> COMMITTED/FAILED/UNKNOWN`. Cancellation after an uncertain boundary is represented as unknown rather than silently replayed. Parallelism is restricted to concurrency-safe read tools. Trace instrumentation is best effort and must not break the application.

The current HEAD adds `pico/agent/effects.py` with `READ`, `LOCAL_WRITE`, `EXECUTE`, `EXTERNAL`, `UNKNOWN` classes and a JSONL journal. This journal is integrated with registry execution, but there is no cross-process executor/replayer in the current main path.

### 5.3 Context, repo, session and recovery behavior

`pico/context_engine/assembler.py` assembles `[system, *history, user]`. Phase A can build identity/bootstrap/memory/active-skills/skills in parallel; Phase B runs Curator; metadata records assembly state. The factory's shipping route is `ContextAssembler`; legacy/default/curator dispatch variants described by older code are not separate shipping engines.

Repo context is constrained by workspace/allowed directories. Filesystem tools support read/write/edit/list/grep/find, use `rg` when available with a Python fallback, skip noisy directories, and deny system-root escapes. Bootstrap loads state-root `agent_memory/profile/soul.md`, `agent.md`, and `TOOLS.md`; there is no explicit `AGENTS.md` discovery in this path.

Session state is owned by SessionManager and persisted history. Memory has two paths: local Markdown profile/history plus optional `MemoryBackend.recall/store/feedback`; the backend owns storage while the host decides when/context. Passing tests show call behavior, not durable cross-process recovery or memory quality.

Checkpoint is a per-turn shadow-Git filesystem snapshot. It does not contain full conversation state, does not prove complete crash recovery, and cannot attribute every shell side effect. `_stash_recovery` currently stores interrupted-turn recovery in memory when checkpoint/files are available and injects a one-time recovery block on a later turn. The untracked `pico/agent/recovery/` package is not imported by the main AgentLoop and therefore is not counted as shipped resume behavior.

### 5.4 Trace and external/evolution behavior

`pico/tracing/trace.py` supplies nested local spans through a context variable and emits `audit.span.v1` records on a best-effort basis. Scheduler, provider, tool, memory, context and skill boundaries have span names/semantic conventions. Trace failures do not fail the user turn.

PicoBench has budget, artifact and semantic campaign code, but importing it on Windows fails at `fcntl`. Evolver is a separate CLI path. `pico evolve` delegates to `pico.evolver.cli.main`, exposing `run`, `check`, `status`, and `finalize`. The AppWorld launch path supports diagnosis, candidate editing, manifest creation, import smoke/beacon checks, path guards, Git child commits, train evaluation, sealed evaluation, selection, and activation artifact creation. No real local run was performed and no `.pico` run artifact exists.

## 6. Implementation

### 6.1 Runtime implementation map

| Concern | Primary symbols/files | Observed contract |
|---|---|---|
| CLI assembly | `pico/cli/commands.py`, `agent_commands.py`, `_runtime_assembly.py` | command selects config/provider/session, assembly owns lifecycle |
| TUI | `pico/cli/tui_commands.py`, `pico/tui_rpc/spine.py` | authenticated loopback RPC, event translation, completion after hub idle |
| Scheduling | `pico/spine/turn.py`, `pico/spine/scheduler.py` | lane per conversation, origin pools, cancellation/shutdown |
| Agent execution | `pico/agent/loop/main.py`, `spine_runner.py` | one bounded run loop, checkpoint/effect/memory hooks |
| Provider | provider factory/LazyProvider paths under `pico/providers/` and assembly | model call, retry/error boundary, usage |
| Context | `pico/context_engine/assembler.py`, `factory.py`, `history_trimmer.py` | one shipping assembler plus segment builders |
| Tool runtime | `pico/agent/tools/registry.py`, `execution.py` | schema, timeout, normalized result, effect and trace lifecycle |
| Repo tools | `pico/agent/tools/filesystem.py`, `file_search.py` | path boundary plus read/write/search operations |
| State | `pico/session/`, `pico/agent/loop/checkpoint.py`, memory modules | session history, optional snapshot, dual memory |
| Trace | `pico/tracing/trace.py`, `semconv.py` | local nested audit spans |

### 6.2 Mandatory CLI audit

| CLI concern | Evidence and result |
|---|---|
| Task entry | `pico run -m` reaches `agent_commands.run`; bare `pico` dispatches TUI launcher |
| Turn creation | one-shot creates `TurnRequest(origin=USER, Source(cli,direct,user,DM), conversation=session_id, text=message)` |
| Tool calls/results | AgentLoop -> ToolRegistry; CLI outlet renders failed tool event only; execution is not inferred from display |
| Errors | provider/tool/turn failure is translated to failed result/event; positive error-path tests passed in core group |
| Session | SessionManager is assembled and used by both CLI and TUI paths |
| Resume | selectors exist; full crash/restart semantics not proven; in-memory recovery stash is limited |
| Trace | scheduler/provider/tool spans are wired; trace is best effort |
| Final result | handle result plus `hub.wait_idle()` before close in REPL; TUI sends completion/error only after hub close/idle |

CLI evidence: `python -m pico --version` exited 0 and printed `✦ Pico v0.1.7`; `python -m pico --help` exited 0 and listed `run`, `evolve`, `tracing`, `sessions`, and `provider`; both Evolver help entry points exited 0.

### 6.3 Mandatory Evolver audit

#### Implementation/interface/docs/tests/main path

- Public bridge: `pico/cli/evolve_commands.py` -> `pico.evolver.cli.main`.
- Public interface: `pico/evolver/cli.py` commands `run`, `check`, `status`, `finalize`; run supports cold start, rounds, unseal and resumability; status is sealed-safe; finalize is one-way unseal.
- Benchmark registry: `pico/evolver/launch/registry.py`, currently `BENCHES={"appworld": "benchmarks.appworld.evolve.entry:build"}` with lazy subject-root loading.
- Example/config: `benchmarks/evolver/small_real.yaml` is a one-round disposable subject setup; `docs/examples/evolve_appworld.yaml` targets an external AppWorld environment and external work directory.
- AppWorld path: `benchmarks/appworld/evolve/entry.py`, `run.py`, `eval.py`, `editor.py`, `adapter.py`; it implements diagnosis, bounded editor changes, allowlists/immutable guards, candidate child commits, ephemeral-worktree evaluation, baseline pairing, fixed train denominator, test-leak assertion and sealed evaluation.
- Production outcome: `pico/evolver/orchestrator/production.py` creates activation artifacts with canonical evidence and Git parent/child binding.
- Activation: `pico/evolver/activation/artifacts.py` models `pending_human -> ready -> activated -> rolled_back` and requires a human actor; ledger/beacon is best effort. There is no public CLI activation/rollback command; API/state tests are the available interface evidence.
- Tests: 297 Evolver tests passed and 23 failed in the attempted 17-file Evolver group. This is substantial unit/contract coverage, not proof of a live model/benchmark run.

#### Candidate Label completeness

`CandidateLabel` enumerates `skill`, `prompt`, `policy`, `runtime`, `model_profile`, and `route`. `LABEL_POLICIES` currently gives only `runtime` a supported AppWorld mutation path: mutable files `benchmarks/appworld/agent_cli.py` and `benchmarks/appworld/tool.py`, fixture `appworld_runtime_v1`, evaluator `appworld_focused_fisher_v1`, and `human_review` activation. Other labels have explicit unsupported/no-fixture/no-evaluator reasons; model profile/route are config-only and do not imply weight changes.

`CandidateManifest` carries candidate id/label, `PatchWhere`, target files, before/after SHA-256, patch digest, fixture, evaluator and activation policy. G5 checks schema, path guard, immutable paths, digest integrity, content change and policy. This is a strong interface contract, but support is intentionally incomplete for non-runtime labels.

#### Evidence completeness and reproducibility

`candidate_evidence.py` defines `AcceptedRuntimeEvidence` with task order, attempt counts, candidate/control evaluations and eligible tasks. It recomputes the three-shield gate, validates measurements and requires a positive full-train lift; callers cannot supply a trusted acceptance boolean. AppWorld run code writes/uses train/sealed metadata and prevents test leakage.

Expected artifacts include `run_meta.json`, journal/rounds JSONL, node files, findings/history, archive/sealed data, retention records, candidate manifest/evidence, before/after/rollback/activation JSON and Git parent/child references. None are present locally: `.pico` is absent, no subject repository is materialized, and no model/benchmark run was started. Therefore reproducibility, selection evidence, activation readiness and rollback from a real run are **not proven** by this Phase 0.

## 7. Runtime / State Contract

| Area | Owner / source of truth | Lifecycle | Terminal/retry/cancel | Side effects / recovery |
|---|---|---|---|---|
| Turn | Scheduler lane + immutable `TurnRequest`; AgentLoop result/events | submit -> queued/running -> result/error/cancel | origin pool and busy policy govern admission; provider/tool errors surface as failed turn; cancellation belongs to scheduler/runner | session/history and trace are downstream effects; hub idle is required before CLI close |
| Provider call | provider boundary/LazyProvider and provider response | call -> response or normalized error | retry policy is in provider/loop boundary; unrecoverable error ends iteration/turn | external model call is not replayed; usage is emitted to sinks |
| Tool call | ToolRegistry + EffectJournal | PREPARED -> RUNNING -> COMMITTED/FAILED/UNKNOWN | timeout/cancel/error become explicit result/status; unknown is terminal for replay safety | filesystem/exec/external effects happen in tool boundary; no cross-process replay |
| Context | ContextAssembler and SessionManager history | segment build -> curatorial assembly -> provider messages | overflow invokes trim/emergency shrink; unrecoverable assembly error fails turn | context metadata/traces are best effort; history is source for next turn |
| Session | SessionManager persisted history | open -> append -> close/reopen | session errors fail or prevent turn admission; `--continue/--resume` selects existing state | conversation state is not contained in shadow Git checkpoint |
| Checkpoint | per-turn CheckpointService shadow Git | snapshot -> best-effort commit -> cleanup/retain | checkpoint failure is recorded/deferred and should not break turn where configured | captures filesystem snapshot, not arbitrary shell side effects or complete conversation state |
| Resume | CLI selector + SessionManager plus limited `_stash_recovery` | select -> load history/recovery block -> next turn | no universal crash-resume terminal contract; uncertain effect must not be replayed silently | current recovery stash is process-memory; untracked recovery package is not integrated |
| Memory | local store and optional `MemoryBackend` | recall/store/feedback, start/stop | backend errors are boundary failures and should not corrupt user turn where best effort applies | backend owns persistence; host owns timing/context; quality and cross-process durability not proven |
| Trace | span contextvar and audit sink | enter/nest/exit/emit | trace failure is non-terminal and best effort | local audit event; no claim of durable external trace export |

## 8. Failure Cases handled, tested, or deferred

| Failure case | Current behavior/evidence | Disposition |
|---|---|---|
| Unknown tool or invalid schema | Registry returns normalized failed result; schema and registry tests cover it | Tested; HARDEN |
| Tool timeout/cancel | Timeout/error is surfaced; cancellation after uncertain effect is `UNKNOWN` | Tested in unit paths; cross-process recovery deferred |
| Provider error/retry | Loop/provider boundary normalizes provider failure and retries where policy allows | Tested with fakes; real provider deferred |
| Context overflow | History trimmer preserves tool-call/result closure and provider-safe keys; loop has emergency shrink | Tested; wider provider compatibility deferred |
| Empty model response/max iterations | Loop has recovery/synthesis path | Tested with mocks |
| Workspace escape/system root | filesystem/search path guard denies invalid roots and skips noisy dirs | Tested |
| Checkpoint Git/symlink/permission behavior | implementation is best effort; Windows symlink privilege tests fail | Deferred to platform hardening |
| Crash/resume after uncertain effect | effect journal records `UNKNOWN`; main path has no process-restart executor/replayer; recovery package is untracked/unintegrated | NEEDS REVIEW |
| Cron timezone | three CLI/config tests fail for `Asia/Shanghai`, `America/New_York`, `UTC` in this environment | Deferred; determine whether environment or implementation issue |
| TUI packaged frontend | dist entry absent; child/handshake tests cannot launch | Deferred until UI build dependency/artifact is supplied |
| PicoBench on Windows | import fails because `benchmarks/picobench` imports POSIX-only `fcntl` | Deferred platform port or supported execution environment |
| Optional channel collection | `websockets`, `lark_oapi`, `botpy`, `wecom_aibot_sdk` unavailable | Deferred optional dependency setup |
| Token-wise pricing | 10 tests fail: observed `0.001719816` vs expected `0.00125`, with cache call/write expectations unmet | Existing behavior; no source fix in Phase 0 |
| Evolver real benchmark evidence | no `.pico` artifacts, subject or model run | Deferred; must be run/reviewed with approved environment |

## 9. Tests Executed

All Python commands below used the existing environment `<parent-root>\.pico-baseline-venv\Scripts\python.exe` and were run from the repository root. No application source was changed in response to failures.

### 9.1 Command/help smoke

```powershell
& '<parent-root>\.pico-baseline-venv\Scripts\python.exe' -m pico --version
& '<parent-root>\.pico-baseline-venv\Scripts\python.exe' -m pico --help
& '<parent-root>\.pico-baseline-venv\Scripts\python.exe' -m pico evolve --help
& '<parent-root>\.pico-baseline-venv\Scripts\python.exe' -m pico.evolver --help
```

Result: exit 0 for all four commands. Version output: `✦ Pico v0.1.7`. Help exposed the expected CLI and Evolver command surfaces.

### 9.2 Test collection and grouped pytest runs

| Command / selected scope | Result | Duration |
|---|---|---:|
| `... -m pytest --collect-only -q` | exit 2; 3,485 selected tests collected, 23 deselected, 33 collection errors | 13.78 s |
| Explicit core AgentLoop/Runtime/Tool/Spine/Sandbox/Subagent group | exit 1; 510 passed, 7 failed, 15 errors | 114.13 s pytest / 116.02 s process |
| Explicit Context/Memory/Session/Trace/token-wise group | exit 1; 351 passed, 1 skipped, 10 failed, 3 deselected | 32.62 s pytest / 35.28 s process |
| Explicit CLI/Provider/Channel/Config group excluding optional channel modules | exit 1; 767 passed, 1 skipped, 9 failed, 16 errors | 43.06 s pytest / 46.13 s process |
| Explicit positive AgentLoop/Runtime/Tool/Spine group (30 files) | exit 0; 422 passed | 54.39 s pytest / 57.28 s process |
| Explicit positive Context/Memory/Session/Trace/turn evidence group (17 files) | exit 0; 295 passed, 1 skipped | 18.96 s pytest / 24.78 s process |
| Explicit positive CLI/Provider/config/plugin group (35 files) | exit 0; 587 passed, 1 skipped | 31.93 s pytest / 34.55 s process |
| Evolver/AppWorld group (17 explicit files) | exit 1; 297 passed, 23 failed | 129.96 s pytest / 134.68 s process |
| Existing untracked `tests/test_phase0b1_recovery.py` | exit 0; 21 passed | 1.03 s pytest / 4.64 s process |

The grouped core failures/errors include missing `/tmp` assumptions, POSIX `fcntl`, Windows symlink privilege (`WinError 1314`), checkpoint permission behavior, and subagent trace/state path issues. The context group failures are all token-wise pricing/cache expectation mismatches. The CLI group failures include timezone availability (`Asia/Shanghai`, `America/New_York`, `UTC`) and missing TUI dist/child handshake artifacts. The Evolver failures are concentrated in Windows symlink/path/encoding/CRLF/`/tmp` and AppWorld sandbox semantics.

The TUI RPC group (`tests/test_tui_rpc_*`, `tests/test_tui_turn_logging.py`, `tests/test_tui_cron_tool_wired.py`) was started but exceeded approximately three minutes without output and was interrupted. It has no pass/fail claim here.

### 9.3 PicoBench and UI commands

```powershell
& '<parent-root>\.pico-baseline-venv\Scripts\python.exe' -m benchmarks.picobench --mode smoke
npm test --prefix ui-tui -- --run
```

Results:

- PicoBench: exit 1 in 0.37 s; import failed at `benchmarks/picobench/artifacts.py` with `ModuleNotFoundError: No module named 'fcntl'`.
- UI: exit 1 in 4.22 s; pretest build could not find `esbuild` (`'esbuild' is not recognized`). `ui-tui/node_modules` and `ui-tui/dist/entry.js` were absent.

## 10. Evidence

### 10.1 Code evidence

- CLI: `pico/__main__.py`, `pico/cli/commands.py`, `pico/cli/agent_commands.py`, `pico/cli/_repl_spine.py`, `pico/cli/_runtime_assembly.py`, `pico/cli/tui_commands.py`.
- TUI wire path: `pico/tui_rpc/spine.py` and related RPC dispatcher/server/subscription modules.
- Turn/scheduler: `pico/spine/turn.py`, `pico/spine/scheduler.py`, `pico/agent/spine_runner.py`.
- Agent core: `pico/agent/loop/main.py`, `pico/agent/loop/checkpoint.py`, `pico/agent/tools/registry.py`, `pico/agent/tools/execution.py`.
- Durable effects: `pico/agent/effects.py`; status model and JSONL journal are present and registry-integrated.
- Context: `pico/context_engine/assembler.py`, `factory.py`, `history_trimmer.py`; legacy boundary `pico/agent/context/builder.py`.
- Repo tools: `pico/agent/tools/filesystem.py`, `pico/agent/tools/file_search.py`.
- Memory/session/trace: `pico/memory_engine/`, `pico/session/`, `pico/tracing/trace.py`, `pico/tracing/semconv.py`.
- Evolver: `pico/evolver/cli.py`, `launch/registry.py`, `orchestrator/production.py`, `activation/artifacts.py`, `candidate_evidence.py`, `candidate_manifest.py`; AppWorld files under `benchmarks/appworld/evolve/`.

### 10.2 Test evidence

Representative passing names/groups include `test_durable_turn_id`, `test_effects_journal`, `test_tool_registry_effects`, runtime/spine/tool registry/security suites, ContextAssembler/history trimming suites, memory/session/trace suites, and the positive CLI/provider/config/plugin group. The recovery-only test `tests/test_phase0b1_recovery.py` passed 21 tests but is untracked and not connected to the AgentLoop.

Representative failing evidence includes `test_token_wise_pricing.py`, checkpoint/symlink/path guard tests, TUI child path/handshake tests, cron timezone tests, PicoBench import, and AppWorld sandbox/small-real/surface-contract tests. Full node-level output remains in the pytest process output; no failure was silently repaired.

### 10.3 Artifact evidence

Read-only checks found no `.pico` run directory, no materialized local Evolver `subject/`, and no model/benchmark run output. The absence is itself material to the Evolver audit: implementation and unit-test contracts exist, but this checkout contains no fresh run metadata, candidate evidence, sealed result, activation record, or rollback record proving reproducibility.

## 11. Files Changed

Created by this Phase 0 task:

- `reports/PHASE_00_LUNA_EXECUTION_REPORT.md`

Application source changes: **none**. Tests were run only; no failure was fixed. The following pre-existing untracked paths were preserved and not modified:

- `pico/agent/recovery/`
- `tests/test_phase0b1_recovery.py`

The final worktree check showed no tracked diff and only those pre-existing untracked paths plus this report.

## 12. Deferred Findings

1. Establish a supported Windows test contract or run POSIX-only suites in their intended environment. `fcntl`, `/tmp`, symlink privileges, path separators, line endings and default encoding currently obscure portability defects.
2. Install/build the TUI frontend in a controlled environment and run the interrupted RPC group; verify packaged `entry.js`, handshake, turn events, completion and cancellation end to end.
3. Decide whether token-wise pricing/cache failures represent a changed contract or a regression, then test the decision without changing it in Phase 0.
4. Define cron timezone portability and whether the environment must ship timezone data.
5. Integrate or explicitly retire the untracked `pico/agent/recovery/` draft. Do not present its 21 passing tests as shipped crash recovery.
6. Specify cross-process recovery semantics around `UNKNOWN` effects, including ownership, durable journal location, replay policy, and operator-visible terminal states.
7. Run a real, reproducible Evolver/AppWorld job only with the required external subject/model/benchmark environment; retain all run, candidate, sealed, activation and rollback artifacts for Web Review.
8. Add a public activation/rollback surface only after the human-review and artifact contract is approved; current API/state tests are not a user-facing command contract.
9. Verify bootstrap instructions, especially the absence of explicit `AGENTS.md` discovery, against the intended product contract.
10. Reconcile docs examples with the actual local registry and supported Candidate Labels; historical digests are not local evidence.

## 13. Interview-Relevant Decisions

- Treat `AgentLoop` plus `Scheduler` plus `ToolRegistry` as the current execution spine because both CLI entry modes converge there; this avoids inventing a second runtime path during reconstruction.
- Keep the durable effect journal boundary, but classify recovery as `NEEDS REVIEW`: an `UNKNOWN` effect is safer than an automatic replay, while the current journal alone does not provide process-restart recovery.
- Classify Checkpoint as `HARDEN`, not Resume: shadow-Git filesystem snapshots are useful state evidence but do not contain full conversation state or arbitrary side effects.
- Classify Context Engine as `HARDEN`: `ContextAssembler` is the shipping path, while the low-level context builder remains compatibility/legacy support and should not be mistaken for a second main engine.
- Report the repository as `PARTIAL` because the positive core groups pass but full collection, platform-sensitive groups, UI execution and live Evolver evidence are incomplete. This is a test-evidence tradeoff, not a source-code fix.
- Treat Candidate Label support as intentionally narrow: only the runtime/AppWorld policy has a complete mutable-path/fixture/evaluator/activation contract; other labels remain explicit unsupported configurations.

## 14. Claims Proven

- The package and console entry resolve to `pico.cli.commands:run`; version/help command smoke passes.
- `pico run` and bare `pico` have distinct front doors that converge on the same assembled AgentLoop runtime.
- Turn, Scheduler, AgentLoop, ContextAssembler, ToolRegistry, Session and Trace implementations are present on the inspected main path.
- The positive core groups passed: 422 AgentLoop/Runtime/Tool/Spine tests, 295 Context/Memory/Session/Trace tests with one skip, and 587 CLI/Provider/config/plugin tests with one skip.
- Effect journal states and tool registry integration exist in the current HEAD.
- Evolver CLI, candidate manifest/evidence gates, AppWorld pipeline, sealed evaluator and activation artifact state machine are implemented and broadly unit-tested.
- No application source was modified by Phase 0; the recovery package/test were pre-existing untracked files.

## 15. Claims NOT Proven

- The full repository test suite is green.
- The packaged TUI frontend and RPC turn flow work end to end in this checkout.
- PicoBench runs on the supported environment used for this baseline.
- Token pricing/cache behavior matches the expected contract.
- Checkpoint/Resume provides complete crash recovery across process restart.
- Every tool side effect is attributable, reversible or replay-safe after a crash.
- Memory is durably effective across processes or providers.
- A real provider/LLM conversation, real AppWorld run, candidate selection, activation or rollback has succeeded from this checkout.
- Evolver runtime artifacts are reproducible here; none were generated.
- Windows failures are all implementation defects rather than missing POSIX assumptions, privileges, optional dependencies or environment data.

## 16. Reviewer Questions

1. Should the next phase prioritize a supported Windows portability contract, or should POSIX-only components be validated in their intended CI/runtime environment first?
2. Is `pico run` the required reviewer-facing CLI acceptance path, with bare `pico` treated as a separately gated TUI acceptance path?
3. Should the untracked recovery draft be integrated into the main AgentLoop, or should the effect journal/recovery surface be redesigned before integration?
4. Which environment and artifact-retention policy should be used for the first real Evolver/AppWorld evidence run?
5. Is the current Candidate Label policy intentionally runtime-only for this milestone, or must the unsupported label contracts be completed before reconstruction proceeds?

**Phase 0 stop condition:** report written; no application source rewritten; wait for Web Reviewer.
