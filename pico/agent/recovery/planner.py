"""Pure Phase 0B.1 recovery-plan construction.

The Phase 0A planner remains the low-level ``EffectRecord`` helper in
``pico.agent.effects``.  This adapter adds the observation axis and emits a
deterministic ``RecoveryPlan``; it never executes or retries anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pico.agent.effects import EffectClass, EffectRecord, EffectStatus, utc_now
from pico.agent.recovery.models import (
    ExecutionAction,
    ObservationAction,
    ObservationState,
    RecoveryCandidate,
    RecoveryObservation,
    RecoveryPlan,
)


class RecoveryPlanner:
    """Build a pure, serializable recovery plan from facts and observations."""

    PLAN_VERSION = "pico.recovery.v1"

    @classmethod
    def plan(
        cls,
        candidate: RecoveryCandidate | EffectRecord,
        observation: RecoveryObservation | Mapping[str, Any] | None = None,
    ) -> RecoveryPlan:
        candidate = cls._as_candidate(candidate)
        observation = cls._as_observation(candidate, observation)
        execution_action, observation_action, reason, requires_human = cls._decide(candidate, observation)
        evidence = cls._evidence(candidate, observation)
        observed_state = {
            "observation_state": observation.observation_state.value,
            "kind": observation.kind,
            "certainty": observation.certainty,
            "matches_precondition": observation.matches_precondition,
            "matches_postcondition": observation.matches_postcondition,
            "current_exists": observation.exists,
            "current_hash": observation.current_hash,
        }
        return RecoveryPlan(
            plan_version=cls.PLAN_VERSION,
            candidate_id=str(candidate.candidate_id),
            candidate_fingerprint=candidate.candidate_fingerprint,
            turn_id=candidate.turn_id,
            session_key=candidate.session_key,
            effect_id=candidate.effect_id,
            logical_effect_id=str(candidate.logical_effect_id),
            decision=execution_action,
            execution_action=execution_action,
            observation_action=observation_action,
            reason=reason,
            evidence=evidence,
            observed_state=observed_state,
            tool_name=candidate.tool_name,
            effect_class=candidate.effect_class,
            result_ref=candidate.result_ref or observation.result_ref,
            observation_id=observation.observation_id,
            requires_human=requires_human,
            created_at=utc_now(),
        )

    @staticmethod
    def _as_candidate(value: RecoveryCandidate | EffectRecord) -> RecoveryCandidate:
        if isinstance(value, RecoveryCandidate):
            return value
        if not isinstance(value, EffectRecord):
            raise TypeError("RecoveryPlanner expects RecoveryCandidate or EffectRecord")
        if value.result_ref or value.receipt_ref:
            state = ObservationState.AVAILABLE
        elif value.status in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            state = ObservationState.UNAVAILABLE
        else:
            state = ObservationState.MISSING
        return RecoveryCandidate.from_effect(value, observation_state=state)

    @staticmethod
    def _as_observation(
        candidate: RecoveryCandidate,
        value: RecoveryObservation | Mapping[str, Any] | None,
    ) -> RecoveryObservation:
        if value is None:
            return RecoveryObservation(
                effect_id=candidate.effect_id,
                effect_class=candidate.effect_class,
                result_ref=candidate.result_ref,
                receipt_ref=candidate.receipt_ref,
                observation_state=candidate.observation_state,
            )
        if isinstance(value, RecoveryObservation):
            observation = value
        elif isinstance(value, Mapping):
            observation = RecoveryObservation(
                effect_id=str(value.get("effect_id", candidate.effect_id)),
                effect_class=value.get("effect_class", candidate.effect_class),
                kind=str(value.get("kind", "unknown")),
                exists=value.get("exists", value.get("current_exists")),
                current_hash=value.get("current_hash"),
                matches_precondition=value.get("matches_precondition"),
                matches_postcondition=value.get("matches_postcondition"),
                result_ref=value.get("result_ref"),
                receipt_ref=value.get("receipt_ref"),
                certainty=str(value.get("certainty", "unknown")),
                observation_state=value.get("observation_state", value.get("state", ObservationState.MISSING)),
                evidence=value.get("evidence", {}),
                observed_at=value.get("observed_at"),
                observation_id=value.get("observation_id"),
            )
        else:
            raise TypeError("RecoveryPlanner observation must be RecoveryObservation or mapping")
        if observation.effect_id != candidate.effect_id:
            raise ValueError("recovery observation effect_id does not match candidate")
        return observation

    @staticmethod
    def _has_available_observation(observation: RecoveryObservation) -> bool:
        return observation.observation_state in {ObservationState.AVAILABLE, ObservationState.CONSUMED}

    @classmethod
    def _decide(
        cls,
        candidate: RecoveryCandidate,
        observation: RecoveryObservation,
    ) -> tuple[ExecutionAction, ObservationAction, str, bool]:
        status = candidate.effect_status
        effect_class = candidate.effect_class
        observation_available = cls._has_available_observation(observation)

        if observation.observation_state is ObservationState.CONSUMED:
            return (
                ExecutionAction.NO_REPLAY,
                ObservationAction.NONE,
                "observation is already durably consumed",
                False,
            )

        if effect_class is EffectClass.LOCAL_WRITE:
            if observation.matches_postcondition is True:
                if observation_available:
                    return (
                        ExecutionAction.NO_REPLAY,
                        ObservationAction.RESUME_WITH_OBSERVATION,
                        "local write matches the expected postcondition and has a durable observation",
                        False,
                    )
                return (
                    ExecutionAction.NO_REPLAY,
                    ObservationAction.OBSERVATION_UNAVAILABLE,
                    "local write matches the expected postcondition but the original ToolResult is unavailable",
                    True,
                )
            if observation.matches_precondition is True and status in {
                EffectStatus.PREPARED,
                EffectStatus.RUNNING,
                EffectStatus.UNKNOWN,
            }:
                return (
                    ExecutionAction.RETRY_ALLOWED,
                    ObservationAction.NONE,
                    "current file still matches the prepared precondition",
                    False,
                )
            if observation.matches_precondition is False and observation.matches_postcondition is False:
                return (
                    ExecutionAction.ASK_HUMAN,
                    ObservationAction.NONE,
                    "current file matches neither the expected precondition nor postcondition",
                    True,
                )
            return (
                ExecutionAction.ASK_HUMAN,
                ObservationAction.OBSERVATION_UNAVAILABLE,
                "local write requires a reliable current-file observation",
                True,
            )

        if effect_class is EffectClass.READ:
            if status in {EffectStatus.PREPARED, EffectStatus.RUNNING, EffectStatus.UNKNOWN}:
                return (
                    ExecutionAction.RETRY_ALLOWED,
                    ObservationAction.NONE,
                    "READ has no durable side effect and may be retried explicitly",
                    False,
                )
            if observation_available:
                return (
                    ExecutionAction.NO_REPLAY,
                    ObservationAction.RESUME_WITH_OBSERVATION,
                    "terminal READ has a durable observation",
                    False,
                )
            # A terminal READ with no result still needs attention.  Phase 0B.1
            # does not silently replay it; a later executor may choose a fresh
            # safe attempt after the invocation is reconstructable.
            return (
                ExecutionAction.NO_REPLAY,
                ObservationAction.OBSERVATION_UNAVAILABLE,
                "terminal READ has no durable observation",
                True,
            )

        if status in {EffectStatus.COMMITTED, EffectStatus.FAILED} and observation_available:
            return (
                ExecutionAction.NO_REPLAY,
                ObservationAction.RESUME_WITH_OBSERVATION,
                "terminal effect has a durable observation",
                False,
            )

        if effect_class in {EffectClass.EXTERNAL, EffectClass.EXECUTE, EffectClass.UNKNOWN}:
            return (
                ExecutionAction.ASK_HUMAN,
                ObservationAction.OBSERVATION_UNAVAILABLE,
                "opaque effect has no safe automatic recovery evidence",
                True,
            )

        return (
            ExecutionAction.ASK_HUMAN,
            ObservationAction.OBSERVATION_UNAVAILABLE,
            "effect observation is unavailable",
            True,
        )

    @staticmethod
    def _evidence(candidate: RecoveryCandidate, observation: RecoveryObservation) -> dict[str, Any]:
        evidence = {
            "effect_status": candidate.effect_status.value,
            "observation_state": observation.observation_state.value,
            "observation_kind": observation.kind,
            "certainty": observation.certainty,
            "exists": observation.exists,
            "current_hash": observation.current_hash,
            "matches_precondition": observation.matches_precondition,
            "matches_postcondition": observation.matches_postcondition,
            "result_ref": candidate.result_ref or observation.result_ref,
            "receipt_ref": candidate.receipt_ref or observation.receipt_ref,
        }
        # Keep path/hash evidence bounded and never copy a ToolResult or argument
        # payload into the plan.  The collector owns the path source.
        if "path" in observation.evidence:
            evidence["path"] = observation.evidence["path"]
        if "observation_error" in observation.evidence:
            evidence["observation_error"] = observation.evidence["observation_error"]
        return evidence


__all__ = ["RecoveryPlanner"]
