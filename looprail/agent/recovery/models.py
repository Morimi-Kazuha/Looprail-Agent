"""Immutable facts used by the Phase 0B.1 recovery reasoning layer.

This module intentionally contains data models only.  The models describe what a
recovery scan can prove; they do not execute tools, write sessions, or change the
workspace.  ``EffectRecord`` remains the source of execution facts while the
observation and plan models keep the semantic-consumption axis separate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from looprail.agent.effects import EffectClass, EffectRecord, EffectStatus, canonical_digest, utc_now


class ObservationState(StrEnum):
    """Durable state of an effect's result observation, independent of execution."""

    MISSING = "missing"
    AVAILABLE = "available"
    CONSUMED = "consumed"
    UNAVAILABLE = "unavailable"


class ExecutionAction(StrEnum):
    """What the later recovery executor is allowed to do with an effect."""

    NO_REPLAY = "no_replay"
    RETRY_ALLOWED = "retry_allowed"
    ASK_HUMAN = "ask_human"


class ObservationAction(StrEnum):
    """What the runtime should do with an already available observation."""

    NONE = "none"
    RESUME_WITH_OBSERVATION = "resume_with_observation"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"


def _enum_value(value: Any, enum_type: type[StrEnum], default: StrEnum) -> StrEnum:
    if value is None:
        return default
    if isinstance(value, enum_type):
        return value
    raw = str(value).strip().lower()
    try:
        return enum_type(raw)
    except ValueError:
        try:
            return enum_type[raw.upper()]
        except KeyError as exc:
            raise ValueError(f"unknown {enum_type.__name__}: {value!r}") from exc


def _copy_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if value is not None else {}


def _identity_digest(value: Any) -> str:
    """Use the Phase 0A canonical SHA-256 helper for all recovery identities."""

    return canonical_digest(value)


def _candidate_identity(candidate: RecoveryCandidate) -> dict[str, Any]:
    """Return timestamp-free, payload-free candidate identity material."""

    return {
        "effect_id": candidate.effect_id,
        "logical_effect_id": candidate.logical_effect_id,
        "attempt": candidate.attempt,
        "turn_id": candidate.turn_id,
        "session_key": candidate.session_key,
        "trace_id": candidate.trace_id,
        "tool_call_id": candidate.tool_call_id,
        "tool_name": candidate.tool_name,
        "effect_class": candidate.effect_class.value,
        "effect_status": candidate.effect_status.value,
        "arguments_digest": candidate.arguments_digest,
        "precondition": candidate.precondition,
        "postcondition": candidate.postcondition,
        "result_ref": candidate.result_ref,
        "receipt_ref": candidate.receipt_ref,
        "checkpoint_id": candidate.checkpoint_id,
        "observation_state": candidate.observation_state.value,
        "observation_id": candidate.observation_id,
        "evidence_digest": candidate.evidence_digest,
        "unresolved_reason": candidate.unresolved_reason,
    }


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    """A durable effect situation that requires recovery reasoning.

    The candidate is a compatibility projection of the latest ``EffectRecord``.
    It deliberately contains no complete tool arguments, ToolResult, Session, or
    journal history.  ``candidate_id`` is derived from semantic state and is stable
    across restarts when that state is unchanged.
    """

    effect_id: str
    logical_effect_id: str | None = None
    attempt: int = 1
    turn_id: str | None = None
    session_key: str | None = None
    trace_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str = ""
    effect_class: EffectClass = EffectClass.UNKNOWN
    effect_status: EffectStatus = EffectStatus.PREPARED
    arguments_digest: str | None = None
    precondition: Mapping[str, Any] = field(default_factory=dict)
    postcondition: Mapping[str, Any] = field(default_factory=dict)
    result_ref: str | None = None
    receipt_ref: str | None = None
    checkpoint_id: str | None = None
    observation_state: ObservationState = ObservationState.MISSING
    unresolved_reason: str = ""
    prepared_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    observation_id: str | None = None
    evidence_digest: str | None = None
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        if not self.effect_id:
            raise ValueError("effect_id must not be empty")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        object.__setattr__(self, "logical_effect_id", self.logical_effect_id or self.effect_id)
        object.__setattr__(self, "effect_class", _enum_value(self.effect_class, EffectClass, EffectClass.UNKNOWN))
        object.__setattr__(self, "effect_status", _enum_value(self.effect_status, EffectStatus, EffectStatus.PREPARED))
        object.__setattr__(
            self, "observation_state", _enum_value(self.observation_state, ObservationState, ObservationState.MISSING)
        )
        object.__setattr__(self, "precondition", _copy_mapping(self.precondition))
        object.__setattr__(self, "postcondition", _copy_mapping(self.postcondition))
        if self.evidence_digest is None:
            object.__setattr__(
                self,
                "evidence_digest",
                _identity_digest(
                    {
                        "observation_state": self.observation_state.value,
                        "observation_id": self.observation_id,
                        "result_ref": self.result_ref,
                        "receipt_ref": self.receipt_ref,
                        "precondition": self.precondition,
                        "postcondition": self.postcondition,
                    }
                ),
            )
        if self.candidate_id is None:
            object.__setattr__(self, "candidate_id", f"candidate-{_identity_digest(_candidate_identity(self))}")

    @property
    def candidate_fingerprint(self) -> str:
        """Timestamp-free fingerprint used to detect a stale recovery plan."""

        return _identity_digest(_candidate_identity(self))

    @classmethod
    def from_effect(
        cls,
        record: EffectRecord,
        *,
        observation_state: ObservationState = ObservationState.MISSING,
        unresolved_reason: str = "",
        observation_id: str | None = None,
        evidence_digest: str | None = None,
    ) -> RecoveryCandidate:
        """Project an old or new ``EffectRecord`` without rewriting its history."""

        return cls(
            effect_id=record.effect_id,
            logical_effect_id=getattr(record, "logical_effect_id", None) or record.effect_id,
            attempt=record.attempt,
            turn_id=record.turn_id,
            session_key=record.session_key,
            trace_id=record.trace_id,
            tool_call_id=record.tool_call_id,
            tool_name=record.tool_name,
            effect_class=record.effect_class,
            effect_status=record.status,
            arguments_digest=record.arguments_digest,
            precondition=record.precondition or {},
            postcondition=record.postcondition or {},
            result_ref=record.result_ref,
            receipt_ref=record.receipt_ref,
            checkpoint_id=record.checkpoint_id,
            observation_state=observation_state,
            unresolved_reason=unresolved_reason,
            prepared_at=record.prepared_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            observation_id=observation_id,
            evidence_digest=evidence_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_fingerprint": self.candidate_fingerprint,
            "effect_id": self.effect_id,
            "logical_effect_id": self.logical_effect_id,
            "attempt": self.attempt,
            "turn_id": self.turn_id,
            "session_key": self.session_key,
            "trace_id": self.trace_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "effect_class": self.effect_class.value,
            "effect_status": self.effect_status.value,
            "arguments_digest": self.arguments_digest,
            "precondition": dict(self.precondition),
            "postcondition": dict(self.postcondition),
            "result_ref": self.result_ref,
            "receipt_ref": self.receipt_ref,
            "checkpoint_id": self.checkpoint_id,
            "observation_state": self.observation_state.value,
            "unresolved_reason": self.unresolved_reason,
            "prepared_at": self.prepared_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "observation_id": self.observation_id,
            "evidence_digest": self.evidence_digest,
        }


