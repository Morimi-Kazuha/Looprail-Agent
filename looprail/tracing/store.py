"""Trace Core ``audit.span.v1`` 的 JSONL + Artifact Storage。

实现 Dependency-free，仅使用 Stdlib；从 Shared Tracing-plugin Core 复制，使 Package 在 Clean
``pip install`` 后保持 Self-contained。

State Dir Layout：

    <state_dir>/logs/audit-events.log       # 每行一个 JSON Event Record
    <state_dir>/logs/audit-spans.log        # 每行一个 JSON Span
    <state_dir>/logs/audit-artifacts/...    # SHA-1 Identified Payloads
    <state_dir>/logs/runs/<trace-id>.jsonl # Durable per-run trace evidence
    <state_dir>/logs/archive/<date>/...     # Rotated Logs

Store 以日期或大小轮换 Active Logs，Artifacts 保存 bounded Payload 与 Preview Reference，并为每个
Trace 保留独立的 inspectable JSONL run artifact。写入多为 Best-effort，Path 出现不代表 Viewer 已成功读取。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from looprail.utils.portable_lock import file_lock

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_ARTIFACT_BYTES = 32 * 1024
DEFAULT_MAX_VALUE_CHARS = 2_048
DEFAULT_MAX_COLLECTION_ITEMS = 64
DEFAULT_MAX_VALUE_DEPTH = 10
TRACE_ARTIFACT_SCHEMA = "looprail.trace.artifact.v1"
TRACE_SUMMARY_SCHEMA = "looprail.trace.summary.v1"

_KIND_FILES = {
    "events": "audit-events.log",
    "spans": "audit-spans.log",
}


def _date_key(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def to_json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview_text(value: Any, max_len: int = 400) -> str:
    text = value if isinstance(value, str) else to_json_text(value)
    if not text:
        return ""
    return text if len(text) <= max_len else f"{text[:max_len]}..."


def safe_segment(value: Any, fallback: str = "unknown") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str("" if value is None else value).strip())
    normalized = normalized.strip("-")
    return (normalized or fallback)[:80]


def _sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in {
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
        # Provider-native hidden-thinking fields are never persisted as raw
        # trace payload, even when an arbitrary caller sends them to an
        # artifact helper instead of the semantic-convention builder.
        "reasoning_content",
        "thinking_blocks",
        "chain_of_thought",
    }:
        return True
    return any(
        normalized.endswith(suffix)
        for suffix in ("_api_key", "_access_token", "_refresh_token", "_client_secret")
    )


def _bounded_value(
    value: Any,
    *,
    depth: int = 0,
    max_value_chars: int = DEFAULT_MAX_VALUE_CHARS,
    max_collection_items: int = DEFAULT_MAX_COLLECTION_ITEMS,
) -> Any:
    """Return a JSON-safe, bounded observation without changing live inputs.

    This is deliberately an observation policy, not a claim that all secrets
    are discoverable. Sensitive-shaped mapping keys are redacted, collections
    are capped, and long scalar values become preview/digest envelopes. The
    final artifact-size gate below is still authoritative for bytes on disk.
    """
    if depth >= DEFAULT_MAX_VALUE_DEPTH:
        return {"truncated": True, "reason": "max_depth"}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        items = list(value.items())
        for key, item in items[:max_collection_items]:
            key_text = str(key)
            result[key_text] = "[REDACTED]" if _sensitive_key(key_text) else _bounded_value(
                item,
                depth=depth + 1,
                max_value_chars=max_value_chars,
                max_collection_items=max_collection_items,
            )
        if len(items) > max_collection_items:
            result["_truncated_items"] = len(items) - max_collection_items
        return result
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        result = [
            _bounded_value(
                item,
                depth=depth + 1,
                max_value_chars=max_value_chars,
                max_collection_items=max_collection_items,
            )
            for item in values[:max_collection_items]
        ]
        if len(values) > max_collection_items:
            result.append({"_truncated_items": len(values) - max_collection_items})
        return result
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
        if len(text) <= max_value_chars:
            return text
        return {
            "truncated": True,
            "chars": len(text),
            "sha256": digest_text(text),
            "preview": text[:max_value_chars],
        }
    if isinstance(value, str):
        if len(value) <= max_value_chars:
            return value
        return {
            "truncated": True,
            "chars": len(value),
            "sha256": digest_text(value),
            "preview": value[:max_value_chars],
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_value(str(value), depth=depth, max_value_chars=max_value_chars)


def bound_trace_record(record: dict[str, Any]) -> dict[str, Any]:
    bounded = _bounded_value(record)
    return bounded if isinstance(bounded, dict) else {"value": bounded}


class TraceStore:
    """一个 State Dir 的 Append-only JSONL Store + Artifact Persistence。

    实例拥有 Logs/Artifacts/Archive/Run Paths 与 Maximum Active Log Bytes。Span/Event 追加在 OSError
    时静默降级；Artifact Persist 返回包含 Path/SHA-1/Bytes/Preview 的 Dict，失败则在 Dict 中显式携带
    Error。所有 JSONL 写入使用相邻 portable lock anchor；这只保护遵循该 Store 合约的本地 Writer，
    不声称能阻止绕过 Store 的外部修改。
    """

    def __init__(self, state_dir: str | os.PathLike[str], max_bytes: int | None = None) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.logs_dir = self.state_dir / "logs"
        self.artifacts_dir = self.logs_dir / "audit-artifacts"
        self.runs_dir = self.logs_dir / "runs"
        self.archive_dir = self.logs_dir / "archive"
        self.max_bytes = max_bytes or int(os.environ.get("TRACE_LOG_MAX_BYTES", DEFAULT_MAX_BYTES))
        try:
            configured_artifact_bytes = int(
                os.environ.get("TRACE_ARTIFACT_MAX_BYTES", DEFAULT_MAX_ARTIFACT_BYTES)
            )
        except (TypeError, ValueError):
            configured_artifact_bytes = DEFAULT_MAX_ARTIFACT_BYTES
        self.max_artifact_bytes = max(1_024, configured_artifact_bytes)

    # -- 路径 --------------------------------------------------------------

    def _ensure(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _active_log(self, kind: str) -> Path:
        return self._ensure(self.logs_dir) / _KIND_FILES[kind]

    def run_path(self, trace_id: str) -> Path:
        return self._ensure(self.runs_dir) / f"{safe_segment(trace_id, 'trace')}.jsonl"

    def trace_artifact_ref(self, trace_id: str) -> str:
        """Return the stable local reference used by Trial artifacts."""
        return str(self.run_path(trace_id))

    # -- 追加与轮换 --------------------------------------------------------

    def _rotate_if_needed(self, kind: str, next_text: str) -> Path:
        path = self._active_log(kind)
        if not path.exists():
            return path
        stat = path.stat()
        current_day = _date_key(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc))
        next_bytes = len(next_text.encode("utf-8"))
        rotate_by_date = current_day != _date_key()
        rotate_by_size = stat.st_size + next_bytes > self.max_bytes
        if not rotate_by_date and not rotate_by_size:
            return path
        day_dir = self._ensure(self.archive_dir / current_day)
        suffix = datetime.now(timezone.utc).strftime("%H%M%S%f")
        base = _KIND_FILES[kind].replace(".log", "")
        path.rename(day_dir / f"{base}-{current_day}-{suffix}.log")
        return path

    def append(self, kind: str, record: dict[str, Any]) -> None:
        bounded = bound_trace_record(record)
        text = f"{to_json_text(bounded)}\n"
        path = self._active_log(kind)
        with file_lock(path.with_suffix(path.suffix + ".lock")):
            path = self._rotate_if_needed(kind, text)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())

    def append_event(self, record: dict[str, Any]) -> None:
        try:
            self.append("events", record)
        except OSError:
            pass

    def append_span(self, span: dict[str, Any]) -> None:
        try:
            bounded = bound_trace_record(span)
            self.append("spans", bounded)
            trace_id = bounded.get("traceId") or bounded.get("runId")
            if isinstance(trace_id, str) and trace_id:
                run_path = self.run_path(trace_id)
                text = f"{to_json_text(bounded)}\n"
                with file_lock(run_path.with_suffix(run_path.suffix + ".lock")):
                    with run_path.open("a", encoding="utf-8") as handle:
                        handle.write(text)
                        handle.flush()
                        os.fsync(handle.fileno())
        except OSError:
            pass

    def read_trace(self, trace_id: str) -> tuple[dict[str, Any], ...]:
        """Read one durable run artifact, ignoring only malformed lines."""
        path = self.run_path(trace_id)
        if not path.exists():
            return ()
        records: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
        except OSError:
            return ()
        return tuple(records)

    def find_run_id(self, turn_id: str | None) -> str | None:
        """Find a run identity for a request when the runner returned no outcome.

        Failure/cancellation paths intentionally return no ``TurnOutcome``.
        The root span still contains the request turn identity, so benchmark
        hosts can link those trials without treating Trace as the failure
        authority.
        """
        if not isinstance(turn_id, str) or not turn_id or not self.runs_dir.exists():
            return None
        for path in sorted(self.runs_dir.glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(value, dict):
                    continue
                attrs = value.get("attributes")
                if isinstance(attrs, dict) and (
                    attrs.get("turn.id") == turn_id or attrs.get("spine.turn_id") == turn_id
                ):
                    return str(value.get("traceId") or path.stem)
        return None

    def trace_summary(self, trace_id: str) -> dict[str, Any]:
        """Return a bounded, reconstructable summary for a run artifact."""
        spans = self.read_trace(trace_id)
        references: list[dict[str, Any]] = []
        terminal: list[str] = []
        for span in spans:
            attrs = span.get("attributes") if isinstance(span.get("attributes"), dict) else {}
            outcome = attrs.get("spine.outcome") or attrs.get("terminal.state")
            if isinstance(outcome, str):
                terminal.append(outcome)
            selected_attrs = {
                key: attrs[key]
                for key in (
                    "run.id",
                    "turn.id",
                    "session.id",
                    "span.type",
                    "spine.outcome",
                    "spine.terminal_event",
                    "spine.error_class",
                    "spine.provider_error_category",
                    "tool.name",
                    "tool.call_id",
                    "tool.error",
                    "tool.failure_category",
                    "llm.provider",
                    "llm.model",
                    "llm.call_id",
                    "context.path",
                    "context.overflow_reason",
                    "memory.operation",
                    "recovery.decision",
                )
                if key in attrs
            }
            if len(references) < 512:
                references.append(
                    {
                        "span_id": span.get("spanId"),
                        "parent_span_id": span.get("parentSpanId"),
                        "name": span.get("name"),
                        "kind": span.get("kind"),
                        "start_time": span.get("startTime"),
                        "end_time": span.get("endTime"),
                        "status": span.get("status"),
                        "attributes": selected_attrs,
                    }
                )
        roots = [row["span_id"] for row in references if row["parent_span_id"] is None]
        return {
            "schema": TRACE_SUMMARY_SCHEMA,
            "run_id": trace_id,
            "trace_artifact": self.trace_artifact_ref(trace_id),
            "span_count": len(spans),
            "summary_truncated": len(spans) > len(references),
            "root_span_ids": roots,
            "terminal_outcomes": terminal[-8:],
            "spans": references,
        }

    # -- 产物 --------------------------------------------------------------

    def persist_artifact(
        self,
        kind: str,
        meta: dict[str, Any],
        payload: Any,
        *,
        label: str | None = None,
        preview_length: int = 400,
    ) -> dict[str, Any]:
        try:
            if payload is None:
                raw_text, extension = "", "json"
            elif isinstance(payload, str):
                raw_text, extension = payload, "txt"
            else:
                raw_text, extension = json.dumps(payload, ensure_ascii=False, indent=2, default=str), "json"
            bounded = _bounded_value(payload)
            if isinstance(payload, str):
                bounded_text = bounded if isinstance(bounded, str) else json.dumps(
                    bounded,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            elif payload is None:
                bounded_text = ""
            else:
                bounded_text = json.dumps(bounded, ensure_ascii=False, indent=2, default=str)
            text = bounded_text
            raw_bytes = len(raw_text.encode("utf-8"))
            if len(text.encode("utf-8")) > self.max_artifact_bytes:
                envelope = {
                    "schema": TRACE_ARTIFACT_SCHEMA,
                    "truncated": True,
                    "original_bytes": raw_bytes,
                    "original_sha256": digest_text(raw_text),
                    "value_type": type(payload).__name__,
                    "preview": preview_text(bounded_text, min(preview_length, 240)),
                }
                text = json.dumps(envelope, ensure_ascii=False, indent=2, default=str)
                if len(text.encode("utf-8")) > self.max_artifact_bytes:
                    envelope["preview"] = preview_text(bounded_text, 64)
                    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
            sha1 = hash_text(text)
            day = _date_key()
            dir_path = self._ensure(self.artifacts_dir / safe_segment(kind) / day)
            file_name = "-".join(
                [
                    datetime.now(timezone.utc).strftime("%H%M%S%f"),
                    safe_segment(meta.get("traceId") or meta.get("runId") or "trace"),
                    safe_segment(meta.get("sessionId") or meta.get("sessionKey") or "session"),
                    safe_segment(label or kind),
                    sha1[:10],
                ]
            )
            file_path = dir_path / f"{file_name}.{extension}"
            file_path.write_text(text, encoding="utf-8")
            return {
                "kind": kind,
                "path": str(file_path),
                "sha1": sha1,
                "bytes": len(text.encode("utf-8")),
                "preview": preview_text(text, min(preview_length, DEFAULT_MAX_VALUE_CHARS)),
            }
        except OSError as exc:
            return {"kind": kind, "path": None, "sha1": None, "bytes": None, "preview": "", "error": str(exc)}

    @staticmethod
    def artifact_attributes(prefix: str, artifact: dict[str, Any] | None) -> dict[str, Any]:
        if not artifact:
            return {}
        attrs = {
            f"{prefix}.artifact_path": artifact.get("path"),
            f"{prefix}.artifact_sha1": artifact.get("sha1"),
            f"{prefix}.artifact_bytes": artifact.get("bytes"),
        }
        if artifact.get("error"):
            attrs[f"{prefix}.artifact_error"] = artifact["error"]
        return attrs
