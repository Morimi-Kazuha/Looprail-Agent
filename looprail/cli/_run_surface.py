"""Reviewer-facing rendering helpers for the shipping ``looprail run`` surface.

This module is deliberately an observation layer.  It formats events that have
already crossed the Spine/Tool Runtime boundary and reads the durable Trace
and Recovery projections after a turn.  It does not execute tools, infer a
terminal result from assistant text, or maintain a second runtime state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping

from looprail.spine.events import ToolEvent, TurnEnded, TurnEvent, TurnFailed
from looprail.tracing.store import TraceStore


class RunVerbosity(StrEnum):
    """The intentionally small output contract of ``looprail run``."""

    NORMAL = "normal"
    VERBOSE = "verbose"
    QUIET = "quiet"


def parse_verbosity(value: str) -> RunVerbosity:
    """Normalize a user-facing verbosity value or raise a useful ``ValueError``."""

    try:
        return RunVerbosity(str(value).strip().casefold())
    except ValueError as exc:
        raise ValueError("verbosity must be one of: normal, verbose, quiet") from exc


def short_id(value: Any, *, keep: int = 12) -> str:
    """Abbreviate an opaque identity while retaining a full lookup elsewhere."""

    text = str(value or "")
    if len(text) <= keep:
        return text
    return text[:keep] + "..."


def bounded_text(value: Any, limit: int = 240) -> str:
    """Make arbitrary runtime text safe for a single bounded terminal line."""

    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_secret",
    "private_key",
}


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Return a tiny redacted projection for Tool arguments."""

    if depth >= 3:
        return "<nested>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 16:
                result["..."] = "more arguments omitted"
                break
            key_text = str(key)
            normalized = key_text.strip().casefold().replace("-", "_")
            result[key_text] = "[REDACTED]" if normalized in _SENSITIVE_KEYS else _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        values = [_safe_value(item, depth=depth + 1) for item in value[:16]]
        if len(value) > 16:
            values.append("...")
        return values
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return bounded_text(value, 120)


