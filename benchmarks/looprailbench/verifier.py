from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from .records import VerificationState, VerifierResult


class Verifier(Protocol):
    async def verify(self, workspace: Path) -> VerifierResult: ...


@dataclass(frozen=True)
class VerifierSeal:
    path: Path
    digest: str

    @classmethod
    def capture(cls, path: Path) -> VerifierSeal:
        path = Path(path)
        return cls(path=path, digest=_file_digest(path))

    def intact(self) -> bool:
        return self.path.is_file() and _file_digest(self.path) == self.digest

    @classmethod
    def capture_many(cls, paths: tuple[Path, ...] | list[Path]) -> "VerifierBundleSeal":
        """Seal a verifier bundle (task definition + expected artifacts)."""
        return VerifierBundleSeal.capture(tuple(paths))


@dataclass(frozen=True)
class VerifierBundleSeal:
    entries: tuple[tuple[Path, str], ...]

    @classmethod
    def capture(cls, paths: tuple[Path, ...]) -> "VerifierBundleSeal":
        return cls(tuple((Path(path), _file_digest(Path(path))) for path in paths))

    def intact(self) -> bool:
        return all(path.is_file() and _file_digest(path) == digest for path, digest in self.entries)


@dataclass(frozen=True)
class VerifierExecution:
    result: VerifierResult
    infrastructure_error: str | None = None


@dataclass(frozen=True)
class JsonArtifactVerifier:
    expected_path: Path
    artifact_path: str
    forbidden_paths: tuple[str, ...] = ()

    @property
    def verifier_id(self) -> str:
        return "json_artifact_v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_path",
            require_normalized_relative_path(
                self.artifact_path,
                field_name="artifact_path",
            ),
        )
        object.__setattr__(
            self,
            "forbidden_paths",
            tuple(
                require_normalized_relative_path(
                    path,
                    field_name="forbidden_paths",
                )
                for path in self.forbidden_paths
            ),
        )

    async def verify(self, workspace: Path) -> VerifierResult:
        workspace = Path(workspace).resolve()
        artifact = _resolve_workspace_path(workspace, self.artifact_path)
        if artifact is None:
            return VerifierResult(
                state=VerificationState.FAILED,
                findings=(f"artifact_path_outside_workspace:{self.artifact_path}",),
            )
        resolved_forbidden = tuple(
            (relative, _resolve_workspace_path(workspace, relative)) for relative in self.forbidden_paths
        )
        escaped_forbidden = tuple(relative for relative, resolved in resolved_forbidden if resolved is None)
        if escaped_forbidden:
            return VerifierResult(
                state=VerificationState.FAILED,
                findings=tuple(f"forbidden_path_outside_workspace:{path}" for path in escaped_forbidden),
            )
        forbidden = [
            relative for relative, resolved in resolved_forbidden if resolved is not None and resolved.exists()
        ]
        if forbidden:
            return VerifierResult(
                state=VerificationState.FAILED,
                findings=tuple(f"forbidden_path:{path}" for path in forbidden),
            )
        if not artifact.is_file():
            return VerifierResult(
                state=VerificationState.FAILED,
                findings=(f"missing_artifact:{self.artifact_path}",),
            )
        try:
            expected = json.loads(Path(self.expected_path).read_text())
            actual = json.loads(artifact.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return VerifierResult(
                state=VerificationState.FAILED,
                findings=(f"invalid_json:{type(exc).__name__}",),
            )
        if actual != expected:
            return VerifierResult(
                state=VerificationState.FAILED,
                findings=("artifact_mismatch",),
            )
        return VerifierResult(state=VerificationState.PASSED)


@dataclass(frozen=True)
class RepositoryTaskVerifier:
    """Deterministic verifier for a small repository-style coding task.

    The verifier is constructed before a strategy mutates the isolated
    workspace. It records baseline digests, then independently checks required
    edits, forbidden-file invariants and a test command. No agent-provided
    status or natural-language self-report participates in the result.
    """

    test_args: tuple[str, ...] = ("-q",)
    required_changed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    baseline_digests: dict[str, str | None] | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_changed_paths",
            tuple(
                require_normalized_relative_path(path, field_name="required_changed_paths")
                for path in self.required_changed_paths
            ),
        )
        object.__setattr__(
            self,
            "forbidden_paths",
            tuple(
                require_normalized_relative_path(path, field_name="forbidden_paths")
                for path in self.forbidden_paths
            ),
        )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @classmethod
    def capture(
        cls,
        workspace: Path,
        *,
        test_args: tuple[str, ...] = ("-q",),
        required_changed_paths: tuple[str, ...] = (),
        forbidden_paths: tuple[str, ...] = (),
        timeout_seconds: float = 30.0,
    ) -> "RepositoryTaskVerifier":
        root = Path(workspace).resolve()
        paths = tuple(dict.fromkeys((*required_changed_paths, *forbidden_paths)))
        baseline: dict[str, str | None] = {}
        for relative in paths:
            normalized = require_normalized_relative_path(relative, field_name="task_paths")
            candidate = _resolve_workspace_path(root, normalized)
            if candidate is None:
                raise ValueError(f"task path escapes workspace: {normalized}")
            baseline[normalized] = _file_digest(candidate) if candidate.is_file() else None
        return cls(
            test_args=tuple(test_args),
            required_changed_paths=tuple(required_changed_paths),
            forbidden_paths=tuple(forbidden_paths),
            baseline_digests=baseline,
            timeout_seconds=timeout_seconds,
        )

    @property
    def verifier_id(self) -> str:
        return "repository_task_v1"

    async def verify(self, workspace: Path) -> VerifierResult:
        root = Path(workspace).resolve()
        baseline = dict(self.baseline_digests or {})
        findings: list[str] = []
        for relative in self.required_changed_paths:
            path = _resolve_workspace_path(root, relative)
            if path is None or not path.is_file():
                findings.append(f"required_file_missing:{relative}")
                continue
            before = baseline.get(relative)
            if before is not None and _file_digest(path) == before:
                findings.append(f"required_file_unchanged:{relative}")
        for relative in self.forbidden_paths:
            path = _resolve_workspace_path(root, relative)
            before = baseline.get(relative)
            after = _file_digest(path) if path is not None and path.is_file() else None
            if after != before:
                findings.append(f"forbidden_file_changed:{relative}")
        if findings:
            return _verifier_result(self, VerificationState.FAILED, findings=findings)

        command = (sys.executable, "-m", "pytest", *self.test_args)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("repository verifier test command timed out") from None
        except OSError:
            raise
        output = (stdout + stderr).decode("utf-8", errors="replace")
        metrics = {
            "test_exit_code": int(process.returncode or 0),
            "test_output_preview": output[:400],
        }
        if process.returncode != 0:
            return _verifier_result(self, VerificationState.FAILED, findings=("tests_failed",), metrics=metrics)
        return _verifier_result(self, VerificationState.PASSED, metrics=metrics)


