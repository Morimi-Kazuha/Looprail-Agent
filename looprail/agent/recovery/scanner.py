"""Read-only discovery of durable effect situations requiring recovery reasoning."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from looprail.agent.effects import EffectJournal, EffectRecord, EffectStatus, canonical_digest
from looprail.agent.recovery.models import ObservationState, RecoveryCandidate, RecoveryObservation

EffectReader = EffectJournal | Iterable[EffectRecord] | Callable[[], Iterable[EffectRecord]]


class RecoveryScanner:
    """Collapse an effect journal and emit candidates without performing recovery."""

    def __init__(self, reader: EffectReader | None = None, *, observation_source: Any = None):
        self._reader = reader
        self._observation_source = observation_source

    def scan(
        self,
        reader: EffectReader | None = None,
        *,
        observation_source: Any = None,
    ) -> list[RecoveryCandidate]:
        """Return all unresolved execution or unconsumed-observation candidates.

        The reader's ``load`` method is intentionally allowed to propagate journal
        corruption.  A scanner must not return a partial candidate list from an
        untrusted history.
        """

        source = reader if reader is not None else self._reader
        if source is None:
            raise ValueError("RecoveryScanner requires an EffectJournal or effect reader")
        records = self._read(source)
        latest = self._collapse(records)
        state_source = observation_source if observation_source is not None else self._observation_source

        candidates: list[RecoveryCandidate] = []
        for effect_id in sorted(latest):
            record = latest[effect_id]
            state, observation_id, evidence_digest = self._observation_facts(record, state_source)
            if not self._needs_recovery(record.status, state):
                continue
            candidates.append(
                RecoveryCandidate.from_effect(
                    record,
                    observation_state=state,
                    unresolved_reason=self._reason(record.status, state),
                    observation_id=observation_id,
                    evidence_digest=evidence_digest,
                )
            )
        return candidates

    scan_effects = scan

    @staticmethod
    def _read(source: EffectReader) -> list[EffectRecord]:
        if isinstance(source, EffectJournal):
            records = source.load()
        elif hasattr(source, "load") and callable(source.load):
            records = source.load()
        elif callable(source):
            records = source()
        else:
            records = source
        result = list(records)
        if not all(isinstance(record, EffectRecord) for record in result):
            raise TypeError("RecoveryScanner effect reader must return EffectRecord values")
        return result

    @staticmethod
    def _collapse(records: Iterable[EffectRecord]) -> dict[str, EffectRecord]:
        latest: dict[str, EffectRecord] = {}
        for record in records:
            latest[record.effect_id] = record
        return latest

    @staticmethod
    def _default_state(record: EffectRecord) -> ObservationState:
        if record.result_ref or record.receipt_ref:
            return ObservationState.AVAILABLE
        if record.status in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            return ObservationState.UNAVAILABLE
        return ObservationState.MISSING

    @classmethod
    def _observation_facts(
        cls,
        record: EffectRecord,
        source: Any,
    ) -> tuple[ObservationState, str | None, str | None]:
        default = cls._default_state(record)
        if source is None:
            return default, None, None

        value: Any = None
        if isinstance(source, Mapping):
            value = source.get(record.effect_id)
        elif hasattr(source, "state_for") and callable(source.state_for):
            value = source.state_for(record)
        elif hasattr(source, "get_state") and callable(source.get_state):
            value = source.get_state(record.effect_id)
        elif callable(source):
            value = source(record)

        if value is None:
            return default, None, None
        if isinstance(value, RecoveryObservation):
            return value.observation_state, value.observation_id, value.fingerprint
        if isinstance(value, Mapping):
            raw_state = value.get("observation_state", value.get("state", default))
            state = cls._coerce_state(raw_state, default)
            observation_id = value.get("observation_id")
            return state, str(observation_id) if observation_id is not None else None, canonical_digest(dict(value))
        return cls._coerce_state(value, default), None, canonical_digest({"state": str(value)})

    @staticmethod
    def _coerce_state(value: Any, default: ObservationState) -> ObservationState:
        try:
            if isinstance(value, ObservationState):
                return value
            return ObservationState(str(value).strip().lower())
        except ValueError:
            return default

    @staticmethod
    def _needs_recovery(status: EffectStatus, observation_state: ObservationState) -> bool:
        if status in {EffectStatus.PREPARED, EffectStatus.RUNNING, EffectStatus.UNKNOWN}:
            return True
        # A terminal effect still needs attention until its observation has been
        # durably consumed.  This is the COMMITTED/FAILED-but-unobserved case.
        return (
            status in {EffectStatus.COMMITTED, EffectStatus.FAILED}
            and observation_state is not ObservationState.CONSUMED
        )

    @staticmethod
    def _reason(status: EffectStatus, observation_state: ObservationState) -> str:
        if status is EffectStatus.PREPARED:
            return "effect_prepared_before_execution"
        if status is EffectStatus.RUNNING:
            return "effect_execution_window_open"
        if status is EffectStatus.UNKNOWN:
            return "effect_outcome_unknown"
        if status is EffectStatus.COMMITTED:
            return f"effect_committed_observation_{observation_state.value}"
        return f"effect_failed_observation_{observation_state.value}"


__all__ = ["EffectReader", "RecoveryScanner"]
