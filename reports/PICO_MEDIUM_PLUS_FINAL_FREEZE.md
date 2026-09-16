# PICO Medium+ — Final Freeze Report

This report records the reproducible repository snapshot produced after the
Web Sol final review. The freeze scope is documentation reconciliation,
repository hygiene, final verification and Git snapshotting only. No Phase 9
work, runtime redesign, deferred-debt repair or interview/resume work is in
scope.

## 1. Freeze Verdict

**PASS — PICO MEDIUM+ FROZEN**. The accepted tree was committed, the
committed-tree status was clean, and the authoritative baseline passed again.

## 2. Repository Path

`<repo-root>`

## 3. Branch

`feat/durable-execution-phase0`

## 4. Pre-Freeze HEAD

`0ae70289b282bdc808e668bd267c7370ffd0e5b8`

## 5. Final Freeze Commit SHA

`74e687ce53d82fc93cea974166a12651734e06f6`

This is the primary freeze commit containing the accepted Phase 0–8
source/tests/docs/reports and the freeze report. The report was completed in a
documentation-only follow-up commit so this exact SHA could be recorded.

## 6. Python / Pico Version

- OS: Windows 11 (`Windows-11-10.0.26200-SP0`)
- Python: `3.12.14`
- Pico: `✦ Pico v0.1.7`
- Package metadata: `pico-harness` version `0.1.7`
- Required Python range: `>=3.12,<3.13`

## 7. Files Included in Freeze

The staged snapshot includes the accepted Phase 0–8 working-tree content and
the two confirmed freeze-scope updates:

- Runtime and production boundaries under `pico/`, including recovery,
  context budget/assembly, memory, tracing, Scheduler/Spine, CLI run surface,
  and controlled Evolver lineage/orchestration/activation support.
- Accepted deterministic evidence/runtime support under `benchmarks/picobench/`
  and the Phase 6 repository fixture/reproducibility helper.
- Accepted Phase 0–8 contract tests, including the Phase 7 demo fixture and
  the existing TUI bootstrap assertion updated for the corrected help text.
- `scripts/run_medium_baseline.ps1`, which is the explicit authoritative
  44-file acceptance manifest.
- `docs/medium-plus-baseline.md` and `docs/tool-runtime-contract.md`.
- `reports/PHASE_00_LUNA_EXECUTION_REPORT.md` through
  `reports/PHASE_08_LUNA_EXECUTION_REPORT.md` and
  `reports/PICO_MEDIUM_PLUS_FINAL_SYSTEM_AUDIT.md`.
- This report: `reports/PICO_MEDIUM_PLUS_FINAL_FREEZE.md`.
- The staged path list was inspected with `git diff --cached --name-status`
  before commit; no accepted untracked source was omitted.

## 8. Generated/Local Files Excluded

Ignored or local-only residue remains excluded from the snapshot:

- `.pico/` runtime/evidence state, including lock residue;
- `__pycache__/`, `*.pyc`, `.pytest_cache/`, coverage and tool caches;
- local virtual environments, build/dist output, logs and temporary benchmark
  workspaces/results;
- secrets, credentials, API keys and environment-specific generated files.

The existing `.gitignore` was respected. No ambiguous untracked file was found:
the untracked source, tests, fixtures, baseline script and Phase 0–8 reports
all map to the accepted implementation or review record.

A high-confidence secret-pattern review found no credential or API-key secret;
the only API-key-shaped matches are literal `phase7-fixture` test values.

## 9. Documentation Drift Fixed

- `docs/medium-plus-baseline.md` now describes the final Phase 1–8 manifest,
  its current 671-test result, repository `.venv` interpreter priority and
  the distinction between manifest inclusion and normal `pico run` execution.
- The same document now states that the accepted deterministic PicoBench and
  Evolver contract tests are in the manifest while neither is an automatic
  `pico run` stage.
- `pico/cli/tui_commands.py` no longer recommends the unsupported
  `pico run --legacy-repl`; the remediation points to the supported
  `pico run --help` guidance.
- `tests/test_cli_tui_bootstrap.py` was updated only to assert that confirmed
  corrected user-facing guidance.

No TUI redesign or Medium+ scope expansion was performed.

## 10. Pre-Commit Baseline Result

