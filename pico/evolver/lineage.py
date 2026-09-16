"""Durable evidence and lineage for the existing Controlled Self-Evolution Loop.

This module is deliberately a small evidence layer, not a second Evolver.  The
existing candidate, trial, gate, verifier, and activation implementations stay
authoritative.  It records the correlation between a historical Runtime run
and those existing artifacts, and freezes the authority boundary before a
candidate is generated.

The source side is intentionally bounded: only durable TraceStore identity,
selected failure attributes, and an optional Trial/Verifier failure summary are
copied.  Prompt bodies, model reasoning, sealed payloads, and benchmark
answers are not part of the candidate-generation context.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from pico.evolver.candidate_manifest import (
    ActivationPolicy,
    CandidateLabel,
    LABEL_POLICIES,
)
from pico.tracing.store import TraceStore


SCHEMA_VERSION = 1
FREEZE_FILENAME = "evolution_freeze.json"
LINEAGE_DIRNAME = "lineage"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class LineageError(ValueError):
    """A durable Trace/Evolution lineage or freeze contract is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise LineageError(f"{field_name} must be a bounded durable identifier")
    return value


def _require_sha(value: Any, field_name: str, *, length: int = 64) -> str:
    pattern = _GIT_SHA_RE if length == 40 else _SHA256_RE
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise LineageError(f"{field_name} must be a lowercase SHA-{length * 4} digest")
    return value


def _strings(value: Any, field_name: str, *, max_items: int = 256) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > max_items:
        raise LineageError(f"{field_name} must be a bounded list of strings")
    out = tuple(item for item in value if isinstance(item, str))
    if len(out) != len(value) or any(not item for item in out):
        raise LineageError(f"{field_name} must contain non-empty strings")
    if len(out) != len(set(out)):
        raise LineageError(f"{field_name} must not contain duplicates")
    return out


def _bounded_text(value: Any, *, max_chars: int = 240) -> str:
    return str(value)[:max_chars]


