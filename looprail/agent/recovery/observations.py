"""Read-only observation collection for Phase 0B.1.

The collector reports facts that are already available locally.  It never calls a
Tool, provider, external API, or mutates the workspace.  In particular, a READ
candidate without a durable result is reported as having no current result; it is
not re-executed here.
"""

from __future__ import annotations

from pathlib import Path

from looprail.agent.effects import EffectClass, EffectRecord, EffectStatus, _file_hash
from looprail.agent.recovery.models import (
    ObservationState,
    RecoveryCandidate,
    RecoveryObservation,
    observation_now,
)


class ObservationCollector:
    """Collect only non-mutating evidence for a recovery candidate."""

    def collect(self, candidate: RecoveryCandidate | EffectRecord) -> RecoveryObservation:
        """Return a deterministic-fact observation without executing the effect."""

        candidate = self._as_candidate(candidate)
        if candidate.effect_class is EffectClass.LOCAL_WRITE:
            return self._collect_local_write(candidate)
        if candidate.result_ref or candidate.receipt_ref:
            return self._durable_reference_observation(candidate)
        if candidate.effect_class is EffectClass.READ:
            return RecoveryObservation(
                effect_id=candidate.effect_id,
                effect_class=candidate.effect_class,
                kind="NO_CURRENT_OBSERVATION",
                certainty="none",
                observation_state=candidate.observation_state,
                evidence={"tool_execution": False},
                observed_at=observation_now(),
            )
        # EXTERNAL, EXECUTE, and UNKNOWN have no safe query/execution contract in
        # this phase.  ``query_performed=False`` is explicit so a future planner
        # cannot mistake this for a remote failure response.
        return RecoveryObservation(
            effect_id=candidate.effect_id,
            effect_class=candidate.effect_class,
            kind="UNKNOWN",
            certainty="unknown",
            observation_state=candidate.observation_state,
            evidence={"query_performed": False, "tool_execution": False},
            observed_at=observation_now(),
        )

    # Readability aliases keep the collector usable by callers that name the
    # operation after the resulting observation rather than the collection step.
    observe = collect
    collect_observation = collect

    @staticmethod
    def _as_candidate(value: RecoveryCandidate | EffectRecord) -> RecoveryCandidate:
        if isinstance(value, RecoveryCandidate):
            return value
        if not isinstance(value, EffectRecord):
            raise TypeError("ObservationCollector expects RecoveryCandidate or EffectRecord")
        state = ObservationState.MISSING
        if value.status in {EffectStatus.COMMITTED, EffectStatus.FAILED} and not (
            value.result_ref or value.receipt_ref
        ):
            state = ObservationState.UNAVAILABLE
        return RecoveryCandidate.from_effect(value, observation_state=state)

    @staticmethod
    def _durable_reference_observation(candidate: RecoveryCandidate) -> RecoveryObservation:
        kind = "DURABLE_RECEIPT" if candidate.receipt_ref else "DURABLE_RESULT"
        return RecoveryObservation(
            effect_id=candidate.effect_id,
            effect_class=candidate.effect_class,
            kind=kind,
            result_ref=candidate.result_ref,
            receipt_ref=candidate.receipt_ref,
            certainty="durable-reference",
            observation_state=(
                candidate.observation_state
                if candidate.observation_state in {ObservationState.AVAILABLE, ObservationState.CONSUMED}
                else ObservationState.AVAILABLE
            ),
            evidence={"result_ref": candidate.result_ref, "receipt_ref": candidate.receipt_ref},
            observed_at=observation_now(),
        )

    @staticmethod
    def _path(candidate: RecoveryCandidate) -> str | None:
        post = candidate.postcondition or {}
        pre = candidate.precondition or {}
        path = post.get("path") or pre.get("path")
        return str(path) if path else None

    @staticmethod
    def _matches(
        *,
        expected_exists: object,
        expected_hash: object,
        current_exists: bool | None,
        current_hash: str | None,
    ) -> bool | None:
        if current_exists is None:
            return None
        if expected_exists is False:
            return current_exists is False and current_hash is None
        if expected_exists is True:
            return current_exists is True and expected_hash is not None and current_hash == expected_hash
        if expected_hash is not None:
            return current_exists is True and current_hash == expected_hash
        return None

    def _collect_local_write(self, candidate: RecoveryCandidate) -> RecoveryObservation:
        path_text = self._path(candidate)
        if path_text is None:
            return RecoveryObservation(
                effect_id=candidate.effect_id,
                effect_class=candidate.effect_class,
                kind="LOCAL_FILE_EVIDENCE_MISSING",
                result_ref=candidate.result_ref,
                receipt_ref=candidate.receipt_ref,
                certainty="unknown",
                observation_state=ObservationState.UNAVAILABLE,
                evidence={"observation_error": "path_evidence_missing"},
                observed_at=observation_now(),
            )

        path = Path(path_text)
        try:
            path.stat()
        except FileNotFoundError:
            current_exists: bool | None = False
            current_hash: str | None = None
            read_error: str | None = None
        except OSError:
            # A failed stat is not proof of absence.  Keep exists as None so the
            # planner cannot turn a permission or I/O problem into a retry.
            current_exists = None
            current_hash = None
            read_error = "stat_failed"
        else:
            current_exists = True
            current_hash = _file_hash(path)
            if current_hash is None:
                # The path existed at stat time but bytes could not be read (or it
                # disappeared during the read).  Report UNKNOWN rather than missing.
                current_exists = None
                read_error = "read_failed"
            else:
                read_error = None

        pre = candidate.precondition or {}
        post = candidate.postcondition or {}
        matches_pre = self._matches(
            expected_exists=pre.get("expected_exists"),
            expected_hash=pre.get("expected_pre_hash"),
            current_exists=current_exists,
            current_hash=current_hash,
        )
        matches_post = self._matches(
            expected_exists=post.get("expected_exists"),
            expected_hash=post.get("expected_post_hash"),
            current_exists=current_exists,
            current_hash=current_hash,
        )

        evidence: dict[str, object] = {
            "path": path_text,
            "current_exists": current_exists,
            "current_hash": current_hash,
        }
        if read_error is not None:
            evidence["observation_error"] = read_error

        if read_error is not None:
            kind = "LOCAL_FILE_READ_ERROR"
            certainty = "unknown"
            state = ObservationState.UNAVAILABLE
        else:
            kind = "LOCAL_FILE"
            certainty = "known"
            state = candidate.observation_state

        return RecoveryObservation(
            effect_id=candidate.effect_id,
            effect_class=candidate.effect_class,
            kind=kind,
            exists=current_exists,
            current_hash=current_hash,
            matches_precondition=matches_pre,
            matches_postcondition=matches_post,
            result_ref=candidate.result_ref,
            receipt_ref=candidate.receipt_ref,
            certainty=certainty,
            observation_state=state,
            evidence=evidence,
            observed_at=observation_now(),
        )


__all__ = ["ObservationCollector"]