Command:

```powershell
.\scripts\run_medium_baseline.ps1
```

Result: **exit 0 — 671 passed in 118.76s (0:01:58)**.

This is the explicit 44-file manifest, not unrestricted repository collection.
It uses the repository `.venv` first, matching the reconciled documentation.

## 11. Post-Commit Baseline Result

The primary freeze commit was verified from its committed tree:

```text
exit 0 — 671 passed in 143.11s (0:02:23)
```

## 12. git diff --check Result

`git diff --check` completed with exit 0 and no whitespace errors. Git emitted
only existing LF-to-CRLF normalization warnings for the Windows working tree.
The staged diff was inspected as well; its check reports intentional Markdown
hard-break trailing spaces in preserved historical report headers and one
historical blank EOF line, with no code or freeze-file whitespace issue. Those
historical reports were not rewritten during freeze.

## 13. CLI Help/Version Checks

- `python -m pico --version` → `✦ Pico v0.1.7`.
- `python -m pico run --help` exits successfully, exposes the supported
  message/session/continue/resume/workspace/config/render/logging options and
  does not expose `--legacy-repl`.
- `python -m pico evolve --help` exits successfully and exposes the explicit
  `run`, `check`, `status` and `finalize` commands; the help text retains the
  manual-activation-by-default boundary.

## 14. Runtime / Evolver Isolation Check

The supported runtime remains:

```text
pico run
→ RuntimeAssembly
→ TurnRequest
→ Scheduler / Lane
→ AgentTurnRunner
→ AgentLoop
→ ContextAssembler
→ Provider
→ ToolRegistry
→ Observation
→ AgentLoop
→ Session / EffectJournal / Trace
```

A source scan of the normal `pico run` assembly, CLI run command, AgentLoop,
Spine and REPL surface found no `evolver` or `pico.evolve` reference. Evolver
is reachable only through the explicit `pico evolve` surface. No paid or live
provider evaluation was run.

## 15. Remaining Deferred Debt

The Final System Audit's non-authoritative broad Evolver/AppWorld probe remains
classified as deferred: 285 passed and 21 failed, with the failures dominated
by Windows symlink privilege (`WinError 1314`), POSIX executable/AppWorld
assumptions, Windows Git path spelling in small-real fixtures and one
Windows-default-codec docs test. These tests are outside the 671-file
acceptance manifest and were not repaired during freeze.

The accepted claim boundary also leaves TUI frontend/RPC completion, channel
SDKs, Cron behavior, live provider quality, external runtimes, real VM
execution, broad cross-platform symlink behavior and other historical Phase 0
debt deferred. The zero-byte ignored PicoBench lock residue is local hygiene,
not a tracked release artifact.

## 16. SAFE Final Claims

- The final explicit Phase 1–8 acceptance manifest passes 671 tests on the
  recorded Windows/Python environment.
- The supported `pico run` path converges on one RuntimeAssembly → Scheduler →
  AgentLoop path with the accepted Context, Tool, Session, Effect and Trace
  boundaries.
- Normal `pico run` does not automatically invoke Evolver; Evolver remains
  explicitly opt-in through `pico evolve`.
- The two confirmed documentation drifts were reconciled without redesigning
  the runtime or expanding Medium+ scope.
- The freeze snapshot stages accepted Phase 0–8 source/tests/docs/reports and
  excludes ignored local runtime residue.
- The committed-tree result is reproducible by rerunning the explicit
  baseline script with the recorded interpreter identity.

## 17. Claims Still NOT Proven

This freeze does not prove:

- a full unrestricted repository test-suite pass;
- general cross-platform support or Windows symlink privilege;
- AppWorld portability, TUI acceptance, channels or Cron acceptance;
- live provider quality, live Evolver improvement or production activation;
- automatic rollback, cryptographic sealed isolation or exactly-once execution;
- full process resurrection, perfect Memory or zero-loss Context;
- production-grade security, multi-user/remote/supply-chain certification;
- arbitrary external-effect replay or a successful checkpoint restoration of
  process state;
- statistically significant general self-improvement.

## 18. Final git status

Verified clean after the documentation-only report-completion commit:

```text
git status --short → empty
```

Ignored local residue may remain, but no ignored artifact is tracked.
