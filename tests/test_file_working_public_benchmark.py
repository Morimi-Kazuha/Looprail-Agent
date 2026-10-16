"""Public offline fixtures and verifier; these tests make no model requests."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.looprailbench.file_working.tasks import freeze, tasks

VERIFY = Path(__file__).parents[1] / "benchmarks/looprailbench/file_working/verify.py"


def run_verifier(workspace: Path, task_file: Path):
    return subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(workspace), str(task_file)],
        capture_output=True,
        cwd=workspace.parent,
        timeout=10,
    )


@pytest.mark.parametrize("task", tasks(), ids=lambda task: task["id"])
def test_frozen_invoice_fixture_and_independent_verifier(tmp_path, task):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name, content in task["initial"].items():
        (workspace / name).write_text(content, encoding="utf-8", newline="\n")
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps(task), encoding="utf-8")

    assert run_verifier(workspace, task_file).returncode != 0

    if task["event"] == "rename":
        (workspace / "billing.py").rename(workspace / "invoice.py")
    for name, content in task["offline_oracle"].items():
        (workspace / name).write_text(content, encoding="utf-8", newline="\n")
    (workspace / "review.md").write_text(
        "Invoice arithmetic review with explicit rounding contract.\n", encoding="utf-8"
    )

    result = run_verifier(workspace, task_file)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_frozen_invoice_manifest_is_deterministic(tmp_path):
    assert freeze(tmp_path / "one") == freeze(tmp_path / "two")