def require_normalized_relative_path(
    value: str,
    *,
    field_name: str,
) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(
            f"{field_name} must be a normalized relative path",
        )
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or "\\" in value
        or path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError(
            f"{field_name} must be a normalized relative path",
        )
    return value


def _resolve_workspace_path(
    workspace: Path,
    relative: str,
) -> Path | None:
    candidate = workspace.joinpath(
        *PurePosixPath(relative).parts,
    ).resolve()
    if not candidate.is_relative_to(workspace):
        return None
    return candidate


async def run_sealed_verifier(
    verifier: Verifier,
    *,
    workspace: Path,
    seal: VerifierSeal | VerifierBundleSeal,
) -> VerifierExecution:
    verifier_id = _verifier_id(verifier)
    verifier_digest = _verifier_digest(verifier)
    if not seal.intact():
        return VerifierExecution(
            result=VerifierResult(
                state=VerificationState.NOT_RUN,
                verifier_id=verifier_id,
                verifier_digest=verifier_digest,
            ),
            infrastructure_error="verifier_digest_changed",
        )
    try:
        result = await verifier.verify(Path(workspace))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return VerifierExecution(
            result=VerifierResult(
                state=VerificationState.NOT_RUN,
                verifier_id=verifier_id,
                verifier_digest=verifier_digest,
            ),
            infrastructure_error=f"verifier_crashed:{type(exc).__name__}",
        )
    if not seal.intact():
        return VerifierExecution(
            result=VerifierResult(
                state=VerificationState.NOT_RUN,
                verifier_id=verifier_id,
                verifier_digest=verifier_digest,
            ),
            infrastructure_error="verifier_digest_changed",
        )
    if not isinstance(result, VerifierResult):
        return VerifierExecution(
            result=VerifierResult(
                state=VerificationState.NOT_RUN,
                verifier_id=verifier_id,
                verifier_digest=verifier_digest,
            ),
            infrastructure_error="verifier_invalid_result",
        )
    return VerifierExecution(
        result=replace(
            result,
            verifier_id=result.verifier_id or verifier_id,
            verifier_digest=result.verifier_digest or verifier_digest,
        )
    )


def _verifier_id(verifier: Verifier) -> str:
    declared = getattr(verifier, "verifier_id", None)
    if isinstance(declared, str) and declared:
        return declared
    verifier_type = type(verifier)
    return f"{verifier_type.__module__}.{verifier_type.__qualname__}"


def _verifier_digest(verifier: Verifier) -> str:
    try:
        source = inspect.getsource(type(verifier))
    except (OSError, TypeError):
        source = f"{type(verifier).__module__}.{type(verifier).__qualname__}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def verifier_identity(verifier: Verifier) -> dict[str, str]:
    """Return bounded identity metadata suitable for a reproducibility manifest."""
    return {
        "id": _verifier_id(verifier),
        "digest": _verifier_digest(verifier),
    }


def _verifier_result(
    verifier: Verifier,
    state: VerificationState,
    *,
    findings: tuple[str, ...] = (),
    metrics: dict[str, Any] | None = None,
) -> VerifierResult:
    return VerifierResult(
        state=state,
        findings=findings,
        metrics=metrics or {},
        verifier_id=_verifier_id(verifier),
        verifier_digest=_verifier_digest(verifier),
    )


def _file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
