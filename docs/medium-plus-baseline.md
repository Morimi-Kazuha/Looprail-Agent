# Medium+ Supported Runtime Baseline

This is the final Medium+ supported execution contract for the Windows
development environment. The accepted authoritative manifest includes the
Phase 1–8 contract tests and currently contains 671 passing tests. The
acceptance surface is `pico run`; the TUI, channels, cron, external runtimes,
real VMs and live LLMs are outside this baseline. PicoBench and Evolver are
included through their accepted deterministic Phase 6/8 contract tests, but
normal `pico run` does not automatically execute PicoBench or Evolver.

## Environment

- Windows
- Python 3.12
- Preferred local environment: the repository `.venv`
- Fallback local environment: `<parent-root>\.pico-baseline-venv`
- No system-Python or global dependency changes are required.

## Commands

From the repository root:

```powershell
.\scripts\run_medium_baseline.ps1
```

The script uses the repository `.venv\Scripts\python.exe` first, then
`<parent-root>\.pico-baseline-venv\Scripts\python.exe` when present, and
otherwise the `python` on `PATH`. A different interpreter can be provided
explicitly:

```powershell
.\scripts\run_medium_baseline.ps1 -Python 'D:\path\to\python.exe'
```

The CLI command/help smoke is:

```powershell
$Python = '.\.venv\Scripts\python.exe'
& $Python -m pico --version
& $Python -m pico --help
```

The deterministic real-spine smoke is included in the script as
`tests/test_phase1_medium_baseline.py`. It uses a scripted provider and real
runtime assembly, Turn, Scheduler, AgentLoop, ContextAssembler, `read_file`,
SessionManager, effect journal and clean shutdown.

## Inclusion rule

`scripts/run_medium_baseline.ps1` is the authoritative explicit test manifest.
It includes the main CLI/REPL spine, AgentLoop/provider fakes, tool and
filesystem boundaries, ContextAssembler/history trimming, Session,
checkpoint-basic, Memory, Trace and Effect Journal tests, together with the
accepted Phase 1–8 contract tests. The manifest currently contains 671 passing
tests, including the deterministic PicoBench evidence contract and the
controlled Evolver contract. This inclusion does not make either system an
automatic `pico run` stage, and does not require a live model, paid API, TUI
frontend, channel SDK, external runtime or real VM.

## Semantics

The baseline must preserve schema validation, path/security checks, timeout and
error behavior, Trace evidence, and the effect journal states
`PREPARED/RUNNING/COMMITTED/FAILED/UNKNOWN`. `UNKNOWN` effects are not replayed.
Phase 2's focused `tests/test_phase2_tool_runtime_contract.py` is deliberately
included in the explicit manifest and covers the Registry failure/effect
boundaries without expanding collection to the full repository.

Phase 3's focused `tests/test_phase3_recovery_contract.py` is also deliberately
included. It covers durable recovery projection, conservative effect decisions,
local-write hash reconciliation, missing/corrupt durable inputs, fresh-runtime
resume evidence, and the cross-process new-Turn boundary. Recovery never
automatically replays `COMMITTED`, `FAILED`, or `UNKNOWN` effects.

Phase 5's focused `tests/test_phase5_memory_contract.py` is deliberately
included. It covers the existing MemoryStore's structured item schema,
eligibility, provenance and repository scope, deterministic deduplication,
supersession/invalidation, stale-source exclusion, bounded recall, Context
Assembler injection, recovery contamination prevention, null-backend local
behavior, failure isolation and the real AgentLoop mainline. The baseline
does not require the optional Myna distribution or an external Memory service.