@dataclass(frozen=True)
class TrialFailureEvidence:
    """A bounded summary of an independently observed Trial/Verifier failure."""

    status: str
    verifier_id: str
    verifier_digest: str
    findings: tuple[str, ...] = ()
    measurement_valid: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.findings, (list, tuple)):
            raise LineageError("trial failure findings must be a bounded list")
        object.__setattr__(self, "findings", tuple(self.findings))
        if not isinstance(self.status, str) or not self.status:
            raise LineageError("trial failure status must be a non-empty string")
        if not isinstance(self.verifier_id, str) or not self.verifier_id:
            raise LineageError("trial failure verifier_id must be a non-empty string")
        _require_sha(self.verifier_digest, "trial failure verifier_digest")
        if not isinstance(self.measurement_valid, bool):
            raise LineageError("trial failure measurement_valid must be boolean")
        if len(self.findings) > 16 or any(not isinstance(item, str) for item in self.findings):
            raise LineageError("trial failure findings are not bounded")
        # A source accepted as failure evidence must actually be non-success.
        if self.status in {"passed", "accepted", "promoted_to_baseline"}:
            raise LineageError("successful Trial/Verifier output cannot be failure evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verifier_id": self.verifier_id,
            "verifier_digest": self.verifier_digest,
            "findings": list(self.findings),
            "measurement_valid": self.measurement_valid,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrialFailureEvidence":
        if set(value) != {"status", "verifier_id", "verifier_digest", "findings", "measurement_valid"}:
            raise LineageError("trial failure evidence has unexpected fields")
        if not isinstance(value["findings"], list):
            raise LineageError("trial failure findings must be a list")
        return cls(
            status=str(value["status"]),
            verifier_id=str(value["verifier_id"]),
            verifier_digest=str(value["verifier_digest"]),
            findings=tuple(str(item) for item in value["findings"]),
            measurement_valid=value["measurement_valid"],
        )


@dataclass(frozen=True)
class TraceFailureEvidence:
    """Bounded, durable source evidence consumed by a candidate generator."""

    source_run_id: str
    trace_artifact: str
    trace_sha256: str
    terminal_outcomes: tuple[str, ...]
    failure_signals: tuple[dict[str, str], ...]
    evidence_digest: str
    trial_failure: TrialFailureEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.terminal_outcomes, (list, tuple)):
            raise LineageError("trace terminal_outcomes must be a list")
        object.__setattr__(self, "terminal_outcomes", tuple(self.terminal_outcomes))
        if any(not isinstance(item, str) or not item for item in self.terminal_outcomes):
            raise LineageError("trace terminal_outcomes must contain non-empty strings")
        if not isinstance(self.failure_signals, (list, tuple)):
            raise LineageError("trace failure signals must be a list")
        if any(not isinstance(signal, Mapping) for signal in self.failure_signals):
            raise LineageError("trace failure signals must be mappings")
        object.__setattr__(self, "failure_signals", tuple(dict(signal) for signal in self.failure_signals))
        _require_id(self.source_run_id, "source_run_id")
        if not isinstance(self.trace_artifact, str) or not self.trace_artifact:
            raise LineageError("trace_artifact must be a non-empty path")
        _require_sha(self.trace_sha256, "trace_sha256")
        _require_sha(self.evidence_digest, "evidence_digest")
        if not self.terminal_outcomes:
            raise LineageError("trace evidence requires at least one terminal outcome")
        if not self.failure_signals or len(self.failure_signals) > 64:
            raise LineageError("trace evidence requires bounded failure signals")
        for signal in self.failure_signals:
            if not isinstance(signal, dict) or not signal or any(
                not isinstance(key, str) or not isinstance(value, str) for key, value in signal.items()
            ):
                raise LineageError("trace failure signals must be string mappings")
        if _sha256_bytes(_canonical_bytes(self._unsigned_dict())) != self.evidence_digest:
            raise LineageError("trace evidence digest mismatch")

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source_run_id": self.source_run_id,
            "trace_artifact": self.trace_artifact,
            "trace_sha256": self.trace_sha256,
            "terminal_outcomes": list(self.terminal_outcomes),
            "failure_signals": list(self.failure_signals),
            "trial_failure": self.trial_failure.to_dict() if self.trial_failure is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "evidence_digest": self.evidence_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TraceFailureEvidence":
        expected = {
            "schema_version",
            "source_run_id",
            "trace_artifact",
            "trace_sha256",
            "terminal_outcomes",
            "failure_signals",
            "evidence_digest",
            "trial_failure",
        }
        if set(value) != expected or value["schema_version"] != SCHEMA_VERSION:
            raise LineageError("trace failure evidence has an unsupported schema")
        signals = value["failure_signals"]
        if not isinstance(signals, list) or any(not isinstance(signal, Mapping) for signal in signals):
            raise LineageError("trace failure signals must be a list")
        parsed_trial = value["trial_failure"]
        if parsed_trial is not None and not isinstance(parsed_trial, Mapping):
            raise LineageError("trial_failure must be an object or null")
        parsed = cls(
            source_run_id=str(value["source_run_id"]),
            trace_artifact=str(value["trace_artifact"]),
            trace_sha256=str(value["trace_sha256"]),
            terminal_outcomes=_strings(value["terminal_outcomes"], "terminal_outcomes"),
            failure_signals=tuple(
                {str(key): _bounded_text(item) for key, item in signal.items()}
                for signal in signals
            ),
            evidence_digest=str(value["evidence_digest"]),
            trial_failure=TrialFailureEvidence.from_dict(parsed_trial) if parsed_trial is not None else None,
        )
        if _sha256_bytes(_canonical_bytes(parsed._unsigned_dict())) != parsed.evidence_digest:
            raise LineageError("trace evidence digest mismatch")
        return parsed


def _failure_signal(record: Mapping[str, Any]) -> dict[str, str] | None:
    attrs = record.get("attributes")
    if not isinstance(attrs, Mapping):
        attrs = {}
    allowed = (
        "spine.outcome",
        "spine.failure_category",
        "spine.error_class",
        "spine.provider_error_category",
        "terminal.state",
        "tool.error",
        "tool.failure_category",
        "recovery.decision",
    )
    selected = {
        key: _bounded_text(attrs[key])
        for key in allowed
        if key in attrs and attrs[key] not in (None, "")
    }
    outcome = str(selected.get("spine.outcome") or selected.get("terminal.state") or "").lower()
    failure_words = ("fail", "error", "cancel", "timeout", "invalid", "crash")
    if selected and (any(word in outcome for word in failure_words) or any(
        key in selected
        for key in (
            "spine.failure_category",
            "spine.error_class",
            "spine.provider_error_category",
            "tool.error",
            "tool.failure_category",
        )
    )):
        signal = {"span_id": _bounded_text(record.get("spanId") or "")}
        name = record.get("name")
        if name:
            signal["name"] = _bounded_text(name)
        signal.update(selected)
        return signal
    return None


