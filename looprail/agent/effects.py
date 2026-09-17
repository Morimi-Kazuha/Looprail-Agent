"""Durable facts for tool effects and conservative recovery planning.

This module deliberately owns a small execution domain rather than extending Session,
TraceStore, or Checkpoint.  The journal records what the runtime durably knows about a
tool boundary; it does not claim exactly-once execution and it never invokes a tool.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from looprail.utils.atomic_io import locked_append, read_utf8_with_incomplete_tail


class EffectClass(StrEnum):
    """Coarse effect class used to choose a safe recovery policy."""

    READ = "read"
    LOCAL_WRITE = "local_write"
    EXECUTE = "execute"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class EffectStatus(StrEnum):
    """Append-only lifecycle states for one logical effect attempt."""

    PREPARED = "prepared"
    RUNNING = "running"
    COMMITTED = "committed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    """Actions the Phase 0A planner may recommend.

    ``COMPENSATE`` is intentionally retained as a vocabulary item for a later phase;
    this planner never returns it and no compensation executor exists here.
    """

    SKIP = "skip"
    RETRY = "retry"
    QUERY = "query"
    ASK_HUMAN = "ask_human"
    COMPENSATE = "compensate"


class InvalidEffectTransitionError(ValueError):
    """Raised when a journal append would move an effect through an invalid state."""


# Short compatibility spelling for callers that use the design-domain name.
InvalidEffectTransition = InvalidEffectTransitionError


class EffectJournalError(OSError):
    """Raised when the durable effect journal cannot establish a required fact."""


class JournalCorruptionError(EffectJournalError):
    """Raised when a complete journal line is malformed or history cannot be trusted."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_digest(value: Any) -> str:
    """Return a stable SHA-256 digest without persisting the argument payload."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def new_effect_id() -> str:
    return f"effect-{uuid4().hex}"


def _enum_value(value: StrEnum | str, enum_type: type[StrEnum]) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    raw = str(value).strip().lower()
    try:
        return enum_type(raw)
    except ValueError:
        # Accept the all-caps spelling used in design documents and hand-authored
        # fixtures while retaining lower-case values on disk.
        try:
            return enum_type[raw.upper()]
        except KeyError as exc:
            raise ValueError(f"unknown {enum_type.__name__}: {value!r}") from exc


def _copy_json_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return dict(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class EffectRecord:
    """One immutable snapshot of a logical tool effect's durable facts."""

    effect_id: str
    turn_id: str | None = None
    session_key: str | None = None
    trace_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str = ""
    effect_class: EffectClass = EffectClass.UNKNOWN
    arguments_digest: str | None = None
    status: EffectStatus = EffectStatus.PREPARED
    attempt: int = 1
    prepared_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    precondition: Mapping[str, Any] | None = None
    postcondition: Mapping[str, Any] | None = None
    result_ref: str | None = None
    receipt_ref: str | None = None
    checkpoint_id: str | None = None
    error_class: str | None = None

    def __post_init__(self) -> None:
        if not self.effect_id:
            raise ValueError("effect_id must not be empty")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        object.__setattr__(self, "effect_class", _enum_value(self.effect_class, EffectClass))
        object.__setattr__(self, "status", _enum_value(self.status, EffectStatus))
        object.__setattr__(self, "precondition", _copy_json_mapping(self.precondition))
        object.__setattr__(self, "postcondition", _copy_json_mapping(self.postcondition))

    def with_updates(self, **changes: Any) -> EffectRecord:
        """Return a new record snapshot without mutating the prior journal fact."""

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": "looprail.effect.v1",
            "effect_id": self.effect_id,
            "turn_id": self.turn_id,
            "session_key": self.session_key,
            "trace_id": self.trace_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "effect_class": self.effect_class.value,
            "arguments_digest": self.arguments_digest,
            "status": self.status.value,
            "attempt": self.attempt,
            "prepared_at": self.prepared_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "precondition": _copy_json_mapping(self.precondition),
            "postcondition": _copy_json_mapping(self.postcondition),
            "result_ref": self.result_ref,
            "receipt_ref": self.receipt_ref,
            "checkpoint_id": self.checkpoint_id,
            "error_class": self.error_class,
        }
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EffectRecord:
        """Decode one JSON object, ignoring only additive unknown fields."""

        if not isinstance(payload, Mapping):
            raise ValueError("effect journal record must be an object")
        effect_id = payload.get("effect_id")
        if not isinstance(effect_id, str) or not effect_id:
            raise ValueError("effect journal record has no effect_id")
        return cls(
            effect_id=effect_id,
            turn_id=payload.get("turn_id"),
            session_key=payload.get("session_key"),
            trace_id=payload.get("trace_id"),
            tool_call_id=payload.get("tool_call_id"),
            tool_name=str(payload.get("tool_name") or ""),
            effect_class=payload.get("effect_class", EffectClass.UNKNOWN),
            arguments_digest=payload.get("arguments_digest"),
            status=payload.get("status", EffectStatus.PREPARED),
            attempt=int(payload.get("attempt", 1)),
            prepared_at=payload.get("prepared_at"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            precondition=payload.get("precondition"),
            postcondition=payload.get("postcondition"),
            result_ref=payload.get("result_ref"),
            receipt_ref=payload.get("receipt_ref"),
            checkpoint_id=payload.get("checkpoint_id"),
            error_class=payload.get("error_class"),
        )


_ALLOWED_TRANSITIONS: dict[EffectStatus, frozenset[EffectStatus]] = {
    EffectStatus.PREPARED: frozenset({EffectStatus.PREPARED, EffectStatus.RUNNING, EffectStatus.FAILED, EffectStatus.UNKNOWN}),
    EffectStatus.RUNNING: frozenset({EffectStatus.RUNNING, EffectStatus.COMMITTED, EffectStatus.FAILED, EffectStatus.UNKNOWN}),
    EffectStatus.COMMITTED: frozenset({EffectStatus.COMMITTED}),
    EffectStatus.FAILED: frozenset({EffectStatus.FAILED}),
    EffectStatus.UNKNOWN: frozenset({EffectStatus.UNKNOWN, EffectStatus.RUNNING, EffectStatus.COMMITTED, EffectStatus.FAILED}),
}

_RECORD_KEYS = frozenset(
    {
        "schema",
        "effect_id",
        "turn_id",
        "session_key",
        "trace_id",
        "tool_call_id",
        "tool_name",
        "effect_class",
        "arguments_digest",
        "status",
        "attempt",
        "prepared_at",
        "started_at",
        "finished_at",
        "precondition",
        "postcondition",
        "result_ref",
        "receipt_ref",
        "checkpoint_id",
        "error_class",
    }
)


def _decode_record_line(line: str) -> EffectRecord:
    payload = json.loads(line)
    if not isinstance(payload, Mapping):
        raise ValueError("effect journal record must be an object")
    if payload.get("schema") != "looprail.effect.v1" or not _RECORD_KEYS.issubset(payload):
        raise ValueError("effect journal record has an invalid schema or field set")
    return EffectRecord.from_dict(payload)


def _nonempty_lines(raw: str) -> list[tuple[int, str, bool]]:
    lines: list[tuple[int, str, bool]] = []
    for line_number, line in enumerate(raw.splitlines(keepends=True), start=1):
        if not line.strip():
            continue
        lines.append((line_number, line.rstrip("\r\n"), line.endswith(("\n", "\r"))))
    return lines


def _has_repairable_tail(raw: str) -> bool:
    lines = _nonempty_lines(raw)
    if not lines:
        return False
    _line_number, line, terminated = lines[-1]
    if terminated:
        return False
    try:
        _decode_record_line(line)
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError):
        return True
    return False


