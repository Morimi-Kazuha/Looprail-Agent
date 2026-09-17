"""Bounded, non-secret identity for a LooprailBench evaluation artifact."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .canonical import canonical_digest
from .environment import capture_environment_identity
from .verifier import Verifier, verifier_identity

REPRODUCIBILITY_MANIFEST_SCHEMA = "looprail.bench.reproducibility.v1"


def capture_reproducibility_manifest(
    repository_root: Path,
    *,
    task_id: str,
    fixture_id: str,
    strategy_id: str,
    verifier: Verifier,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture only bounded evaluation identity, never ambient environment."""
    root = Path(repository_root)
    environment = capture_environment_identity(root)
    commit = _git_commit(root)
    manifest = {
        "schema": REPRODUCIBILITY_MANIFEST_SCHEMA,
        "looprail": {
            "source_commit": commit,
            "working_tree_identity": "workspace-local",
        },
        "python": {
            "implementation": platform.python_implementation(),
            "version": sys.version.split()[0],
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "task": {"task_id": task_id, "fixture_id": fixture_id},
        "strategy": {"strategy_id": strategy_id},
        "verifier": verifier_identity(verifier),
        "config": dict(config or {}),
        "environment_digest": canonical_digest(environment),
    }
    return manifest


def _git_commit(root: Path) -> str:
    git = shutil.which("git")
    if not git:
        return "unavailable"
    try:
        completed = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    commit = completed.stdout.strip()
    return commit if commit else "unavailable"


__all__ = ["REPRODUCIBILITY_MANIFEST_SCHEMA", "capture_reproducibility_manifest"]
