# ForgeAgent — GitHub Packaging Report

## Status

**PASS — FORGEAGENT README READY FOR WEB SOL REVIEW**

This packaging pass updates the public presentation only. It does not start a new engineering phase, redesign the Runtime, rename internal packages, or change package behavior.

## Public identity

- Public project name: **ForgeAgent**
- Recommended repository name: `forge-agent`
- English positioning: **ForgeAgent — A local-first Coding Agent for long-running tasks in real code repositories.**
- Chinese positioning: **ForgeAgent —— 面向真实代码仓库长链路任务的 Local-first Coding Agent。**
- Recommended GitHub description: `Local-first Coding Agent with durable execution, context management, checkpoint/resume, memory, tracing and evaluation.`
- Public naming note: the Python package and CLI retain `pico` compatibility identifiers, so the README documents `uv run pico`.

## README result

Both landing pages are concise personal open-source documentation and stay within the requested 120–180 line range:

- `README.md`: **121 lines**
- `README.zh-CN.md`: **121 lines**

Both README files contain the same high-level structure:

1. Project title, badges, and one-line positioning
2. Short introduction
3. What the project is / 项目简介
4. Core features
5. One conceptual Mermaid architecture diagram
6. Quick Start / 快速开始
7. Example / 使用示例
8. Testing / 测试
9. Roadmap / 后续计划
10. License / 许可证

The landing pages present ForgeAgent directly, without historical project phases, internal workflow names, audit narrative, or private release instructions. Deep implementation details remain in the existing documentation and reports.

## Quick Start recorded in the README

```bash
cd forge-agent
uv sync --frozen --extra dev --dev
uv run pico --version
uv run pico onboard --skip-memory
uv run pico run --workspace /path/to/project -m "Find the cause of the failing tests, fix the bug, run the relevant tests, and summarize the changes."
```

The command sequence matches the repository's current `uv` workflow and CLI surface. No package, import, persisted-directory, configuration, or CLI rename was performed.

## Roadmap recorded in the README

- Improve CLI visualization and interaction.
- Add richer execution-progress rendering.
- Strengthen sandbox and high-risk tool policies.
- Expand real-repository Coding Agent benchmarks.
- Improve Context compression and retrieval strategies.
- Improve long-term Memory quality and lifecycle.
- Expand Controlled Self-Evolution experiments.
- Improve cross-platform portability.

These items are explicitly aspirational; the README does not present them as completed functionality.

## Attribution and public hygiene

- `LICENSE`, `NOTICES.md`, and `LICENSES/` remain preserved.
- Existing public documentation hygiene changes remain in the worktree; 88 genuine machine-local absolute-path occurrences were normalized across 12 documentation/report files.
- The public-tree checker allowlist and focused regression test cover the intended public documentation and report files.
- No high-confidence private key, API key, or token value was found by the existing hygiene scan.

## Changed files in the packaging worktree

Public landing pages and packaging report:

- `README.md`
- `README.zh-CN.md`
- `reports/PICO_GITHUB_PACKAGING_REPORT.md`

Existing documentation hygiene and public-tree support:

- `docs/medium-plus-baseline.md`
- `reports/PHASE_00_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_01_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_02_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_03_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_04_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_05_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_06_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_07_LUNA_EXECUTION_REPORT.md`
- `reports/PHASE_08_LUNA_EXECUTION_REPORT.md`
- `reports/PICO_MEDIUM_PLUS_FINAL_SYSTEM_AUDIT.md`
- `reports/PICO_MEDIUM_PLUS_FINAL_FREEZE.md`
- `scripts/check_public_tree.py`
- `tests/test_public_release_tree.py`

## Verification

- `git diff --check`: **PASS**, exit code 0.
- `python scripts/check_public_tree.py`: **PASS**, `public release tree: OK`.
- Focused public-tree regression test: **7 passed**.
- Frozen core Runtime acceptance result: **671 passed** on the Windows / Python 3.12 baseline.
- Runtime source untouched: **confirmed**; no files under `pico/` were changed.
- No commit, push, tag, remote change, or package behavior change was performed.

## Review handoff

The repository is ready for Web Sol review of the ForgeAgent public naming and concise GitHub README presentation.