def _valid_records(raw: str) -> list[EffectRecord]:
    """Decode records, allowing only an unterminated malformed final tail."""

    records: list[EffectRecord] = []
    lines = _nonempty_lines(raw)
    for index, (line_number, line, terminated) in enumerate(lines):
        try:
            records.append(_decode_record_line(line))
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError):
            if index == len(lines) - 1 and not terminated:
                # A torn final line cannot establish a terminal fact.  It is deliberately
                # ignored; EffectJournal.append may remove it before the next append.
                continue
            raise JournalCorruptionError(f"corrupt effect journal record at line {line_number}") from None
    return records


def _validate_history(records: list[EffectRecord]) -> None:
    latest: dict[str, EffectRecord] = {}
    for record in records:
        previous = latest.get(record.effect_id)
        if previous is None:
            if record.status is not EffectStatus.PREPARED:
                raise InvalidEffectTransitionError(
                    f"effect {record.effect_id} must start at PREPARED, got {record.status.value}"
                )
        else:
            if record.status not in _ALLOWED_TRANSITIONS[previous.status]:
                raise InvalidEffectTransitionError(
                    f"invalid effect transition {previous.status.value} -> {record.status.value}"
                )
            for field_name in (
                "turn_id",
                "session_key",
                "trace_id",
                "tool_call_id",
                "tool_name",
                "effect_class",
                "arguments_digest",
                "attempt",
                "precondition",
                "postcondition",
            ):
                if getattr(record, field_name) != getattr(previous, field_name):
                    raise InvalidEffectTransitionError(
                        f"effect {record.effect_id} changed immutable field {field_name}"
                    )
        latest[record.effect_id] = record