def load_trace_failure_evidence(
    state_dir: str | os.PathLike[str],
    source_run_id: str,
    *,
    trial_failure: TrialFailureEvidence | None = None,
) -> TraceFailureEvidence:
    """Load a real durable TraceStore run and extract bounded failure evidence.

    The function refuses an absent/empty trace and refuses to manufacture a
    failure from a caller-provided label.  ``trial_failure`` is optional so a
    Runtime-only failure can be consumed, but when supplied it is validated and
    preserved as the independent PicoBench/Verifier observation.
    """

    _require_id(source_run_id, "source_run_id")
    store = TraceStore(state_dir)
    trace_path = store.run_path(source_run_id)
    if trace_path.is_symlink() or not trace_path.is_file():
        raise LineageError(f"durable TraceStore run is missing or unsafe: {trace_path}")
    records = store.read_trace(source_run_id)
    if not records:
        raise LineageError(f"durable TraceStore run {source_run_id!r} is empty")
    signals = tuple(signal for record in records if (signal := _failure_signal(record)) is not None)
    if not signals:
        raise LineageError(f"TraceStore run {source_run_id!r} contains no failure/weakness signal")
    summary = store.trace_summary(source_run_id)
    terminal = tuple(
        item for item in summary.get("terminal_outcomes", []) if isinstance(item, str) and item
    )
    if not terminal:
        terminal = tuple(signal.get("spine.outcome", "failure_evidence") for signal in signals)
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "trace_artifact": str(trace_path),
        "trace_sha256": _sha256_bytes(trace_path.read_bytes()),
        "terminal_outcomes": list(dict.fromkeys(terminal)),
        "failure_signals": list(signals),
        "trial_failure": trial_failure.to_dict() if trial_failure is not None else None,
    }
    return TraceFailureEvidence(
        source_run_id=source_run_id,
        trace_artifact=str(trace_path),
        trace_sha256=unsigned["trace_sha256"],
        terminal_outcomes=tuple(unsigned["terminal_outcomes"]),
        failure_signals=signals,
        evidence_digest=_sha256_bytes(_canonical_bytes(unsigned)),
        trial_failure=trial_failure,
    )


