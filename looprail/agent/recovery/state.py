"""Durable recovery marker storage.

The marker is intentionally smaller than a Session, EffectJournal, or
Checkpoint.  It records only the last Turn outcome and the optional checkpoint
reference.  Effect facts are always re-derived from ``EffectJournal`` by the
projector; a marker can therefore be missing or stale without becoming a
second effect truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from looprail.agent.effects import canonical_digest, utc_now
from looprail.utils.atomic_io import StorageCorruptionError, atomic_replace, locked_read

RECOVERY_STATE_SCHEMA = "looprail.recovery.state.v1"


class RecoveryArtifactError(ValueError):
    """The optional recovery marker cannot be trusted."""


@dataclass(frozen=True, slots=True)
class RecoveryMarker:
    """The small durable hint written once a Turn reaches a known boundary."""

    session_key: str
    last_turn_id: str | None = None
    last_turn_status: str | None = None
    checkpoint_id: str | None = None
    checkpoint_files: tuple[str, ...] = ()
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_key, str) or not self.session_key:
            raise ValueError("recovery marker session_key must not be empty")
        if self.last_turn_id is not None and not isinstance(self.last_turn_id, str):
            raise ValueError("recovery marker last_turn_id must be a string or null")
        if self.last_turn_status is not None and not isinstance(self.last_turn_status, str):
            raise ValueError("recovery marker last_turn_status must be a string or null")
        if self.checkpoint_id is not None and not isinstance(self.checkpoint_id, str):
            raise ValueError("recovery marker checkpoint_id must be a string or null")
        if self.updated_at is not None and not isinstance(self.updated_at, str):
            raise ValueError("recovery marker updated_at must be a string or null")
        files = tuple(self.checkpoint_files)
        if not all(isinstance(item, str) for item in files):
            raise ValueError("recovery marker checkpoint_files must contain only strings")
        object.__setattr__(self, "checkpoint_files", files)
        object.__setattr__(self, "updated_at", self.updated_at or utc_now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RECOVERY_STATE_SCHEMA,
            "session_key": self.session_key,
            "last_turn_id": self.last_turn_id,
            "last_turn_status": self.last_turn_status,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_files": list(self.checkpoint_files),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecoveryMarker":
        if not isinstance(payload, Mapping):
            raise RecoveryArtifactError("recovery marker must be an object")
        if payload.get("schema") != RECOVERY_STATE_SCHEMA:
            raise RecoveryArtifactError("recovery marker has an unsupported schema")
        required = {
            "schema",
            "session_key",
            "last_turn_id",
            "last_turn_status",
            "checkpoint_id",
            "checkpoint_files",
            "updated_at",
        }
        if not required.issubset(payload):
            raise RecoveryArtifactError("recovery marker is missing required fields")
        files = payload.get("checkpoint_files")
        if not isinstance(files, list):
            raise RecoveryArtifactError("recovery marker checkpoint_files must be a list")
        try:
            return cls(
                session_key=payload["session_key"],
                last_turn_id=payload["last_turn_id"],
                last_turn_status=payload["last_turn_status"],
                checkpoint_id=payload["checkpoint_id"],
                checkpoint_files=tuple(files),
                updated_at=payload["updated_at"],
            )
        except (TypeError, ValueError) as exc:
            raise RecoveryArtifactError("recovery marker has invalid fields") from exc


class RecoveryStateStore:
    """Atomically persist and load one marker per Session.

    A digest filename avoids turning a user-controlled session key into a path.
    The session key is still stored and verified in the payload, so a misplaced
    or copied marker cannot silently apply to another Session.
    """

    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root).expanduser().resolve()
        self.recovery_dir = self.state_root / "recovery"

    def path_for(self, session_key: str) -> Path:
        if not isinstance(session_key, str) or not session_key:
            raise ValueError("session_key must not be empty")
        return self.recovery_dir / f"session-{canonical_digest(session_key)}.json"

    def save_turn(
        self,
        *,
        session_key: str,
        turn_id: str | None,
        status: str | None,
        checkpoint_id: str | None = None,
        checkpoint_files: list[str] | tuple[str, ...] | None = None,
    ) -> RecoveryMarker:
        marker = RecoveryMarker(
            session_key=session_key,
            last_turn_id=turn_id,
            last_turn_status=status,
            checkpoint_id=checkpoint_id,
            checkpoint_files=tuple(checkpoint_files or ()),
        )
        path = self.path_for(session_key)
        payload = json.dumps(marker.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            atomic_replace(path, payload, increment_epoch=True)
        except OSError:
            raise
        return marker

    def load(self, session_key: str) -> RecoveryMarker | None:
        path = self.path_for(session_key)
        try:
            raw, _epoch, _known = locked_read(path)
        except StorageCorruptionError as exc:
            raise RecoveryArtifactError(f"recovery marker generation is corrupt: {path}") from exc
        except (OSError, UnicodeError) as exc:
            raise RecoveryArtifactError(f"recovery marker could not be read: {path}") from exc
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RecoveryArtifactError(f"recovery marker is not valid JSON: {path}") from exc
        try:
            marker = RecoveryMarker.from_dict(payload)
        except RecoveryArtifactError:
            raise
        if marker.session_key != session_key:
            raise RecoveryArtifactError("recovery marker session identity does not match its lookup key")
        return marker


__all__ = [
    "RECOVERY_STATE_SCHEMA",
    "RecoveryArtifactError",
    "RecoveryMarker",
    "RecoveryStateStore",
]
