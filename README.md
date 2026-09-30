# Looprail

[![Python 3.12](https://img.shields.io/badge/python-3.12%2B-3776AB.svg)](https://www.python.org/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)

[Repository](https://github.com/Morimi-Kazuha/Looprail-Agent) · [Issues](https://github.com/Morimi-Kazuha/Looprail-Agent/issues)

**A local-first Coding Agent Runtime for long-running tasks in real code repositories.**

[中文说明](README.zh-CN.md)

Looprail separates model reasoning from deterministic runtime execution. The
model decides what to do; the Runtime governs tool execution, context assembly,
state persistence and recovery, tracing, and evaluation. The result is a
repository-aware execution loop that can keep working across many steps.

## Core Features

- **Agent Loop** — Carries a repository task through inspection, changes,
  commands, observations, and follow-up decisions.
- **Governed Tool Runtime** — Validates tool calls and applies execution,
  filesystem, timeout, and effect boundaries.
- **Repository-aware Context Engine** — Builds bounded context from the
  workspace, history, tools, and relevant memory.
- **Durable Session + Checkpoint / Resume** — Persists task state and supports
  conservative recovery after interruption.
- **Structured Memory** — Stores scoped, attributed knowledge for later work.
- **Trace + Deterministic Evaluation** — Makes execution inspectable and
  checks important contracts without requiring a live model.
- **Controlled Self-Evolution** — Evaluates candidate Runtime changes behind
  explicit evidence and human activation.
- **Local-first execution** — Works from a local checkout with the repository
  and its normal development tools in view.

## Simple Architecture

```mermaid
flowchart TD
    U[User / CLI] --> L[Agent Loop]
    L --> C[Context Engine]
    C <--> S[Session / Structured Memory]
    C --> M[Model]
    M --> L
    L --> T[Tool Runtime]
    T --> R[Repository / Shell / Tests]
    R --> L
    L --> E[Trace / Evaluation]
    S -. Checkpoint / Resume .-> L
```

The model handles uncertain reasoning. The Runtime provides the deterministic
contracts around that reasoning.

## Quick Start

Looprail is currently documented from a source checkout; this workflow does
not assume a package has been published to PyPI.

From the repository root:

```bash
git clone https://github.com/Morimi-Kazuha/Looprail-Agent.git
cd Looprail-Agent
uv sync --frozen --extra dev --dev
uv run --frozen looprail --version
```

Configure a Provider and the local Runtime from the interactive wizard:

```bash
uv run --frozen looprail onboard --skip-memory
```

`--skip-memory` is the supported source-checkout setup when no external Memory
implementation is installed. The wizard still configures the Provider and
local execution path.

## Usage Example

Run one task against a real repository:

```bash
uv run --frozen looprail run --workspace "<repo-root>" -m "Inspect the failing tests, make the smallest safe fix, run the relevant tests, and summarize the result."
```

Replace `<repo-root>` with the target repository path. Run
`uv run --frozen looprail run --help` for session, resume, configuration, and
output options.

## Testing

The supported deterministic Runtime regression baseline is:

```powershell
.\scripts\run_runtime_baseline.ps1 -Python .\.venv\Scripts\python.exe
```

The current baseline reports **671 passed** on Windows with Python 3.12.
LooprailBench provides additional deterministic evaluation infrastructure; its
source and reproducibility helpers live under
[`benchmarks/looprailbench/`](benchmarks/looprailbench/).

## Policy-Guarded Semantic Recovery

```text
Deterministic failure detection → semantic recovery advice
→ local policy validation → bounded runtime execution
```

**Trigger:** only consecutive hard failures of the same tool, at the existing
recovery boundary, at most once per turn while iteration budget remains.
**Advisor:** Jev recommends only `RECHECK_INPUTS`, `USE_ALTERNATIVE`, or `STOP`.
**Guard:** local schema, enum, probability and confidence validation; Runtime
checks trigger eligibility, available actions and remaining budget.
**Authority:** Runtime owns every subsequent tool call and its permissions.

```mermaid
flowchart TD
    D[Tool failure detector] --> T[Recovery trigger and budget check]
    T --> A[Jev DecisionAdvisorPort]
    A --> C[Structured Choice]
    C --> P[Local Policy Guard]
    P --> R[RECHECK_INPUTS]
    P --> U[USE_ALTERNATIVE]
    P --> S[STOP]
    R --> X[Runtime authority]
    U --> X
    S --> X
    X --> Tools[Governed tools]
    X --> I[Tool-free summary; interrupted]
    A -. unavailable .-> F[Original deterministic recovery]
    P -. invalid / low confidence .-> F
    F --> X
```

Disabled mode, missing keys, timeout, network errors, invalid responses/schema
and low confidence preserve Looprail's original deterministic recovery. Optional
advisor failures do not crash the Agent Runtime. Cancellation still propagates.
Jev cannot directly invoke tools, replay side effects, bypass budgets, or mark
interrupted work as successful. RECHECK/ALTERNATIVE inject fixed local hints;
the main model can ignore them. Policy-accepted STOP disables future tool batches
for this turn, produces a tool-free summary, and persists `interrupted`.
The current batch has already finished when recovery is considered.

Enable with `LOOPRAIL_JEV_ENABLED=true` and `LOOPRAIL_OPENROUTER_API_KEY` for
OpenRouter, or `LOOPRAIL_JEV_API_KEY` for direct TypeSafe with an explicit model.
Only Looprail's configuration contract is read. The default OpenRouter model is
`typesafe/jev-1.13`; text-provider configuration remains independent. Decisions
appear as `agent.recovery.decision` / `JEV_DECISION` with confidence, fallback,
local reason and latency. Credentials stay in the environment.

The configurable **0.65** confidence threshold is a conservative default,
not a benchmarked optimum. The [15-case recovery scaffold](benchmarks/recovery/README.md)
compares decisions against acceptable action sets and records distributions,
fallbacks and latency. `python -m scripts.eval_jev_recovery` defaults to an
offline fake; its results validate the harness, not Jev accuracy or task-success
gains. See [architecture and demos](docs/JEV_INTEGRATION.md) and
[validation evidence](docs/JEV_VALIDATION.md).

## Roadmap

- Broader cross-platform validation.
- Expanded deterministic evaluation.
- A clearer public release and distribution workflow.

## License

Looprail is released under the [Apache License 2.0](LICENSE).

Third-party attribution and notices are preserved in [NOTICES.md](NOTICES.md)
and [LICENSES/](LICENSES/).
