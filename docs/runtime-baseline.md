# Supported Runtime Baseline

This document describes the supported deterministic Runtime regression check
for a Windows source checkout. It covers the `looprail run` execution path and
its contracts; it is not a product tier or a claim that every optional
integration is available on every platform.

The current manifest reports **671 passed** with Windows and Python 3.12. It
does not require a live model, paid API call, channel SDK, external Memory
service, TUI frontend, external runtime, or real VM.

## Environment

- Windows
- Python 3.12
- Preferred local environment: `.venv`
- Fallback local environment: `<parent-root>\.looprail-baseline-venv`
- No system-Python or global dependency changes are required.

## Run the baseline

From the repository root:

```powershell
.\scripts\run_runtime_baseline.ps1
```

The script tries the repository `.venv\Scripts\python.exe`, then
`<parent-root>\.looprail-baseline-venv\Scripts\python.exe`, and finally the
`python` executable on `PATH`. To select an interpreter explicitly:

```powershell
.\scripts\run_runtime_baseline.ps1 -Python "<python-executable>"
```

The script's manifest is intentionally explicit so that an ordinary repository
test run cannot silently change the supported regression set. Use `-Help` for
the script synopsis and parameter details.

The basic CLI checks are:

```powershell
$Python = ".\.venv\Scripts\python.exe"
& $Python -m looprail --version
& $Python -m looprail --help
```

## What it covers

The manifest includes the main CLI and REPL spine, Agent Loop and Provider
fakes, tool and filesystem boundaries, Context assembly and history trimming,
Session, checkpoint and recovery, Structured Memory, Trace, and Effect Journal
contracts. It also includes the accepted deterministic LooprailBench evidence
and controlled self-evolution contract tests.

These tests verify the local Runtime path. They do not make LooprailBench or
Controlled Self-Evolution an automatic stage of `looprail run`, and they do not
require a live Provider, paid call, channel, external Memory backend, or VM.

## Contract boundary

The baseline protects schema validation, workspace and path checks, timeout and
error behavior, Trace evidence, and effect-journal states:

```text
PREPARED → RUNNING → COMMITTED / FAILED / UNKNOWN
```

`UNKNOWN` effects are not replayed automatically. The focused
`tests/test_tool_runtime_contract.py` covers the Registry effect and failure
boundary. `tests/test_durable_recovery_contract.py` covers conservative resume
and reconciliation behavior. `tests/test_memory_lifecycle_contract.py`
covers structured item scope, provenance, deduplication, invalidation, recall,
Context injection, and failure isolation.

## Related evaluation

LooprailBench is the repository's deterministic evaluation infrastructure. Its
stable suites, reproduction helpers, and evidence contracts live under
[`benchmarks/looprailbench/`](../benchmarks/looprailbench/). Generated evidence
belongs outside Git.