class EffectJournal:
    """Crash-safe append-only JSONL journal independent of Session and TraceStore."""

    def __init__(self, state_root: Path | str | None = None, *, path: Path | str | None = None):
        if path is not None:
            self.path = Path(path)
        elif state_root is not None and Path(state_root).suffix == ".jsonl":
            self.path = Path(state_root)
        else:
            self.path = Path(state_root or Path.cwd()) / "effects" / "journal.jsonl"

    @property
    def journal_path(self) -> Path:
        return self.path

    def _validate_raw(self, raw: str, record: EffectRecord) -> None:
        latest = None
        for existing in _valid_records(raw):
            if existing.effect_id == record.effect_id:
                latest = existing
        if latest is None:
            if record.status is not EffectStatus.PREPARED:
                raise InvalidEffectTransition(
                    f"effect {record.effect_id} must start at PREPARED, got {record.status.value}"
                )
            return
        if record.effect_id != latest.effect_id:
            raise InvalidEffectTransition("effect transition has mismatched effect_id")
        for field_name in (
            "turn_id",
            "session_key",
            "trace_id",
            "tool_call_id",
            "tool_name",
            "effect_class",
            "arguments_digest",
            "attempt",
            "precondition",
            "postcondition",
        ):
            if getattr(record, field_name) != getattr(latest, field_name):
                raise InvalidEffectTransition(f"effect {record.effect_id} changed immutable field {field_name}")
        if record.status not in _ALLOWED_TRANSITIONS[latest.status]:
            raise InvalidEffectTransition(
                f"invalid effect transition {latest.status.value} -> {record.status.value}"
            )

    def append(self, record: EffectRecord) -> EffectRecord:
        """Durably append one state snapshot after validating its transition."""

        if not isinstance(record, EffectRecord):
            raise TypeError("EffectJournal.append expects EffectRecord")
        if record.status is not EffectStatus.PREPARED and not self.path.exists():
            raise InvalidEffectTransitionError(
                f"effect {record.effect_id} must start at PREPARED, got {record.status.value}"
            )
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        def validate(raw: str) -> None:
            self._validate_raw(raw, record)

        try:
            locked_append(
                self.path,
                [payload],
                require_existing=record.status is not EffectStatus.PREPARED,
                validate_existing=validate,
                repair_incomplete_tail=_has_repairable_tail,
            )
        except UnicodeDecodeError as exc:
            raise JournalCorruptionError(f"could not decode effect journal {self.path}") from exc
        except (OSError, ValueError) as exc:
            if isinstance(exc, (InvalidEffectTransition, JournalCorruptionError)):
                raise
            raise EffectJournalError(f"could not append effect {record.effect_id}") from exc
        return record

    def load(self) -> list[EffectRecord]:
        """Load all valid records, including a complete final line without newline."""

        try:
            raw = read_utf8_with_incomplete_tail(self.path)
        except FileNotFoundError:
            return []
        except UnicodeDecodeError as exc:
            raise JournalCorruptionError(f"could not decode effect journal {self.path}") from exc
        records = _valid_records(raw)
        _validate_history(records)
        return records

    def history(self, effect_id: str | None = None) -> list[EffectRecord]:
        records = self.load()
        return [record for record in records if effect_id is None or record.effect_id == effect_id]

    load_history = history

    def latest(self, effect_id: str) -> EffectRecord | None:
        latest = None
        for record in self.load():
            if record.effect_id == effect_id:
                latest = record
        return latest

    get_latest = latest
    latest_state = latest
    get_latest_state = latest


