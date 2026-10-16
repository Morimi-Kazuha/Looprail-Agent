# File Working Memory

File Working Memory (FWM) is an optional cache of file observations made by
Looprail's trusted built-in `read_file` tool. It is separate from long-term
structured memory and from context compression.

## What is stored and checked

A successful built-in read can produce a receipt containing the repository
identity, access-scope identity, canonical relative path, source SHA-256, byte
and line counts, the returned line range, and bounded exact excerpts. A failed
read does not produce a reusable receipt. MCP tools, plugins, and other tools
with a matching name cannot issue one. Common secret/config paths and private
key file types are excluded from observation.

Recall is limited to a small number of entries, excerpts, bytes, and estimated
tokens. Before reuse, Looprail reopens the source file, rejects linked or
reparse paths, hashes the full current file, and checks each quoted line against
the recorded returned range. A changed or unreadable source is skipped. The
excerpt is marked as untrusted evidence; a matching hash and quote prove that
the text is grounded in the current file version, not that its meaning is
correct.

Context assembly suppresses an excerpt when the same lines are already visible
in the current turn, admits it only within the context budget, and retires it
after new tool output or a source change. Optional evidence is dropped before
the Runtime asks the existing context-compression path to remove more history.

## Enablement and supported host

The feature is **off by default**. The Looprail `config.json` accepts this
root-level feature block (the loader accepts the `fileWorking` camel-case key):

```json
{
  "fileWorking": {
    "enabled": true
  }
}
```

Cross-session reuse has been verified for the Windows local single-user CLI.
The CLI derives a stable scope from the Windows OS token SID and the host-state
path only after checking that the external state directory and its ACLs meet
the local policy. The feature does not create or repair that directory's ACL.
If the checks fail, the CLI skips FWM. The host-state directory must be outside
the repository; a repository-controlled cache is rejected.

The TUI, Gateway, non-Windows hosts, mapped drives, and arbitrary
`--workspace` arrangements do not claim the same cross-session support. Without
the Windows CLI identity proof, there is no stable cross-session scope. The
feature also does not guarantee that the model will skip a file read.

## Relationship to other runtime features

- **R1 structured memory** keeps longer-lived, scoped user or project
  knowledge.
- **R2 context compression** carries current work state as the context budget
  tightens.
- **File Working Memory** carries versioned observations from successful file
  reads.
- **JEV** offers bounded recovery suggestions after eligible tool failures.

These mechanisms have different evidence and authority boundaries. FWM does
not write observations into the long-term MemoryStore or choose tools for the
agent.

## Regression commands

From a clean checkout with the development dependencies installed, run the
deterministic contracts:

```powershell
uv run --frozen pytest -q tests/test_file_working_memory.py tests/test_file_working_runtime.py tests/test_file_working_cli.py tests/test_file_working_public_benchmark.py
uv run --frozen ruff check `
  looprail/agent/loop/main.py looprail/agent/tools/base.py `
  looprail/agent/tools/filesystem.py looprail/agent/tools/registry.py `
  looprail/cli/_file_working_scope.py looprail/cli/_runtime_assembly.py `
  looprail/cli/agent_commands.py looprail/config/loader.py `
  looprail/config/looprail.py looprail/context_engine/assembler.py `
  looprail/context_engine/factory.py looprail/context_engine/segments/memory.py `
  looprail/memory_engine/file_working.py tests/test_file_working_memory.py `
  tests/test_file_working_runtime.py tests/test_file_working_cli.py `
  tests/test_file_working_public_benchmark.py `
  benchmarks/looprailbench/file_working/tasks.py `
  benchmarks/looprailbench/file_working/verify.py
```

These tests use temporary repositories, deterministic providers, and synthetic
offline fixtures. They do not make real provider requests. See [the evaluation
note](file-working-memory-evaluation.md) for the boundary between these
contracts and the one live A/B pair.