@dataclass(frozen=True)
class EvolutionRunFreeze:
    """Immutable authority boundary captured before candidate generation."""

    evolution_run_id: str
    baseline_sha: str
    candidate_label: CandidateLabel
    mutable_paths: tuple[str, ...]
    fixture: str
    evaluator: str
    train_task_ids: tuple[str, ...]
    sealed_task_ids: tuple[str, ...]
    verifier_id: str
    verifier_digest: str
    claim_gate_id: str
    activation_policy: ActivationPolicy
    source: TraceFailureEvidence

    def __post_init__(self) -> None:
        _require_id(self.evolution_run_id, "evolution_run_id")
        _require_sha(self.baseline_sha, "baseline_sha", length=40)
        policy = LABEL_POLICIES[self.candidate_label]
        if not policy.supported:
            raise LineageError(f"unsupported candidate label cannot be frozen: {self.candidate_label.value}")
        if self.mutable_paths != policy.mutable_paths:
            raise LineageError("frozen mutable_paths differ from the canonical label allowlist")
        if self.fixture != policy.fixture or self.evaluator != policy.evaluator:
            raise LineageError("frozen fixture/evaluator differ from the canonical label policy")
        if self.activation_policy is not policy.activation_policy:
            raise LineageError("frozen activation policy differs from the canonical label policy")
        if not self.train_task_ids or len(self.train_task_ids) != len(set(self.train_task_ids)):
            raise LineageError("frozen train task pack must be non-empty and unique")
        if len(self.sealed_task_ids) != len(set(self.sealed_task_ids)):
            raise LineageError("frozen sealed task pack must be unique")
        if set(self.train_task_ids) & set(self.sealed_task_ids):
            raise LineageError("train and sealed task packs must be disjoint")
        if not self.verifier_id or not isinstance(self.verifier_id, str):
            raise LineageError("frozen verifier identity is missing")
        _require_sha(self.verifier_digest, "verifier_digest")
        if not self.claim_gate_id:
            raise LineageError("frozen claim gate identity is missing")
        if self.source.source_run_id == self.evolution_run_id:
            raise LineageError("source Run ID and Evolution Run ID must remain distinguishable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "evolution_run_id": self.evolution_run_id,
            "baseline_sha": self.baseline_sha,
            "candidate_label": self.candidate_label.value,
            "mutable_paths": list(self.mutable_paths),
            "fixture": self.fixture,
            "evaluator": self.evaluator,
            "train_task_ids": list(self.train_task_ids),
            "sealed_task_ids": list(self.sealed_task_ids),
            "verifier_id": self.verifier_id,
            "verifier_digest": self.verifier_digest,
            "claim_gate_id": self.claim_gate_id,
            "activation_policy": self.activation_policy.value,
            "source": self.source.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvolutionRunFreeze":
        expected = {
            "schema_version",
            "evolution_run_id",
            "baseline_sha",
            "candidate_label",
            "mutable_paths",
            "fixture",
            "evaluator",
            "train_task_ids",
            "sealed_task_ids",
            "verifier_id",
            "verifier_digest",
            "claim_gate_id",
            "activation_policy",
            "source",
        }
        if set(value) != expected or value["schema_version"] != SCHEMA_VERSION:
            raise LineageError("evolution freeze has an unsupported schema")
        source = value["source"]
        if not isinstance(source, Mapping):
            raise LineageError("evolution freeze source must be an object")
        return cls(
            evolution_run_id=str(value["evolution_run_id"]),
            baseline_sha=str(value["baseline_sha"]),
            candidate_label=CandidateLabel(value["candidate_label"]),
            mutable_paths=_strings(value["mutable_paths"], "mutable_paths"),
            fixture=str(value["fixture"]),
            evaluator=str(value["evaluator"]),
            train_task_ids=_strings(value["train_task_ids"], "train_task_ids"),
            sealed_task_ids=_strings(value["sealed_task_ids"], "sealed_task_ids"),
            verifier_id=str(value["verifier_id"]),
            verifier_digest=str(value["verifier_digest"]),
            claim_gate_id=str(value["claim_gate_id"]),
            activation_policy=ActivationPolicy(value["activation_policy"]),
            source=TraceFailureEvidence.from_dict(source),
        )


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def freeze_evolution_run(
    work_dir: str | os.PathLike[str],
    *,
    evolution_run_id: str,
    baseline_sha: str,
    source: TraceFailureEvidence,
    train_task_ids: list[str] | tuple[str, ...],
    sealed_task_ids: list[str] | tuple[str, ...],
    candidate_label: CandidateLabel = CandidateLabel.runtime,
    verifier_id: str = "verifier-boundary-deferred",
    verifier_digest: str = "0" * 64,
    claim_gate_id: str = "phase6-layered-claim-gate-v1",
) -> EvolutionRunFreeze:
    """Create or verify the one-way freeze record for an Evolution Run."""

    policy = LABEL_POLICIES[candidate_label]
    freeze = EvolutionRunFreeze(
        evolution_run_id=evolution_run_id,
        baseline_sha=baseline_sha,
        candidate_label=candidate_label,
        mutable_paths=policy.mutable_paths,
        fixture=str(policy.fixture),
        evaluator=str(policy.evaluator),
        train_task_ids=tuple(train_task_ids),
        sealed_task_ids=tuple(sealed_task_ids),
        verifier_id=verifier_id,
        verifier_digest=verifier_digest,
        claim_gate_id=claim_gate_id,
        activation_policy=policy.activation_policy,
        source=source,
    )
    path = Path(work_dir) / FREEZE_FILENAME
    if path.is_symlink():
        raise LineageError(f"evolution freeze path must not be a symlink: {path}")
    if path.is_file():
        try:
            current = EvolutionRunFreeze.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise LineageError(f"existing evolution freeze is unreadable: {path}") from exc
        if current.to_dict() != freeze.to_dict():
            raise LineageError("Evolution Run freeze is immutable and differs from the requested boundary")
        return current
    _atomic_json_write(path, freeze.to_dict())
    return freeze


@dataclass(frozen=True)
class CandidateGenerationContext:
    """The only source context exposed to a deterministic/model generator.

    Notice the absence of sealed task IDs, expected answers, sealed results,
    and verifier implementation bytes.  The frozen task pack remains durable
    in :class:`EvolutionRunFreeze`, while this public generation view contains
    only the train-side authority needed to propose a bounded patch.
    """

    evolution_run_id: str
    baseline_sha: str
    candidate_label: CandidateLabel
    mutable_paths: tuple[str, ...]
    fixture: str
    evaluator: str
    activation_policy: ActivationPolicy
    train_task_ids: tuple[str, ...]
    source: TraceFailureEvidence

    @classmethod
    def from_freeze(cls, freeze: EvolutionRunFreeze) -> "CandidateGenerationContext":
        return cls(
            evolution_run_id=freeze.evolution_run_id,
            baseline_sha=freeze.baseline_sha,
            candidate_label=freeze.candidate_label,
            mutable_paths=freeze.mutable_paths,
            fixture=freeze.fixture,
            evaluator=freeze.evaluator,
            activation_policy=freeze.activation_policy,
            train_task_ids=freeze.train_task_ids,
            source=freeze.source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evolution_run_id": self.evolution_run_id,
            "baseline_sha": self.baseline_sha,
            "candidate_label": self.candidate_label.value,
            "mutable_paths": list(self.mutable_paths),
            "fixture": self.fixture,
            "evaluator": self.evaluator,
            "activation_policy": self.activation_policy.value,
            "train_task_ids": list(self.train_task_ids),
            "source_run_id": self.source.source_run_id,
            "source_trace_sha256": self.source.trace_sha256,
            "source_evidence_digest": self.source.evidence_digest,
        }


def bind_candidate_to_trace(candidate: Any, context: CandidateGenerationContext) -> Any:
    """Bind a generated Candidate to the frozen source before it is applied.

    Existing bench candidates are intentionally duck-typed.  The binding is
    explicit and checked for conflicting pre-existing values, so a generator
    cannot silently relabel a candidate as evidence-backed after evaluation.
    """

    values = {
        "evolution_run_id": context.evolution_run_id,
        "source_run_id": context.source.source_run_id,
        "source_trace_ref": context.source.trace_artifact,
        "source_evidence_digest": context.source.evidence_digest,
        "source_finding": context.source.failure_signals[0].get("spine.outcome", "bounded trace failure"),
    }
    for name, expected in values.items():
        existing = getattr(candidate, name, "")
        if existing not in (None, "", expected):
            raise LineageError(f"candidate {name} conflicts with the frozen source lineage")
        setattr(candidate, name, expected)
    return candidate


@dataclass(frozen=True)
class CandidateLineage:
    """Per-candidate durable correlation record beside the strict activation bundle."""

    evolution_run_id: str
    source_run_id: str
    source_trace_artifact: str
    source_evidence_digest: str
    baseline_sha: str
    parent_node_id: str
    candidate_id: str
    label: CandidateLabel
    patch_digest: str
    fixture: str
    evaluator: str
    activation_policy: ActivationPolicy
    train: dict[str, Any]
    sealed: dict[str, Any] | None
    verifier: dict[str, str]
    claim_state: str
    activation_state: str

    def __post_init__(self) -> None:
        _require_id(self.evolution_run_id, "evolution_run_id")
        _require_id(self.source_run_id, "source_run_id")
        _require_id(self.parent_node_id, "parent_node_id")
        _require_id(self.candidate_id, "candidate_id")
        _require_sha(self.source_evidence_digest, "source_evidence_digest")
        _require_sha(self.baseline_sha, "baseline_sha", length=40)
        _require_sha(self.patch_digest, "patch_digest")
        if not self.source_trace_artifact or not self.fixture or not self.evaluator:
            raise LineageError("candidate lineage is missing source/fixture/evaluator identity")
        if not isinstance(self.train, dict) or (self.sealed is not None and not isinstance(self.sealed, dict)):
            raise LineageError("candidate lineage evaluation payloads must be objects")
        if not isinstance(self.verifier, dict) or set(self.verifier) != {"id", "digest"}:
            raise LineageError("candidate lineage verifier identity must contain id and digest")
        if not self.verifier["id"]:
            raise LineageError("candidate lineage verifier id is empty")
        _require_sha(self.verifier["digest"], "candidate lineage verifier digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "evolution_run_id": self.evolution_run_id,
            "source_run_id": self.source_run_id,
            "source_trace_artifact": self.source_trace_artifact,
            "source_evidence_digest": self.source_evidence_digest,
            "baseline_sha": self.baseline_sha,
            "parent_node_id": self.parent_node_id,
            "candidate_id": self.candidate_id,
            "label": self.label.value,
            "patch_digest": self.patch_digest,
            "fixture": self.fixture,
            "evaluator": self.evaluator,
            "activation_policy": self.activation_policy.value,
            "train": self.train,
            "sealed": self.sealed,
            "verifier": self.verifier,
            "claim_state": self.claim_state,
            "activation_state": self.activation_state,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateLineage":
        expected = {
            "schema_version",
            "evolution_run_id",
            "source_run_id",
            "source_trace_artifact",
            "source_evidence_digest",
            "baseline_sha",
            "parent_node_id",
            "candidate_id",
            "label",
            "patch_digest",
            "fixture",
            "evaluator",
            "activation_policy",
            "train",
            "sealed",
            "verifier",
            "claim_state",
            "activation_state",
        }
        if set(value) != expected or value["schema_version"] != SCHEMA_VERSION:
            raise LineageError("candidate lineage has an unsupported schema")
        verifier = value["verifier"]
        if not isinstance(verifier, Mapping):
            raise LineageError("candidate lineage verifier must be an object")
        train = value["train"]
        sealed = value["sealed"]
        if not isinstance(train, Mapping) or (sealed is not None and not isinstance(sealed, Mapping)):
            raise LineageError("candidate lineage evaluation payloads must be objects")
        parsed = cls(
            evolution_run_id=str(value["evolution_run_id"]),
            source_run_id=str(value["source_run_id"]),
            source_trace_artifact=str(value["source_trace_artifact"]),
            source_evidence_digest=str(value["source_evidence_digest"]),
            baseline_sha=str(value["baseline_sha"]),
            parent_node_id=str(value["parent_node_id"]),
            candidate_id=str(value["candidate_id"]),
            label=CandidateLabel(value["label"]),
            patch_digest=str(value["patch_digest"]),
            fixture=str(value["fixture"]),
            evaluator=str(value["evaluator"]),
            activation_policy=ActivationPolicy(value["activation_policy"]),
            train=dict(train),
            sealed=dict(sealed) if sealed is not None else None,
            verifier={"id": str(verifier.get("id")), "digest": str(verifier.get("digest"))},
            claim_state=str(value["claim_state"]),
            activation_state=str(value["activation_state"]),
        )
        if value != parsed.to_dict():
            raise LineageError("candidate lineage is not canonical")
        return parsed


def _lineage_path(work_dir: str | os.PathLike[str], candidate_id: str) -> Path:
    _require_id(candidate_id, "candidate_id")
    root = Path(work_dir) / LINEAGE_DIRNAME
    if root.is_symlink():
        raise LineageError(f"lineage directory must not be a symlink: {root}")
    path = root / f"{candidate_id}.json"
    if path.is_symlink():
        raise LineageError(f"candidate lineage path must not be a symlink: {path}")
    return path


def write_candidate_lineage(work_dir: str | os.PathLike[str], lineage: CandidateLineage) -> Path:
    """Write an immutable candidate identity, idempotently."""

    path = _lineage_path(work_dir, lineage.candidate_id)
    if path.is_file():
        current = load_candidate_lineage(work_dir, lineage.candidate_id)
        if current.to_dict() != lineage.to_dict():
            raise LineageError("candidate lineage identity already exists with different content")
        return path
    _atomic_json_write(path, lineage.to_dict())
    return path


def load_candidate_lineage(work_dir: str | os.PathLike[str], candidate_id: str) -> CandidateLineage:
    path = _lineage_path(work_dir, candidate_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LineageError(f"candidate lineage is unreadable: {path}") from exc
    if not isinstance(value, Mapping):
        raise LineageError("candidate lineage root must be an object")
    lineage = CandidateLineage.from_dict(value)
    if lineage.candidate_id != candidate_id:
        raise LineageError("candidate lineage filename does not match candidate_id")
    return lineage


def update_candidate_lineage(
    work_dir: str | os.PathLike[str],
    candidate_id: str,
    *,
    sealed: dict[str, Any] | None = None,
    activation_state: str | None = None,
) -> Path:
    """Update lifecycle evidence without changing candidate identity fields."""

    current = load_candidate_lineage(work_dir, candidate_id)
    updated = replace(
        current,
        sealed=current.sealed if sealed is None else dict(sealed),
        activation_state=current.activation_state if activation_state is None else str(activation_state),
    )
    path = _lineage_path(work_dir, candidate_id)
    _atomic_json_write(path, updated.to_dict())
    return path


def finalize_candidate_lineages(
    work_dir: str | os.PathLike[str],
    sealed_dir: str | os.PathLike[str],
) -> int:
    """Attach bounded sealed outcomes after the existing runner unseals.

    The sealed runner remains the owner of test execution and selection.  This
    helper only indexes its already durable JSON records into sidecars; it
    never reads a sealed result during candidate generation or train gating.
    Candidates not selected for post-hoc scoring are explicitly marked
    ``not_selected`` rather than being treated as positive evidence.
    """

    root = Path(work_dir) / LINEAGE_DIRNAME
    if root.is_symlink() or not root.is_dir():
        return 0
    sealed_root = Path(sealed_dir)
    changed = 0
    for path in sorted(root.glob("*.json")):
        candidate_id = path.stem
        current = load_candidate_lineage(work_dir, candidate_id)
        matches = sorted(sealed_root.glob(f"*_{candidate_id}.json")) if sealed_root.is_dir() else []
        if matches:
            try:
                record = json.loads(matches[-1].read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise LineageError(f"sealed record is unreadable: {matches[-1]}") from exc
            if not isinstance(record, Mapping) or record.get("node_id") != candidate_id:
                raise LineageError(f"sealed record identity mismatch for candidate {candidate_id!r}")
            measurement = record.get("measurement")
            sealed = {
                "status": "measured" if isinstance(measurement, Mapping) else "invalid",
                "result_available": isinstance(measurement, Mapping),
                "round": record.get("round"),
                "node_id": candidate_id,
                "pass_at_1": record.get("pass_at_1"),
                "measurement": dict(measurement) if isinstance(measurement, Mapping) else None,
                "task_count": len(record.get("per_task", {})) if isinstance(record.get("per_task"), Mapping) else 0,
            }
        else:
            sealed = {
                "status": "not_selected",
                "result_available": False,
                "node_id": candidate_id,
            }
        if current.sealed != sealed:
            update_candidate_lineage(work_dir, candidate_id, sealed=sealed)
            changed += 1
    return changed


def claim_state_for_outcome(verdict: Any) -> str:
    """Map existing Evolver verdict vocabulary to Phase 6 Claim vocabulary."""

    value = getattr(verdict, "value", verdict)
    return {
        "accepted": "SUPPORTED",
        "rejected": "REJECTED",
        "inconclusive": "INCONCLUSIVE",
        "failed": "INVALID_MEASUREMENT",
    }.get(str(value), "INCONCLUSIVE")


__all__ = [
    "CandidateGenerationContext",
    "CandidateLineage",
    "EvolutionRunFreeze",
    "FREEZE_FILENAME",
    "LINEAGE_DIRNAME",
    "LineageError",
    "SCHEMA_VERSION",
    "TraceFailureEvidence",
    "TrialFailureEvidence",
    "bind_candidate_to_trace",
    "claim_state_for_outcome",
    "freeze_evolution_run",
    "finalize_candidate_lineages",
    "load_candidate_lineage",
    "load_trace_failure_evidence",
    "update_candidate_lineage",
    "write_candidate_lineage",
]