def bounded_json(value: Any, limit: int = 180) -> str:
    """Serialize a bounded, redacted value for human-readable Tool output."""

    try:
        text = json.dumps(_safe_value(value), ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        text = bounded_text(value, limit)
    return bounded_text(text, limit)


def _tool_target(name: str, arguments: Mapping[str, Any] | None) -> str | None:
    args = arguments if isinstance(arguments, Mapping) else {}
    if name == "tool_call" and isinstance(args.get("arguments"), Mapping):
        return format_tool_invocation(str(args.get("name") or "tool_call"), args["arguments"])
    if name in {"read_file", "write_file", "edit_file", "list_dir", "find_files", "file_search"}:
        target = args.get("path") or args.get("directory") or args.get("root") or args.get("pattern")
        return bounded_text(target, 180) if target is not None else None
    if name in {"grep", "search_files"}:
        query = args.get("query") or args.get("pattern") or args.get("text")
        target = args.get("path") or args.get("directory") or args.get("root")
        bits = [bounded_text(query, 100) if query is not None else ""]
        if target is not None:
            bits.append(bounded_text(target, 100))
        return " ".join(bit for bit in bits if bit)
    if name in {"shell", "exec", "execute"}:
        command = args.get("command") or args.get("cmd") or args.get("script")
        return bounded_text(command, 180) if command is not None else None
    return None


def format_tool_invocation(name: str, arguments: Mapping[str, Any] | None = None) -> str:
    """Render only the bounded, user-useful part of a real Tool invocation."""

    tool_name = bounded_text(name or "unknown_tool", 80)
    target = _tool_target(tool_name, arguments)
    if target:
        return f"{tool_name} {target}"
    if arguments:
        return f"{tool_name} {bounded_json(arguments)}"
    return tool_name


def _event_effect_status(event: ToolEvent) -> str | None:
    value = getattr(event, "effect_status", None)
    if value is not None:
        value = getattr(value, "value", value)
        normalized = str(value).strip().casefold()
        if normalized:
            return normalized
    preview = str(event.result_preview or "").casefold()
    if any(
        marker in preview
        for marker in (
            "outcome is unknown",
            "outcome unknown",
            "effect unknown",
            "journal unavailable",
        )
    ):
        return "unknown"
    return None


def tool_failure_category(event: ToolEvent) -> str | None:
    """Use the Runtime category when present, with a narrow legacy fallback."""

    value = getattr(event, "failure_category", None)
    if value is not None:
        value = getattr(value, "value", value)
        if str(value).strip():
            return str(value).strip()
    text = str(event.result_preview or "").casefold()
    if "invalid parameters" in text or "not found" in text:
        return "tool_validation_failure"
    if "timed out" in text:
        return "tool_timeout"
    if "outcome is unknown" in text or "journal unavailable" in text:
        return "tool_effect_unknown"
    return "tool_execution_failure" if event.failed else None


def format_tool_start(event: ToolEvent) -> str:
    """Format a Tool START event as a bounded two-line progress block."""

    return f"Tool\n  {format_tool_invocation(event.name, event.arguments)}"


def format_tool_complete(
    event: ToolEvent,
    *,
    start_name: str | None = None,
    start_arguments: Mapping[str, Any] | None = None,
    verbose: bool = False,
) -> str:
    """Format a Tool COMPLETE event without treating text as success evidence."""

    name = event.name or start_name or "unknown_tool"
    invocation = format_tool_invocation(name, start_arguments)
    effect_status = _event_effect_status(event)
    if effect_status == "unknown":
        status = "? effect outcome unknown"
    elif event.failed or effect_status == "failed":
        status = f"FAIL {tool_failure_category(event) or 'tool_failure'}"
    elif effect_status == "committed":
        status = "OK committed"
    else:
        status = "OK completed"

    duration = ""
    if event.duration_ms is not None:
        try:
            duration = f" ({max(0, int(float(event.duration_ms)))} ms)"
        except (TypeError, ValueError):
            duration = ""
    if event.truncated:
        status += " [preview truncated]"

    lines = ["Tool", f"  {invocation}", f"  {status}{duration}"]
    if verbose:
        effect_id = getattr(event, "effect_id", None)
        if effect_status:
            lines.append(f"  effect: {effect_status}")
        if effect_id:
            lines.append(f"  effect_id: {bounded_text(effect_id, 120)}")
        if event.result_preview:
            lines.append(f"  preview: {bounded_text(event.result_preview, 240)}")
    elif event.failed and event.result_preview:
        lines.append(f"  detail: {bounded_text(event.result_preview, 180)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class RunEvidence:
    """The bounded lookup result shown at the end of one CLI turn."""

    run_id: str | None = None
    trace_path: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    records: tuple[dict[str, Any], ...] = ()


def discover_run_evidence(turn_id: str | None, state_dir: Path | str | None) -> RunEvidence:
    """Resolve the displayed Turn ID through the durable Phase 6 TraceStore."""

    if not turn_id or state_dir is None:
        return RunEvidence()
    try:
        store = TraceStore(state_dir)
        run_id = store.find_run_id(turn_id)
        if not run_id:
            return RunEvidence()
        path = store.run_path(run_id)
        records = store.read_trace(run_id)
        if not path.is_file() or not records:
            return RunEvidence(run_id=run_id)
        return RunEvidence(
            run_id=run_id,
            trace_path=store.trace_artifact_ref(run_id),
            summary=store.trace_summary(run_id),
            records=records,
        )
    except Exception:  # noqa: BLE001 — evidence discovery is observational only.
        # Evidence is best-effort at the presentation boundary.  A missing
        # lookup must be visible, but it must not mutate or break the Turn.
        return RunEvidence()


def _span_rows(evidence: RunEvidence, name: str | None = None) -> list[dict[str, Any]]:
    # ``trace_summary`` intentionally exposes only selected attributes.  The
    # reviewer surface needs a few additional bounded Phase 4/5 counters, so
    # prefer the already-loaded durable records for those observations.  The
    # summary-only fallback keeps this helper useful for synthetic evidence
    # and for an older store that cannot return the full run.
    rows: Any = list(evidence.records)
    if not rows:
        rows = evidence.summary.get("spans")
    if not isinstance(rows, list):
        rows = []
    return [row for row in rows if isinstance(row, dict) and (name is None or row.get("name") == name)]


def _attrs(row: Mapping[str, Any] | None) -> dict[str, Any]:
    value = row.get("attributes") if isinstance(row, Mapping) else None
    return value if isinstance(value, dict) else {}


def _root_attrs(evidence: RunEvidence) -> dict[str, Any]:
    rows = _span_rows(evidence, "spine.turn")
    return _attrs(rows[-1]) if rows else {}


def _context_values(evidence: RunEvidence) -> dict[str, Any] | None:
    rows = _span_rows(evidence, "context.assemble")
    if not rows:
        return None
    attrs = _attrs(rows[-1])
    def _count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    compacted = _count(attrs.get("context.items_compacted", 0))
    dropped = _count(attrs.get("context.items_dropped", 0))
    for row in _span_rows(evidence, "context.compact"):
        row_attrs = _attrs(row)
        compacted += _count(row_attrs.get("context.items_compacted", 0))
        dropped += _count(row_attrs.get("context.items_dropped", 0))
    return {
        "estimated": attrs.get("context.estimated_tokens_after"),
        "limit": attrs.get("context.limit"),
        "compacted": compacted,
        "dropped": dropped,
        "budget": attrs.get("context.input_budget"),
    }


def _provider_values(evidence: RunEvidence) -> dict[str, Any] | None:
    rows = [*_span_rows(evidence, "llm.call"), *_span_rows(evidence, "llm.call.stream")]
    if not rows:
        return None
    attrs = _attrs(rows[-1])
    return {"provider": attrs.get("llm.provider"), "model": attrs.get("llm.model")}


def _memory_values(evidence: RunEvidence, outcome: Any) -> dict[str, int] | None:
    hits = getattr(outcome, "memory_hits", None)
    if not isinstance(hits, int):
        hits = 0
        for row in _span_rows(evidence, "memory.recall"):
            value = _attrs(row).get("memory.hits")
            if isinstance(value, int):
                hits = max(hits, value)
    writes = len(_span_rows(evidence, "memory.write"))
    memory_boundaries = [
        row for row in _span_rows(evidence) if str(row.get("name", "")).startswith("memory.")
    ]
    if hits == 0 and writes == 0 and not memory_boundaries:
        return None
    return {"recalled": max(0, hits), "writes": max(0, writes)}


def _failure_category(evidence: RunEvidence) -> str | None:
    attrs = _root_attrs(evidence)
    for key in ("spine.provider_error_category", "spine.failure_category", "spine.error_class"):
        value = attrs.get(key)
        if value:
            return str(value)
    return None


def _marker_status(state_dir: Path | str | None, session_key: str | None, turn_id: str | None) -> str | None:
    if state_dir is None or not session_key or not turn_id:
        return None
    try:
        from looprail.agent.recovery.state import RecoveryStateStore

        marker = RecoveryStateStore(state_dir).load(session_key)
        if marker is not None and marker.last_turn_id == turn_id:
            return marker.last_turn_status
    except Exception:  # noqa: BLE001 — a diagnostic lookup cannot change Turn truth.
        pass
    return None


def terminal_status(
    terminal: TurnEvent | None,
    outcome: Any,
    *,
    evidence: RunEvidence | None = None,
    recovery_status: str | None = None,
) -> str:
    """Map Runtime terminal facts to the compact human-facing label."""

    if isinstance(terminal, TurnFailed):
        if evidence is not None and _failure_category(evidence) == "context_budget_failure":
            return "BUDGET_EXCEEDED"
        if recovery_status == "interrupted":
            return "MAX_ITERATIONS"
        return "CANCELLED" if terminal.cancelled else "FAILED"
    if recovery_status == "interrupted":
        return "MAX_ITERATIONS"
    if recovery_status == "error":
        return "FAILED"
    if terminal is None and outcome is None:
        root_outcome = _root_attrs(evidence).get("spine.outcome") if evidence else None
        if root_outcome == "cancelled":
            return "CANCELLED"
        return "FAILED"
    if isinstance(terminal, TurnEnded):
        root_outcome = _root_attrs(evidence or {}).get("spine.outcome") if evidence else None
        if root_outcome == "completed_with_tool_failure" or terminal.tool_failures:
            return "COMPLETED_WITH_TOOL_FAILURE"
        return "COMPLETED"
    return "COMPLETED" if outcome is not None else "FAILED"


@dataclass
class CliRunReporter:
    """Render one ``looprail run`` turn from Runtime observations."""

    emit: Callable[[str], None]
    session_key: str
    task: str
    state_dir: Path | str | None = None
    verbosity: RunVerbosity = RunVerbosity.NORMAL
    recovery_state: Any | None = None
    trace_dir: Path | str | None = None
    recovery_dir: Path | str | None = None
    terminal: TurnEvent | None = field(default=None, init=False)
    turn_id: str | None = field(default=None, init=False)

    def _block(self, *lines: str) -> None:
        self.emit("\n".join(lines))

    def begin(self) -> None:
        if self.verbosity is RunVerbosity.QUIET:
            return
        self._block(
            "LOOPRAIL Run",
            f"Session  {bounded_text(self.session_key, 180)}",
            "Run      pending",
            "Task",
            f"  {bounded_text(self.task, 260)}",
        )

    def turn_submitted(self, turn_id: str | None) -> None:
        self.turn_id = turn_id
        if self.verbosity is not RunVerbosity.QUIET and turn_id:
            self.emit(f"Turn     {bounded_text(turn_id, 180)}")

    def observe(self, event: TurnEvent) -> None:
        event_conversation = getattr(event, "conversation_id", None)
        if event_conversation and event_conversation != self.session_key:
            return
        if isinstance(event, (TurnFailed, TurnEnded)):
            self.terminal = event

    def render_recovery(self, state: Any | None = None) -> None:
        state = state or self.recovery_state
        if state is None:
            return
        unknown = tuple(getattr(state, "unknown_effect_ids", ()) or ())
        if self.verbosity is RunVerbosity.QUIET:
            if unknown:
                self.emit(
                    "Recovery blocked: UNKNOWN effects "
                    + bounded_text(", ".join(map(str, unknown[:8])), 180)
                    + "; automatic replay disabled"
                )
            return
        artifact = getattr(getattr(state, "artifact_status", None), "value", None) or "unknown"
        previous = getattr(state, "last_turn_id", None)
        previous_status = getattr(state, "last_turn_status", None) or "unknown"
        journal = getattr(getattr(state, "effect_journal_status", None), "value", None) or "unknown"
        checkpoint = getattr(state, "checkpoint_id", None) or "none"
        self._block(
            "Recovery",
            f"  durable state: {bounded_text(artifact, 80)}",
            f"  previous Turn: {bounded_text(previous, 160) if previous else 'not recorded'} ({previous_status})",
            "  next Turn: NEW (fresh invocation)",
            f"  checkpoint: {bounded_text(checkpoint, 160)}",
            f"  effect journal: {bounded_text(journal, 80)}",
            f"  unknown effects: {bounded_text(', '.join(map(str, unknown[:8])) if unknown else 'none', 180)}",
            "  automatic replay: disabled",
        )

    def finish(self, outcome: Any | None = None) -> tuple[str, RunEvidence]:
        evidence = discover_run_evidence(self.turn_id, self.trace_dir or self.state_dir)
        recovery_status = _marker_status(self.recovery_dir or self.state_dir, self.session_key, self.turn_id)
        status = terminal_status(
            self.terminal,
            outcome,
            evidence=evidence,
            recovery_status=recovery_status,
        )

        if evidence.run_id and self.verbosity is not RunVerbosity.QUIET:
            self.emit(f"Run      {bounded_text(evidence.run_id, 180)}")

        if self.verbosity is not RunVerbosity.QUIET:
            context = _context_values(evidence)
            if context is not None:
                self._block(
                    "Context",
                    f"  estimated: {context['estimated']}",
                    f"  limit: {context['limit']}",
                    f"  compacted: {context['compacted']}",
                    f"  dropped: {context['dropped']}",
                )
                if self.verbosity is RunVerbosity.VERBOSE:
                    self.emit(f"  input budget: {context['budget']}")

            provider = _provider_values(evidence)
            if provider is not None:
                self._block(
                    "Provider",
                    f"  selected: {bounded_text(provider.get('provider') or 'unknown', 120)}",
                    f"  model: {bounded_text(provider.get('model') or 'unknown', 180)}",
                )

            memory = _memory_values(evidence, outcome)
            if memory is not None:
                self._block("Memory", f"  recalled: {memory['recalled']}", f"  writes: {memory['writes']}")

        category = _failure_category(evidence)
        if status in {"FAILED", "CANCELLED", "BUDGET_EXCEEDED"} and category:
            self._block("Failure", f"  category: {bounded_text(category, 160)}")

        self._block("Result", f"  {status}")
        if evidence.run_id and evidence.trace_path:
            self._block(
                "Evidence",
                f"  run: {evidence.run_id}",
                f"  trace: {bounded_text(evidence.trace_path, 260)}",
                f"  spans: {evidence.summary.get('span_count', len(evidence.records))}",
            )
        else:
            self._block("Evidence", "  run: unavailable (trace disabled or not persisted)")
        return status, evidence


__all__ = [
    "CliRunReporter",
    "RunEvidence",
    "RunVerbosity",
    "bounded_json",
    "bounded_text",
    "discover_run_evidence",
    "format_tool_complete",
    "format_tool_invocation",
    "format_tool_start",
    "parse_verbosity",
    "short_id",
    "terminal_status",
    "tool_failure_category",
]