def _observation_identity(observation: RecoveryObservation) -> dict[str, Any]:
    """Return observation facts without the volatile observation timestamp."""

    return {
        "effect_id": observation.effect_id,
        "effect_class": observation.effect_class.value,
        "kind": observation.kind,
        "exists": observation.exists,
        "current_hash": observation.current_hash,
        "matches_precondition": observation.matches_precondition,
        "matches_postcondition": observation.matches_postcondition,
        "result_ref": observation.result_ref,
        "receipt_ref": observation.receipt_ref,
        "certainty": observation.certainty,
        "observation_state": observation.observation_state.value,
        "evidence": observation.evidence,
    }


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    """Current, read-only evidence used for one recovery decision."""

    effect_id: str
    effect_class: EffectClass = EffectClass.UNKNOWN
    kind: str = "unknown"
    exists: bool | None = None
    current_hash: str | None = None
    matches_precondition: bool | None = None
    matches_postcondition: bool | None = None
    result_ref: str | None = None
    receipt_ref: str | None = None
    certainty: str = "unknown"
    observation_state: ObservationState = ObservationState.MISSING
    evidence: Mapping[str, Any] = field(default_factory=dict)
    observed_at: str | None = None
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.effect_id:
            raise ValueError("effect_id must not be empty")
        object.__setattr__(self, "effect_class", _enum_value(self.effect_class, EffectClass, EffectClass.UNKNOWN))
        object.__setattr__(
            self, "observation_state", _enum_value(self.observation_state, ObservationState, ObservationState.MISSING)
        )
        object.__setattr__(self, "evidence", _copy_mapping(self.evidence))
        if self.observation_id is None:
            object.__setattr__(self, "observation_id", f"observation-{_identity_digest(_observation_identity(self))}")

    @property
    def fingerprint(self) -> str:
        return _identity_digest(_observation_identity(self))

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "effect_id": self.effect_id,
            "effect_class": self.effect_class.value,
            "kind": self.kind,
            "exists": self.exists,
            "current_hash": self.current_hash,
            "matches_precondition": self.matches_precondition,
            "matches_postcondition": self.matches_postcondition,
            "result_ref": self.result_ref,
            "receipt_ref": self.receipt_ref,
            "certainty": self.certainty,
            "observation_state": self.observation_state.value,
            "evidence": dict(self.evidence),
            "observed_at": self.observed_at,
        }