def classify_effect(tool_name: str, capability: Any) -> EffectClass:
    """Map current Tool capabilities to the narrower Phase 0A effect vocabulary.

    Existing ``WRITE`` is intentionally not treated as durable ``LOCAL_WRITE`` except
    for the explicitly supported ``write_file`` tool.
    """

    effect = getattr(capability, "effect", None)
    value = getattr(effect, "value", str(effect or "unknown")).lower()
    if value == "read":
        return EffectClass.READ
    if value == "write" and tool_name == "write_file":
        return EffectClass.LOCAL_WRITE
    if value == "execute":
        return EffectClass.EXECUTE
    if value == "external":
        return EffectClass.EXTERNAL
    return EffectClass.UNKNOWN


def _resolved_path(tool: Any, path: Any) -> Path:
    resolver = getattr(tool, "_resolve", None)
    if callable(resolver):
        return Path(resolver(str(path)))
    return Path(str(path)).expanduser().resolve()


def _text_bytes_for_write(content: Any) -> bytes:
    text = str(content)
    # Path.write_text() uses the host text newline convention when no newline is
    # specified.  Mirror that convention for evidence without storing file content.
    normalized = text.replace("\r\n", "\n").replace("\n", os.linesep)
    return normalized.encode("utf-8")


def _file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return None


