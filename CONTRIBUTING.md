# Contributing to Looprail

Thanks for helping improve Looprail. Contributions are most useful when they
keep the Runtime predictable, make behavior observable, and include the
smallest test or documentation change needed to explain the result.

## Before you start

For a bug fix or feature, open an issue in the repository's issue tracker when
possible. Describe the problem, the affected contract, and how you plan to
verify it. Keep unrelated cleanup out of the same change.

## Development setup

Looprail currently targets Python 3.12. The native TUI uses Node.js 22 and
`uv` manages the Python environment.

From a source checkout:

```bash
uv sync --frozen --extra dev --dev
npm ci --prefix ui-tui
npm run build --prefix ui-tui
```

The TUI build creates the local bundle used by packaged and installed CLI
checks. Do not commit generated build output unless the repository's release
process explicitly requires it.

## Verification

Run focused tests first, then checks that match the scope of the change:

```bash
uv run --frozen --extra dev pytest -q tests/test_public_release_tree.py
uv run --frozen --extra dev ruff check looprail scripts benchmarks tests
git diff --check
```

The supported deterministic Runtime regression baseline is run from
PowerShell:

```powershell
pwsh -NoProfile -File scripts/run_runtime_baseline.ps1 -Python .\.venv\Scripts\python.exe
```

The baseline is intentionally explicit and currently reports 671 passing
tests. LooprailBench has separate deterministic suites under
`benchmarks/looprailbench/`; do not treat live or paid evaluation as a normal
pull-request check.

For TUI changes, also run:

```bash
npm run lint:rpc --prefix ui-tui
npm run type-check --prefix ui-tui
npm run build --prefix ui-tui
```

## Change expectations

- Preserve existing Runtime contracts unless the change explicitly updates the
  contract and its tests.
- Add or update a regression test for observable behavior changes.
- Update the relevant public documentation when commands or user-visible
  behavior change.
- Keep credentials, tokens, private URLs, personal data, raw traces, local
  paths, generated benchmark output, and environment files out of commits.
- Prefer small, reviewable changes. Do not add a dependency without explaining
  why it is necessary.
- Use clear branch names and Conventional Commit messages, for example
  `fix: preserve resume evidence` or `docs: clarify local setup`.

## Pull requests

A pull request should include:

- a concise description of the change and its reason;
- the contracts or files affected;
- exact verification commands and results;
- known platform or environment limitations;
- any migration or rollback consideration.

Do not commit generated reports, screenshots, videos, PDFs, HTML artifacts, or
large local outputs. Keep internal evidence and credentials outside the public
tree.

## Security reports

Do not disclose vulnerabilities in a public issue, pull request, chat, or
captured log. Follow [SECURITY.md](SECURITY.md) for the private reporting
boundary.