def _plan_identity(plan: RecoveryPlan) -> dict[str, Any]:
    """Return timestamp-free plan identity material."""

    return {
        "plan_version": plan.plan_version,
        "candidate_id": plan.candidate_id,
        "candidate_fingerprint": plan.candidate_fingerprint,
        "turn_id": plan.turn_id,
        "session_key": plan.session_key,
        "effect_id": plan.effect_id,
        "logical_effect_id": plan.logical_effect_id,
        "decision": plan.decision.value,
        "execution_action": plan.execution_action.value,
        "observation_action": plan.observation_action.value,
        "reason": plan.reason,
        "evidence": plan.evidence,
        "observed_state": plan.observed_state,
        "required_action": plan.required_action,
        "tool_name": plan.tool_name,
        "effect_class": plan.effect_class.value,
        "result_ref": plan.result_ref,
        "observation_id": plan.observation_id,
        "requires_human": plan.requires_human,
    }


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """A deterministic, non-executing recovery decision."""

    plan_version: str = "looprail.recovery.v1"
    candidate_id: str = ""
    candidate_fingerprint: str = ""
    turn_id: str | None = None
    session_key: str | None = None
    effect_id: str = ""
    logical_effect_id: str = ""
    decision: ExecutionAction | str | None = None
    reason: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    observed_state: Mapping[str, Any] = field(default_factory=dict)
    required_action: str = ""
    tool_name: str = ""
    effect_class: EffectClass = EffectClass.UNKNOWN
    result_ref: str | None = None
    observation_id: str | None = None
    requires_human: bool = False
    observation_action: ObservationAction | str = ObservationAction.NONE
    execution_action: ExecutionAction | str | None = None
    created_at: str | None = None
    recovery_id: str | None = None
    plan_hash: str | None = None

    def __post_init__(self) -> None:
        execution = self.execution_action if self.execution_action is not None else self.decision
        execution_value = _enum_value(execution, ExecutionAction, ExecutionAction.ASK_HUMAN)
        object.__setattr__(self, "decision", execution_value)
        object.__setattr__(self, "execution_action", execution_value)
        object.__setattr__(
            self, "observation_action", _enum_value(self.observation_action, ObservationAction, ObservationAction.NONE)
        )
        object.__setattr__(self, "effect_class", _enum_value(self.effect_class, EffectClass, EffectClass.UNKNOWN))
        object.__setattr__(self, "evidence", _copy_mapping(self.evidence))
        object.__setattr__(self, "observed_state", _copy_mapping(self.observed_state))
        if not self.required_action:
            if self.observation_action is not ObservationAction.NONE:
                required = self.observation_action.value
            elif execution_value is ExecutionAction.RETRY_ALLOWED:
                required = "retry_explicitly"
            elif execution_value is ExecutionAction.ASK_HUMAN:
                required = "ask_human"
            else:
                required = "no_replay"
            object.__setattr__(self, "required_action", required)
        identity = _plan_identity(self)
        plan_hash = self.plan_hash or _identity_digest(identity)
        object.__setattr__(self, "plan_hash", plan_hash)
        if self.recovery_id is None:
            object.__setattr__(self, "recovery_id", f"recovery-{plan_hash}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "plan_hash": self.plan_hash,
            "plan_version": self.plan_version,
            "candidate_id": self.candidate_id,
            "candidate_fingerprint": self.candidate_fingerprint,
            "turn_id": self.turn_id,
            "session_key": self.session_key,
            "effect_id": self.effect_id,
            "logical_effect_id": self.logical_effect_id,
            "decision": self.decision.value,
            "execution_action": self.execution_action.value,
            "observation_action": self.observation_action.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "observed_state": dict(self.observed_state),
            "required_action": self.required_action,
            "tool_name": self.tool_name,
            "effect_class": self.effect_class.value,
            "result_ref": self.result_ref,
            "observation_id": self.observation_id,
            "requires_human": self.requires_human,
            "created_at": self.created_at,
        }


def observation_now() -> str:
    """Keep timestamp creation explicit so identities never depend on wall time."""

    return utc_now()


__all__ = [
    "ExecutionAction",
    "ObservationAction",
    "ObservationState",
    "RecoveryCandidate",
    "RecoveryObservation",
    "RecoveryPlan",
]