def local_write_evidence(tool: Any, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Capture hash-only pre/post expectations for the supported ``write_file`` tool."""

    if getattr(tool, "name", None) != "write_file":
        return None
    if "path" not in arguments or "content" not in arguments:
        return None
    path = _resolved_path(tool, arguments["path"])
    exists = path.exists()
    pre_hash = _file_hash(path) if exists else None
    post_hash = hashlib.sha256(_text_bytes_for_write(arguments["content"])).hexdigest()
    precondition = {
        "path": str(path),
        "expected_pre_hash": pre_hash,
        "expected_exists": exists,
    }
    postcondition = {
        "path": str(path),
        "expected_post_hash": post_hash,
        "expected_exists": True,
    }
    return precondition, postcondition


def observe_local_write(record: EffectRecord) -> dict[str, Any]:
    """Read only hash/existence evidence for a previously prepared local write."""

    post = record.postcondition or {}
    pre = record.precondition or {}
    path = post.get("path") or pre.get("path")
    if not path:
        return {"current_exists": False, "current_hash": None}
    current_path = Path(str(path))
    exists = current_path.exists()
    return {"current_exists": exists, "current_hash": _file_hash(current_path) if exists else None}


def local_write_precondition_matches(
    record: EffectRecord,
    observations: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether an observed file still satisfies the prepared write version."""

    precondition = record.precondition or {}
    if "expected_exists" not in precondition or "expected_pre_hash" not in precondition:
        return False
    observed = dict(observations) if observations is not None else observe_local_write(record)
    if "current_exists" not in observed or "current_hash" not in observed:
        return False

    expected_exists = precondition["expected_exists"]
    expected_hash = precondition["expected_pre_hash"]
    if observed["current_exists"] != expected_exists:
        return False
    if expected_exists and expected_hash is None:
        return False
    return observed["current_hash"] == expected_hash


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    effect_id: str
    action: RecoveryAction
    reason: str
    effect_class: EffectClass
    status: EffectStatus
    observations: Mapping[str, Any] = field(default_factory=dict)

    @property
    def retry_allowed(self) -> bool:
        """Compatibility/readability flag for callers rendering RETRY_ALLOWED."""

        return self.action is RecoveryAction.RETRY


class RecoveryPlanner:
    """Pure Phase 0A policy: decide from facts, never execute or mutate."""

    @staticmethod
    def plan(record: EffectRecord, observations: Mapping[str, Any] | None = None) -> RecoveryDecision:
        observations = dict(observations or {})
        effect_class = record.effect_class
        status = record.status

        if status is EffectStatus.COMMITTED:
            return RecoveryDecision(
                record.effect_id,
                RecoveryAction.SKIP,
                "journal says the effect is committed",
                effect_class,
                status,
                observations,
            )

        if effect_class is EffectClass.READ:
            if observations.get("result_available"):
                action = RecoveryAction.SKIP
                reason = "a read result is already available"
            else:
                action = RecoveryAction.RETRY
                reason = "READ has no durable external side effect and may be retried"
            return RecoveryDecision(record.effect_id, action, reason, effect_class, status, observations)

        if effect_class is EffectClass.LOCAL_WRITE:
            post = record.postcondition or {}
            pre = record.precondition or {}
            if "path" not in post and "path" not in pre:
                return RecoveryDecision(
                    record.effect_id,
                    RecoveryAction.ASK_HUMAN,
                    "local write has no path evidence",
                    effect_class,
                    status,
                    observations,
                )
            if "current_hash" not in observations:
                return RecoveryDecision(
                    record.effect_id,
                    RecoveryAction.ASK_HUMAN,
                    "local write requires an explicit current-file hash observation",
                    effect_class,
                    status,
                    observations,
                )
            current_hash = observations.get("current_hash")
            expected_post = post.get("expected_post_hash")
            expected_pre = pre.get("expected_pre_hash")
            current_exists = observations.get("current_exists")
            if "expected_post_hash" in post and current_hash == expected_post:
                return RecoveryDecision(
                    record.effect_id,
                    RecoveryAction.SKIP,
                    "current file matches expected post-write hash",
                    effect_class,
                    status,
                    observations,
                )
            expected_pre_exists = pre.get("expected_exists")
            if expected_pre is None:
                pre_matches = (
                    "expected_pre_hash" in pre
                    and expected_pre_exists is False
                    and current_hash is None
                    and current_exists is False
                )
            else:
                pre_matches = "expected_pre_hash" in pre and current_hash == expected_pre and (
                    expected_pre_exists is None
                    or current_exists is None
                    or current_exists == expected_pre_exists
                )
            if pre_matches:
                return RecoveryDecision(
                    record.effect_id,
                    RecoveryAction.RETRY,
                    "current file still matches the prepared pre-write hash",
                    effect_class,
                    status,
                    observations,
                )
            return RecoveryDecision(
                record.effect_id,
                RecoveryAction.ASK_HUMAN,
                "current file matches neither the expected pre-write nor post-write hash",
                effect_class,
                status,
                observations,
            )

        return RecoveryDecision(
            record.effect_id,
            RecoveryAction.ASK_HUMAN,
            "effect class is not automatically replayable in Phase 0A",
            effect_class,
            status,
            observations,
        )


__all__ = [
    "EffectClass",
    "EffectJournal",
    "EffectJournalError",
    "EffectRecord",
    "EffectStatus",
    "JournalCorruptionError",
    "InvalidEffectTransition",
    "InvalidEffectTransitionError",
    "RecoveryAction",
    "RecoveryDecision",
    "RecoveryPlanner",
    "canonical_digest",
    "classify_effect",
    "local_write_evidence",
    "local_write_precondition_matches",
    "new_effect_id",
    "observe_local_write",
    "utc_now",
]
