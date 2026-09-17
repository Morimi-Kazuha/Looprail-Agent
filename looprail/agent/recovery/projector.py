"""Fresh-runtime recovery projection and conservative resume evidence.

``RecoveryProjector`` is read-only with respect to the Session, workspace,
EffectJournal, and Checkpoint.  It combines their durable facts into a bounded
projection for one new Turn.  It never calls a Tool, provider, subprocess, or
restore operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from looprail.agent.effects import EffectJournal, EffectStatus, JournalCorruptionError, canonical_digest
from looprail.agent.recovery.models import RecoveryCandidate, RecoveryPlan
from looprail.agent.recovery.observations import ObservationCollector
from looprail.agent.recovery.planner import RecoveryPlanner
from looprail.agent.recovery.scanner import RecoveryScanner
from looprail.agent.recovery.state import RecoveryArtifactError, RecoveryMarker, RecoveryStateStore
from looprail.session.manager import SessionManager


class RecoveryArtifactStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    CORRUPT = "corrupt"


class DurableInputStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class RecoveryState:
    """A bounded, read-only state view for one explicitly resumed Session."""

    session_key: str
    session_exists: bool | None = None
    session_message_count: int | None = None
    artifact_status: RecoveryArtifactStatus = RecoveryArtifactStatus.MISSING
    last_turn_id: str | None = None
    last_turn_status: str | None = None
    checkpoint_id: str | None = None
    checkpoint_files: tuple[str, ...] = ()
    checkpoint_status: str = "not_recorded"
    effect_journal_status: DurableInputStatus = DurableInputStatus.MISSING
    candidates: tuple[RecoveryCandidate, ...] = ()
    plans: tuple[RecoveryPlan, ...] = ()
    committed_effect_ids: tuple[str, ...] = ()
    failed_effect_ids: tuple[str, ...] = ()
    unknown_effect_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    projection_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_status", RecoveryArtifactStatus(self.artifact_status))
        object.__setattr__(self, "effect_journal_status", DurableInputStatus(self.effect_journal_status))
        object.__setattr__(self, "checkpoint_files", tuple(self.checkpoint_files))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "plans", tuple(self.plans))
        object.__setattr__(self, "committed_effect_ids", tuple(self.committed_effect_ids))
        object.__setattr__(self, "failed_effect_ids", tuple(self.failed_effect_ids))
        object.__setattr__(self, "unknown_effect_ids", tuple(self.unknown_effect_ids))
        object.__setattr__(self, "warnings", tuple(dict.fromkeys(self.warnings)))

    @property
    def automatic_replay_effect_ids(self) -> tuple[str, ...]:
        """The default automatic replay set, deliberately always empty."""

        return ()

    @property
    def resumed_continuation(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "looprail.recovery.projection.v1",
            "projection_id": self.projection_id,
            "session_key": self.session_key,
            "session_exists": self.session_exists,
            "session_message_count": self.session_message_count,
            "artifact_status": self.artifact_status.value,
            "last_turn_id": self.last_turn_id,
            "last_turn_status": self.last_turn_status,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_files": list(self.checkpoint_files),
            "checkpoint_status": self.checkpoint_status,
            "effect_journal_status": self.effect_journal_status.value,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "plans": [plan.to_dict() for plan in self.plans],
            "committed_effect_ids": list(self.committed_effect_ids),
            "failed_effect_ids": list(self.failed_effect_ids),
            "unknown_effect_ids": list(self.unknown_effect_ids),
            "warnings": list(self.warnings),
            "automatic_replay_effect_ids": [],
        }

    def prompt_block(self, *, max_effects: int = 16) -> str:
        """Render bounded recovery evidence for the next model Turn."""

        lines = ["[Recovery — durable state inspected; this is a fresh Turn]"]
        lines.append(f"Session: {self.session_key}")
        lines.append(
            "Previous Turn: "
            + (f"{self.last_turn_id} ({self.last_turn_status or 'unknown'})" if self.last_turn_id else "not recorded")
        )
        lines.append(f"Recovery artifact: {self.artifact_status.value}")
        if self.checkpoint_id:
            lines.append(
                f"Checkpoint reference: {self.checkpoint_id} ({self.checkpoint_status}); "
                "reference-only inspection, no automatic restore."
            )
            if self.checkpoint_files:
                lines.append("Checkpoint files: " + ", ".join(self.checkpoint_files[:max_effects]))
        else:
            lines.append("Checkpoint reference: none recorded.")

        if self.effect_journal_status is DurableInputStatus.CORRUPT:
            lines.append("Effect journal: not trusted; automatic replay is disabled.")
        elif self.plans:
            lines.append("Effect evidence:")
            for plan in self.plans[:max_effects]:
                effect_status = str(plan.evidence.get("effect_status", "unknown"))
                observation = str(plan.observed_state.get("observation_state", "unknown"))
                lines.append(
                    f"- {plan.effect_id} / {plan.tool_name or 'unknown tool'}: "
                    f"state={effect_status}; decision={plan.execution_action.value}; observation={observation}."
                )
            if len(self.plans) > max_effects:
                lines.append(f"- {len(self.plans) - max_effects} additional effect record(s) omitted.")
        else:
            lines.append("Effect journal: no unresolved effect candidate was found.")

        if self.unknown_effect_ids:
            lines.append(
                "Unknown effects: "
                + ", ".join(self.unknown_effect_ids[:max_effects])
                + "; do not auto-replay."
            )
        if self.warnings:
            lines.append("Durable warnings: " + ", ".join(self.warnings[:max_effects]))
        lines.append(
            "The previous process/provider/coroutine/Tool is not resumed. "
            "COMMITTED effects are not blindly re-executed; FAILED effects are not automatically retried; "
            "UNKNOWN effects are never auto-replayed. Any retry is an explicit decision in this new Turn."
        )
        return "\n".join(lines)

    def trace_attributes(self, *, new_turn_id: str | None) -> dict[str, Any]:
        """Return bounded attributes for the local ``recovery.resume`` trace."""

        return {
            "recovery.resumed_continuation": True,
            "recovery.task": "looprail.run.resume",
            "recovery.session_key": self.session_key,
            "recovery.durable_state_inspected": True,
            "recovery.recovery_artifact_status": self.artifact_status.value,
            "recovery.effect_journal_status": self.effect_journal_status.value,
            "recovery.unknown_effect_ids": list(self.unknown_effect_ids[:16]),
            "recovery.checkpoint_id": self.checkpoint_id,
            "recovery.checkpoint_status": self.checkpoint_status,
            "recovery.checkpoint_used": bool(self.checkpoint_id),
            "recovery.checkpoint_use": "reference_only",
            "recovery.checkpoint_restored": False,
            "recovery.previous_turn_id": self.last_turn_id,
            "recovery.new_turn": True,
            "recovery.new_turn_id": new_turn_id,
            "recovery.projection_id": self.projection_id,
        }


class RecoveryProjector:
    """Read durable sources and construct a new-Turn recovery state."""

    def __init__(
        self,
        state_root: Path | str,
        *,
        workspace: Path | str | None = None,
        session_manager: SessionManager | None = None,
        effect_journal: EffectJournal | None = None,
        checkpoint_dir: Path | str | None = None,
        state_store: RecoveryStateStore | None = None,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.workspace = Path(workspace or state_root).expanduser().resolve()
        self.sessions = session_manager or SessionManager(self.state_root)
        self.effect_journal = effect_journal or EffectJournal(self.state_root)
        self.state_store = state_store or RecoveryStateStore(self.state_root)
        self.checkpoint_dir = Path(checkpoint_dir).expanduser().resolve() if checkpoint_dir is not None else None

    def record_turn(
        self,
        *,
        session_key: str,
        turn_id: str | None,
        status: str | None,
        checkpoint_id: str | None = None,
        checkpoint_files: list[str] | tuple[str, ...] | None = None,
    ) -> RecoveryMarker:
        """Persist only Turn/checkpoint metadata; effects remain journal-owned."""

        return self.state_store.save_turn(
            session_key=session_key,
            turn_id=turn_id,
            status=status,
            checkpoint_id=checkpoint_id,
            checkpoint_files=checkpoint_files,
        )

    def project(self, session_key: str) -> RecoveryState:
        if not isinstance(session_key, str) or not session_key:
            raise ValueError("session_key must not be empty")

        marker, artifact_status, warnings = self._load_marker(session_key)
        session_exists, session_message_count = self._inspect_session(session_key, warnings)
        journal_status, records = self._load_effects(warnings)
        session_records = [record for record in records if record.session_key == session_key]
        latest = self._latest(session_records)

        candidates: list[RecoveryCandidate] = []
        plans: list[RecoveryPlan] = []
        if journal_status is DurableInputStatus.AVAILABLE:
            candidates = RecoveryScanner(session_records).scan()
            collector = ObservationCollector()
            for candidate in candidates:
                plans.append(RecoveryPlanner.plan(candidate, collector.collect(candidate)))

        checkpoint_id = marker.checkpoint_id if marker is not None else None
        checkpoint_files = marker.checkpoint_files if marker is not None else ()
        checkpoint_status = self._checkpoint_status(checkpoint_id, warnings)
        committed = tuple(sorted(effect_id for effect_id, record in latest.items() if record.status is EffectStatus.COMMITTED))
        failed = tuple(sorted(effect_id for effect_id, record in latest.items() if record.status is EffectStatus.FAILED))
        unknown = tuple(sorted(effect_id for effect_id, record in latest.items() if record.status is EffectStatus.UNKNOWN))

        identity = {
            "session_key": session_key,
            "session_exists": session_exists,
            "session_message_count": session_message_count,
            "artifact_status": artifact_status.value,
            "last_turn_id": marker.last_turn_id if marker else None,
            "last_turn_status": marker.last_turn_status if marker else None,
            "checkpoint_id": checkpoint_id,
            "checkpoint_files": list(checkpoint_files),
            "checkpoint_status": checkpoint_status,
            "effect_journal_status": journal_status.value,
            "latest_effects": [
                {"effect_id": effect_id, "status": record.status.value}
                for effect_id, record in sorted(latest.items())
            ],
            "plans": [plan.plan_hash for plan in plans],
            "warnings": list(warnings),
        }
        projection_id = f"projection-{canonical_digest(identity)}"
        return RecoveryState(
            session_key=session_key,
            session_exists=session_exists,
            session_message_count=session_message_count,
            artifact_status=artifact_status,
            last_turn_id=marker.last_turn_id if marker else None,
            last_turn_status=marker.last_turn_status if marker else None,
            checkpoint_id=checkpoint_id,
            checkpoint_files=checkpoint_files,
            checkpoint_status=checkpoint_status,
            effect_journal_status=journal_status,
            candidates=tuple(candidates),
            plans=tuple(plans),
            committed_effect_ids=committed,
            failed_effect_ids=failed,
            unknown_effect_ids=unknown,
            warnings=tuple(warnings),
            projection_id=projection_id,
        )

    def _load_marker(
        self,
        session_key: str,
    ) -> tuple[RecoveryMarker | None, RecoveryArtifactStatus, list[str]]:
        warnings: list[str] = []
        try:
            marker = self.state_store.load(session_key)
        except RecoveryArtifactError:
            return None, RecoveryArtifactStatus.CORRUPT, ["recovery_artifact_corrupt"]
        if marker is None:
            return None, RecoveryArtifactStatus.MISSING, ["recovery_artifact_missing"]
        return marker, RecoveryArtifactStatus.AVAILABLE, warnings

    def _inspect_session(self, session_key: str, warnings: list[str]) -> tuple[bool | None, int | None]:
        try:
            session = self.sessions.peek(session_key)
        except Exception:  # noqa: BLE001 — projection must fail closed on unreadable Session state.
            warnings.append("session_corrupt_or_unreadable")
            return None, None
        if session is None:
            warnings.append("session_missing")
            return False, None
        return True, len(session.messages)

    def _load_effects(self, warnings: list[str]) -> tuple[DurableInputStatus, list[Any]]:
        if not self.effect_journal.path.exists():
            warnings.append("effect_journal_missing")
            return DurableInputStatus.MISSING, []
        try:
            return DurableInputStatus.AVAILABLE, self.effect_journal.load()
        except (JournalCorruptionError, ValueError, OSError):
            # Invalid transitions and malformed complete lines are equally
            # untrusted.  No partial scan is projected from them.
            warnings.append("effect_journal_corrupt")
            return DurableInputStatus.CORRUPT, []

    @staticmethod
    def _latest(records: list[Any]) -> dict[str, Any]:
        latest: dict[str, Any] = {}
        for record in records:
            latest[record.effect_id] = record
        return latest

    def _checkpoint_status(self, checkpoint_id: str | None, warnings: list[str]) -> str:
        if not checkpoint_id:
            return "not_recorded"
        if self.checkpoint_dir is None:
            warnings.append("checkpoint_reference_unverified")
            return "unverified"
        if not (self.checkpoint_dir / "HEAD").is_file():
            warnings.append("checkpoint_missing")
            return "missing"
        return "available"


__all__ = [
    "DurableInputStatus",
    "RecoveryArtifactStatus",
    "RecoveryProjector",
    "RecoveryState",
]
