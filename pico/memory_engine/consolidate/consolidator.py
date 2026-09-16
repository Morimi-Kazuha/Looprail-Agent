"""Pico 的 Persistent Agent Memory System。

本模块管理 Two-layer User Memory：``episodes.md`` 保存可 Grep 的时间化事件，``user.md`` 保存按主题整理
的当前 Profile Snapshot。轻量 `annotate` Path 把 Conversation Chunk 变成带 Tags 的 Episode/Foresight；
重量 `refresh_section` Path 只在 Tag 足够活跃时重写一个 H2 Profile Section。`MemoryConsolidator` 再以
Prompt Token Pressure 决定何时归档 Session Tail。

核心数据流是 Session Messages → LLM Tool Call → Boundary Validation → Locked Markdown Write → Future
Context Selection。LLM 调用成功不等于结果通过边界；文件写入成功不等于内容已进入后续 Prompt；Profile
或 Foresight 也只是记忆证据，不能直接证明用户任务完成或预测成真。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import weakref
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

from loguru import logger

from pico.memory_engine.backend import (
    MEMORY_ITEM_SCHEMA,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_STATUSES,
    MEMORY_VERIFICATIONS,
    Memory,
    MemoryKind,
    MemoryScope,
    MemoryStatus,
    MemoryVerification,
    MemoryWriteResult,
    memory_content_digest,
    normalize_memory_provenance,
    normalize_memory_text,
)
from pico.tracing import semconv, trace
from pico.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain

if TYPE_CHECKING:
    from pico.providers.base import LLMProvider
    from pico.session.manager import Session, SessionManager


# ---------------------------------------------------------------------------
# Phase 5 structured Memory policy helpers
# ---------------------------------------------------------------------------


_STRUCTURED_MEMORY_FILE_NAME = "items.jsonl"
_STRUCTURED_MEMORY_MAX_TEXT = 4_096
_STRUCTURED_MEMORY_DEFAULT_TOP_K = 5
_STRUCTURED_MEMORY_MAX_TOP_K = 64
_STRUCTURED_MEMORY_DEFAULT_MAX_CHARS = 6_000
_STRUCTURED_MEMORY_MAX_CHARS = 32_000
_STRUCTURED_MEMORY_ELIGIBLE_KINDS = frozenset(
    {
        MemoryKind.PROJECT_FACT.value,
        MemoryKind.USER_PREFERENCE.value,
        MemoryKind.REPO_CONVENTION.value,
        MemoryKind.SUCCESSFUL_PROCEDURE.value,
        MemoryKind.FAILURE_LESSON.value,
        MemoryKind.ENVIRONMENT_FACT.value,
    }
)
_STRUCTURED_MEMORY_DURABLE_SCOPES = frozenset(
    {
        "project",
        "repo_local",
        "global",
        "user",
    }
)
_STRUCTURED_MEMORY_RECOVERY_MARKERS = (
    "[recovery",
    "unknown effect",
    "unknown_effect",
    "checkpoint:",
    "recovery evidence",
    "recovery_evidence",
)
_STRUCTURED_MEMORY_METADATA_KEYS = frozenset(
    {
        "category",
        "labels",
        "tags",
        "reusable",
        "explicit_durable",
        "transient",
        "one_off",
        "hypothesis",
        "recovery_only",
    }
)


def _stable_repository_identity(path: Path) -> str:
    """Derive a stable, non-guessable repository identity from a local root."""

    resolved = str(path.expanduser().resolve()).casefold()
    return "repo-" + hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def repository_identity_for_path(path: Path) -> str:
    """Public helper used by Context/Agent hosts to share one repo boundary."""

    return _stable_repository_identity(Path(path))


def _memory_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _memory_timestamp_key(value: str | None) -> float:
    parsed = _memory_timestamp(value)
    if parsed is None:
        return float("-inf")
    return parsed.timestamp()


def _memory_now_iso(now_fn: Callable[[], datetime]) -> str:
    return now_fn().isoformat()


def _bounded_memory_metadata(value: Any) -> dict[str, Any]:
    """Retain small labels only; raw ToolResult/log payloads are not item metadata."""

    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in sorted(_STRUCTURED_MEMORY_METADATA_KEYS):
        if key not in value:
            continue
        current = value[key]
        if isinstance(current, (str, int, float, bool)):
            out[key] = str(current)[:256] if isinstance(current, str) else current
        elif isinstance(current, (list, tuple)):
            out[key] = [str(item)[:64] for item in current[:16]]
    return out


def _looks_like_recovery_evidence(value: Any) -> bool:
    """Recognize Phase 3 warnings without treating ordinary history as Memory."""

    if isinstance(value, Mapping):
        if value.get("_recovery_synthetic") or value.get("recovery_only"):
            return True
        return any(_looks_like_recovery_evidence(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_looks_like_recovery_evidence(v) for v in value)
    if not isinstance(value, str):
        return False
    lowered = value.casefold().strip()
    return any(marker in lowered for marker in _STRUCTURED_MEMORY_RECOVERY_MARKERS)


def _memory_source_label(provenance: Mapping[str, Any]) -> str:
    return str(provenance.get("source") or provenance.get("source_type") or "unknown").strip()[:128]


def _memory_identity_payload(item: Memory) -> dict[str, Any]:
    provenance = normalize_memory_provenance(item.provenance)
    return {
        "scope": item.scope,
        "kind": item.kind,
        "normalized_key": item.normalized_key,
        "source": _memory_source_label(provenance),
        "content_digest": item.content_digest,
        "repo_identity": provenance.get("repo_identity"),
        "project_id": provenance.get("project_id"),
        "user_id": provenance.get("user_id"),
    }


def _memory_id(item: Memory) -> str:
    encoded = json.dumps(_memory_identity_payload(item), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "memory-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _memory_dedupe_key(item: Memory) -> tuple[str, ...]:
    provenance = normalize_memory_provenance(item.provenance)
    return (
        item.scope,
        item.kind,
        item.normalized_key or "",
        _memory_source_label(provenance),
        item.content_digest or "",
    )


def _memory_conflict_key(item: Memory) -> tuple[str, ...]:
    provenance = normalize_memory_provenance(item.provenance)
    return (
        item.scope,
        item.kind,
        item.normalized_key or "",
        provenance.get("repo_identity", ""),
        provenance.get("project_id", ""),
        provenance.get("user_id", ""),
    )


def _memory_write_metadata(item: Memory) -> dict[str, Any]:
    """Summarize a write for tests/traces without returning the claim payload."""

    return {
        "schema": MEMORY_ITEM_SCHEMA,
        "memory_id": item.memory_id,
        "kind": item.kind,
        "scope": item.scope,
        "verification": item.verification,
        "status": item.status,
        "version": item.version,
        "content_digest": item.content_digest,
        "provenance": normalize_memory_provenance(item.provenance),
    }


def _memory_eligibility(item: Memory, *, repository_identity: str) -> tuple[bool, str, dict[str, Any]]:
    """Apply the deterministic Phase 5 write gate to one canonical candidate."""

    text = (item.text or "").strip()
    if not text:
        return False, "empty_claim", {}
    if len(text) > _STRUCTURED_MEMORY_MAX_TEXT:
        return False, "claim_oversized", {"max_text": _STRUCTURED_MEMORY_MAX_TEXT}
    if item.kind not in _STRUCTURED_MEMORY_ELIGIBLE_KINDS:
        return False, "kind_not_reusable", {"kind": item.kind}
    if item.scope not in MEMORY_SCOPES:
        return False, "invalid_scope", {"scope": item.scope}
    if item.scope not in _STRUCTURED_MEMORY_DURABLE_SCOPES:
        return False, "scope_not_durable", {"scope": item.scope}
    if item.verification not in MEMORY_VERIFICATIONS:
        return False, "invalid_verification", {"verification": item.verification}
    if item.verification in {
        MemoryVerification.UNCERTAIN.value,
        MemoryVerification.DERIVED.value,
    }:
        return False, "verification_not_eligible", {"verification": item.verification}
    if item.verification == MemoryVerification.USER_PROVIDED.value and item.kind != MemoryKind.USER_PREFERENCE.value:
        return False, "user_provided_only_for_preference", {"kind": item.kind}

    provenance = normalize_memory_provenance(item.provenance)
    source_type = provenance.get("source_type", "").casefold()
    if source_type in {
        "tool_result",
        "raw_tool_result",
        "raw_tool_output",
        "tool_output",
        "reasoning",
        "chain_of_thought",
        "recovery",
        "recovery_evidence",
        "checkpoint",
    }:
        return False, "source_not_durable", {"source_type": source_type}
    if _looks_like_recovery_evidence(item.text) or _looks_like_recovery_evidence(provenance):
        return False, "recovery_evidence_not_memory", {}

    if item.scope in {"project", "repo_local"} and provenance.get("repo_identity") != repository_identity:
        return False, "wrong_repository_scope", {
            "expected_repo_identity": repository_identity,
            "item_repo_identity": provenance.get("repo_identity"),
        }
    if item.scope == "user" and not provenance.get("user_id"):
        return False, "user_scope_requires_identity", {}

    metadata = item.metadata
    if any(metadata.get(key) is True or str(metadata.get(key, "")).casefold() == "true" for key in ("transient", "one_off", "hypothesis", "recovery_only")):
        return False, "not_reusable", {}
    if metadata.get("reusable") is False or str(metadata.get("reusable", "")).casefold() == "false":
        return False, "not_reusable", {}
    if metadata.get("explicit_durable") is False:
        return False, "not_explicitly_durable", {}
    return True, "eligible", {
        "eligible": True,
        "kind": item.kind,
        "scope": item.scope,
        "verification": item.verification,
        "source_type": source_type or "manual",
    }


def _ensure_text(value: Any) -> str:
    """把 Tool-call Payload Value 规范为适合 File Storage 的 Text。

    已是字符串时原样返回，其他 JSON-compatible Value 使用 ``json.dumps(..., ensure_ascii=False)``，保留
    中文可读性。无法 JSON Serialize 的对象会抛错，由上层 Annotate Failure Path 捕获；函数不对内容做
    可信度验证或脱敏。
    """
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


_HISTORY_TS_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2})")
_HISTORY_TS_FORMATS = (
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
)


def _parse_history_paragraph_ts_ms(paragraph: str) -> int | None:
    """提取 HISTORY.md Paragraph 开头的 ``[YYYY-MM-DD HH:MM]`` Timestamp。

    同时接受日期与时间之间的 Space 或 ``T``，按 Local-time Interpretation 转成 Epoch Milliseconds。
    Paragraph 没有 Leading Stamp 或格式无法 Parse 时返回 `None`，不猜测文件 Mtime；调用方会丢弃无法
    可靠锚定时间的段落。
    """
    m = _HISTORY_TS_RE.match(paragraph)
    if not m:
        return None
    raw = m.group(1)
    for fmt in _HISTORY_TS_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return int(dt.timestamp() * 1000)
    return None


def _normalize_save_memory_args(args: Any) -> dict[str, Any] | None:
    """把 Provider Tool-call Arguments 规范为预期 Dict Shape。

    String 先按 JSON 解析；List 只接受首项为 Dict 的形式；Dict 原样返回，其他结构返回 `None`。该兼容层
    覆盖不同 Provider 对 Function Arguments 的包装差异，不验证 Dict 内业务字段，后续调用点负责检查。
    """
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None


# 拆分归并：每次触发都执行轻量的事件和预见标注；只在标签足够活跃时
# 执行重量的用户资料分节刷新。预见输出为选择开启，默认关闭以保持工具模式最小化并节省令牌。


def _build_annotate_tool(*, enable_foresight: bool) -> list[dict]:
    """构造 LLM-facing ``annotate_conversation`` Tool Definition。

    Tool 始终要求 ``episode_summary``，并详细约束单行 Timestamp、Concrete Identifiers、Content/Process
    Tags。只有 ``enable_foresight=True`` 时才加入 ``foresight_hint`` Slot 及其 Prediction/Window/
    Confidence/Source Schema；关闭时 **根本不要求模型预测**，既节省 Tokens，也缩小 Prompt 与输出面。

    返回值是 Provider Tool Schema List，不执行 Tool，也不保证 LLM 遵守规则；写回前仍有代码层 Validation。
    """
    properties: dict[str, dict] = {
        "episode_summary": {
            "type": "array",
            "description": (
                "One entry per distinct event. Each entry is a SINGLE LINE "
                "(no newlines), formatted exactly as:\n"
                "  '[YYYY-MM-DD HH:MM] <summary, <=100 chars> #tag1 #tag2'\n\n"
                "SUMMARY — must include concrete identifiers (file paths, "
                "function names, PR/issue numbers, percentages, time/size "
                "values). Generic descriptions waste a slot.\n"
                "  GOOD: 'PR #1287 merged: require_auth(scope) replaces 6 "
                "sites in api/views/+middleware/'\n"
                "  BAD:  'User worked on auth refactor'\n\n"
                "TAGS — 1-4 tags per entry, kebab-case. Two CLASSES:\n"
                "  (A) CONTENT tags — name WHAT the episode is about. "
                "Every episode MUST carry at least one content tag. "
                "Use existing slugs from the 'tags you've recently used' "
                "list (see prompt) before inventing new ones — DO NOT "
                "split one project across multiple slugs like "
                "#project-clawtrack-release / -docs / -cli; pick ONE "
                "stable slug per project. New project tags follow "
                "'#project-<work-slug>' where slug names the WORK, not "
                "the codebase. Other content tags: {#perf, #bug, "
                "#decision, #blocker, #deferred, #pivot, #pr, #review, "
                "#rfc, #design, #infra, #sql, #ml}.\n"
                "  (B) PROCESS tags — {#question, #habit, #answer} "
                "describe HOW the user is interacting, not WHAT about. "
                "They are SUFFIXES only — NEVER the primary tag. "
                "An episode tagged ONLY '#question' or ONLY '#habit' is "
                "INVALID and will be rejected. Always pair with at "
                "least one content tag.\n"
                "  AVOID '#task' entirely — it's meaningless filler.\n\n"
                "Order entries by conversation timestamp. Empty array only "
                "when the chunk produced no substantive event."
            ),
            "items": {"type": "string"},
        },
    }
    required: list[str] = ["episode_summary"]
    description = (
        "Annotate this conversation chunk for episodic memory. Produces "
        "tagged episode lines. Does NOT update the user profile — that "
        "happens separately via refresh_profile_section when tag "
        "frequency warrants a focused rewrite."
    )
    if enable_foresight:
        properties["foresight_hint"] = {
            "type": "array",
            "description": (
                "Predictions / behavioral patterns inferred from this "
                "conversation. Fill when ANY of these signals present:\n"
                "(a) User explicitly defers a task ('I'll come back to "
                "X tomorrow', 'next sprint').\n"
                "(b) A recurring pattern visible across 2+ episodes "
                "(e.g. Saturday runs across multiple weeks → predict "
                "next Saturday run; Sunday-night planning → predict "
                "next Sunday planning). Look back at the 'tags you've "
                "recently used' list — if it shows recurring habits, "
                "emit them as foresight.\n"
                "(c) User commits to a specific future action with "
                "time anchor ('I'll write RFC tomorrow', "
                "'release Monday 9am').\n"
                "(d) An upcoming dated event mentioned in conversation "
                "('birthday 5/25', 'demo next Friday', 'deadline EOM').\n"
                "Empty array ONLY if none of (a)-(d) signals present. "
                "Default lean: emit foresight when reasonable — a "
                "low-confidence prediction is more useful than no "
                "prediction. Aim for 1-3 entries per substantive "
                "annotate call."
            ),
            "items": {
                "type": "object",
                "required": [
                    "prediction",
                    "window",
                    "confidence",
                    "src_ts",
                ],
                "properties": {
                    "prediction": {"type": "string"},
                    "window": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "src_ts": {"type": "string"},
                },
            },
        }
        required.append("foresight_hint")
        description = (
            "Annotate this conversation chunk for episodic memory. "
            "Produces tagged episode lines and foresight predictions. "
            "Does NOT update the user profile — that happens separately "
            "via refresh_profile_section when tag frequency warrants a "
            "focused rewrite."
        )
    return [
        {
            "type": "function",
            "function": {
                "name": "annotate_conversation",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
    ]


_REFRESH_SECTION_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "refresh_profile_section",
            "description": (
                "Rewrite ONE H2 section of user.md given recent episodes "
                "tagged with a specific topic. Other H2 sections are left "
                "untouched by the splicer — do not include their content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section_heading": {
                        "type": "string",
                        "description": (
                            "Exact H2 heading line to replace, e.g. "
                            "'## Projects' or '## Habits'. Must include "
                            "the leading '## '. If the topic naturally fits "
                            "inside an existing H2 (e.g. tag #project-b -> "
                            "'## Projects'), use that existing heading and "
                            "structure project-specific content under H3 in "
                            "the body. Only create a new H2 if no existing "
                            "section fits."
                        ),
                    },
                    "section_body": {
                        "type": "string",
                        "description": (
                            "New markdown body for this section, NOT "
                            "including the heading line itself. Every bullet "
                            "MUST end with '[src: episodes.md @ "
                            "YYYY-MM-DD HH:MM]'. H3/H4 sub-headings are "
                            "allowed within the body."
                        ),
                    },
                },
                "required": ["section_heading", "section_body"],
            },
        },
    }
]


_EPISODE_LINE_RE = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2})\]\s+(.*?)\s*$")
_TAG_RE = re.compile(r"#([a-z][a-z0-9-]*)")


def _parse_episode_line(line: str) -> tuple[str, str, list[str]] | None:
    """把 ``episodes.md`` Line 拆成 ``(timestamp, summary, tags)``。

    只接受 ``[YYYY-MM-DD HH:MM] <summary> #tag #tag`` Shape，Timestamp 也兼容 ``T`` Separator。返回的
    Summary 会移除所有 Tag Tokens，Tags 不含前导 ``#``。不匹配返回 `None`，让统计与 Refresh 跳过
    Freeform/Corrupt Line，而不是误归类。
    """
    m = _EPISODE_LINE_RE.match(line)
    if not m:
        return None
    ts, body = m.group(1), m.group(2)
    tags = _TAG_RE.findall(body)
    summary = _TAG_RE.sub("", body).strip()
    return ts, summary, tags


# 代码层保护。
#
# 这些保护分别强制提示词已声明、但 LLM 在 30 天尺度下不能稳定遵循的规则。
# 它们保持为纯函数，便于独立单元测试，也可以脱离 MemoryStore 的其余部分进行推理。

# 存储时不带开头的 '#'，因为 ``_TAG_RE`` 解析事件行时已会移除。
_PROCESS_TAGS: frozenset[str] = frozenset({"question", "habit", "answer"})
_VALID_CONFIDENCE: frozenset[str] = frozenset({"low", "medium", "high"})
_SRC_LINK_RE = re.compile(r"\[src:\s+episodes\.md\s+@\s+\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}\]")
# 预见预测语义去重前移除的词元：不承载主题内容的常见主语、助动词和框架词。
_FORESIGHT_DEDUP_STOPWORDS: frozenset[str] = frozenset(
    {
        "user",
        "will",
        "may",
        "might",
        "likely",
        "again",
        "today",
        "tomorrow",
        "next",
        "this",
        "that",
        "the",
        "for",
        "with",
        "from",
        "and",
        "are",
        "has",
        "have",
        "continue",
        "recurring",
        "pattern",
        "habit",
    }
)
_FORESIGHT_TOKEN_RE = re.compile(r"[a-zA-Z]{4,}|[一-鿿]{2,}")
# “同一主张的不同改写”所用 Jaccard 阈值。根据实验选择 0.6，可捕获“周六跑步 x4”
# 或“每日服药提醒 x6”这类重复习惯聚类，同时不影响明显不同的预测。
_FORESIGHT_SEMANTIC_DUP_JACCARD: float = 0.6


def _stem_trailing_s(token: str) -> str:
    """执行 Cheap Plural→Singular：移除长度至少 5 且不以 ``ss`` 结尾 Token 的单个 Trailing ``s``。

    可把 ``reminders → reminder``、``meetings → meeting``、``mondays → monday`` 归一化，使 Jaccard
    捕获 Sibling-form Duplicates，而无需引入 Real Stemmer；对当前处理量，`nltk PorterStemmer` 过重。
    函数 **不** 修改 ``boss``、``class``、``-ing`` / ``-ed`` Forms。Occasional Missed Dedup 是相对
    Heavyweight Morphology + Extra Dependency 可接受的 Failure Mode。
    """
    if len(token) >= 5 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _is_process_only_episode(line: str) -> bool:
    """Episode 只有 ``#question`` / ``#habit`` / ``#answer`` Tags 时返回 `True`。

    Prompt 已声明这类 Episode **INVALID**，因为 Process Tags 必须搭配 Content Tag；但 30-day Scale 下 LLM
    仍约 5% 违规。本 Filter 在 Annotate-writeback Boundary 丢弃它们，使其不进入 ``episodes.md``，也不
    错误触发 `refresh_section`。

    Unparseable 或 Untagged Lines 返回 `False`，避免意外 Suppress 无关 Freeform Notes。该函数只判断 Tag
    Class，不评价 Summary 事实是否正确。
    """
    parsed = _parse_episode_line(line)
    if not parsed:
        return False
    _, _, tags = parsed
    if not tags:
        return False
    return {t.lower() for t in tags}.issubset(_PROCESS_TAGS)


def _normalize_confidence(value: str) -> str:
    """值属于 ``{low, medium, high}`` 时原样返回，否则返回 ``?``。

    LLM 偶尔输出 ``strong`` / ``likely`` / ``definite``，违背 Prompt-specified Enum。渲染成 ``?`` 让
    Deviation 在 ``user.md`` 中可见，而不是 Silently Persist Bad Value；函数不会猜测这些词对应哪个等级。
    """
    v = (value or "").strip().lower()
    return v if v in _VALID_CONFIDENCE else "?"


def _foresight_token_set(prediction: str) -> frozenset[str]:
    """把 Foresight Prediction Tokenize，供 Semantic-dedup Comparison。

    提取长度至少 4 的 English Words 与长度至少 2 的 CJK Runs，Lowercase 后移除 ``user will``、
    ``recurring habit`` 等不承载 Topic Content 的 High-frequency Framing Words。剩余 Token 再经
    `_stem_trailing_s`，使 ``reminders``/``reminder``、``meetings``/``meeting`` 等 Plural/Singular
    Siblings Collapse 到同一形式。返回 Frozen Set 只用于近似比较，不是语言学分词结果。
    """
    raw = _FORESIGHT_TOKEN_RE.findall(prediction.lower())
    return frozenset(_stem_trailing_s(t) for t in raw if t not in _FORESIGHT_DEDUP_STOPWORDS)


def _is_semantic_duplicate_foresight(
    new_pred: str,
    existing_preds: list[str],
) -> bool:
    """``new_pred`` 与任一 Existing Prediction 的 Content-token Jaccard 达到 Threshold 时返回 `True`。

    `append_foresight` 中 ``(prediction, src_ts)`` Dedup 只比较 Exact String，会放过同一 Semantic Claim 的
    Reworded Re-emissions，例如 ``User runs every Saturday morning`` 与追加 ``recurring habit`` 的版本。
    本 Jaccard Check 捕获这些改写。Empty Token Set 不判重复；阈值是近似 Trade-off，可能存在少量 False
    Positive/Negative。
    """
    new_tokens = _foresight_token_set(new_pred)
    if not new_tokens:
        return False
    for ex in existing_preds:
        ex_tokens = _foresight_token_set(ex)
        if not ex_tokens:
            continue
        union = new_tokens | ex_tokens
        jaccard = len(new_tokens & ex_tokens) / len(union)
        if jaccard >= _FORESIGHT_SEMANTIC_DUP_JACCARD:
            return True
    return False


def _drop_bullets_without_src(body: str) -> tuple[str, int]:
    """移除缺少 ``[src: episodes.md @ ts]`` Link 的 Profile Bullets。

    Non-bullet Lines，包括 Blank、Headings、Prose，Verbatim 保留。Prompt 要求每条 Profile Bullet 引用
    Source Episode Timestamp，因此没有 Evidence Link 的 Bullet 会在写回边界丢弃。

    Returns ``(cleaned_body, n_dropped)``。该检查验证 Citation Shape，不验证被引用 Episode 是否真的支持
    Claim；后者仍需人工或更强 Evidence Review。
    """
    kept: list[str] = []
    dropped = 0
    for line in body.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("- "):
            kept.append(line)
            continue
        if _SRC_LINK_RE.search(line):
            kept.append(line)
        else:
            dropped += 1
    return "\n".join(kept), dropped

    # 预见预测持久化到 user.md 的 ## Foresight 分节。条目使用单行格式，
    # 与用户资料条目使用的 [src:] 式证据链接相呼应：
    #   - <预测>（生成时间 <gen_ts>，窗口 <range>，置信度 <level>，来源 episodes.md @ <ep_ts>）
    # ``from`` 是 annotate() 输出该预测时的墙上时间；``src`` 是触发它的事件时间戳，
    # 由 LLM 以 src_ts 提供。


_FORESIGHT_HEADING = "## Foresight"
_FORESIGHT_BULLET_RE = re.compile(
    r"^-\s+(?P<prediction>.+?)\s+"
    r"\(from\s+(?P<gen_ts>[^,]+),\s+"
    r"window:\s+(?P<window>[^,]+),\s+"
    r"confidence:\s+(?P<confidence>[^,]+),\s+"
    r"src:\s+episodes\.md\s+@\s+(?P<src_ts>.+?)\)\s*$"
)


def _format_foresight_bullet(entry: dict[str, Any], generation_ts: str) -> str:
    """把 Foresight Dict 渲染成一条 ``user.md`` Bullet Line。

    Prediction、Window、Confidence、Source Timestamp 与 `generation_ts` 都进入固定单行格式。Missing/Blank
    Fields 用 ``?`` 替代，使 Partial LLM Output 仍能写成可人工 Review 的 Record；非法 Confidence 也经
    `_normalize_confidence` 显式标记，而不是伪造有效值。
    """
    pred = (entry.get("prediction") or "").strip() or "?"
    window = (entry.get("window") or "").strip() or "?"
    # 强制限定为 {low|medium|high}，枚举外的值渲染为 '?'。
    confidence = _normalize_confidence(entry.get("confidence") or "")
    src_ts = (entry.get("src_ts") or "").strip() or "?"
    return f"- {pred} (from {generation_ts}, window: {window}, confidence: {confidence}, src: episodes.md @ {src_ts})"


_RELEVANCE_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)


def _tokenize_for_relevance(text: str) -> set[str]:
    """返回 Lowercased、长度至少 2 的 Alphanumeric/CJK Runs Set。

    这是 Proper Tokenization 的 Lightweight Stand-in：Chinese Run 会成为一个 Multi-char Token，例如三字
    Phrase 是 One Token；English 按 Whitespace + Punctuation 分割。它服务 Profile Section Lexical
    Relevance，不理解同义词、词形或语义。
    """
    return {t.lower() for t in _RELEVANCE_TOKEN_RE.findall(text)}


def _parse_user_md_sections(content: str) -> dict[str, str]:
    """把 ``content`` 中每个 H2 Section 解析为 ``{H2_heading_line: body}``。

    First H2 之前的 H1 Preamble 被 Drop；Body 是 Heading 后到 Next H2 或 EOF 之间的文本，移除首尾 Blank
    Lines。Dict Insertion Order 与 Source File 一致，供后续自然渲染。重复 H2 Heading 会由后出现者覆盖，
    当前格式约定要求 Heading 唯一。
    """
    lines = content.splitlines()
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip("\n")
            current = line.strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip("\n")
    return sections


def _score_section_relevance(query: str, heading: str, body: str) -> float:
    """计算 Lexical-overlap Relevance，即 Query Vocabulary 在 Heading/Body 中的重合量。

    Heading Hit 权重为 Body 的 3x，因为 Section Titles 短且 Intentional；例如询问 ``Projects`` 应可靠拉取
    ``## Projects``。空 Query Tokens 返回 0。分数只用于 Profile Section Selection，不是 Memory Fact 的
    真实性或任务相关性概率。
    """
    q_tokens = _tokenize_for_relevance(query)
    if not q_tokens:
        return 0.0
    heading_hits = len(q_tokens & _tokenize_for_relevance(heading))
    body_hits = len(q_tokens & _tokenize_for_relevance(body))
    return heading_hits * 3.0 + body_hits


def _splice_h2_section_at_end(content: str, heading: str, new_body: str) -> str:
    """类似 ``_splice_h2_section``，但保证 Named Section 位于 File **End**。

    Section 不存在时像 Fallback Path 一样 Append；已在任意位置存在时，先移除原 Section Range，再用
    `new_body` 追加到末尾。H1 Preamble 与其他 H2 顺序保持不变。

    `append_foresight` 用它让 Auto-managed ``## Foresight`` Pillar 始终位于 ``user.md`` 底部，不受
    `refresh_section` 后续追加 ``## Projects`` / ``## Habits`` 等影响。
    """
    lines = content.splitlines()
    target = heading.strip()

    h_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == target:
            h_idx = i
            break

    if h_idx is not None:
        # 查找下一个 H2 边界或 EOF，以确定当前分节范围。
        next_h2 = None
        for i in range(h_idx + 1, len(lines)):
            if lines[i].startswith("## "):
                next_h2 = i
                break
        if next_h2 is not None:
            lines = lines[:h_idx] + lines[next_h2:]
        else:
            lines = lines[:h_idx]

    content_without = "\n".join(lines).rstrip("\n")
    body = new_body.strip("\n")
    if not content_without:
        return f"{target}\n\n{body}\n"
    return f"{content_without}\n\n{target}\n\n{body}\n"


def _ensure_foresight_at_end(content: str) -> str:
    """``## Foresight`` 存在但不是 Last H2 时，把它移动到末尾。

    操作 Idempotent：Foresight Absent 或 Already Last 时原样返回 `content`。`refresh_section` Writer 在任何
    Non-Foresight H2 Splice 后调用，使 Auto-managed Pillar 不会被新 Append 的 ``## Projects`` /
    ``## Habits`` 视觉掩埋。移动只改变 Section Position，不改写其 Body。
    """
    sections = _parse_user_md_sections(content)
    if _FORESIGHT_HEADING not in sections:
        return content
    h2_order = list(sections.keys())
    if h2_order[-1] == _FORESIGHT_HEADING:
        return content
    body = sections[_FORESIGHT_HEADING]
    return _splice_h2_section_at_end(content, _FORESIGHT_HEADING, body)


def _splice_h2_section(content: str, heading: str, new_body: str) -> str:
    """复制 ``content``，并把 ``heading`` 标识的 H2 Body 替换成 ``new_body``。

    Body 范围从 Heading 下一行到 Next H2 之前；若它是 Last H2 则到 EOF。H1 Preamble 与其他 H2 Sections
    Byte-for-byte 保留。Heading 不存在时，把 ``heading`` + ``new_body`` 作为 Fresh Section Append 到文件
    末尾。函数只处理 Exact H2 Line，不把 ``###`` 等更深 Heading 当边界。
    """
    lines = content.splitlines()
    target = heading.strip()

    h_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == target:
            h_idx = i
            break

    if h_idx is None:
        sep = "\n\n" if content and not content.endswith("\n\n") else ""
        return content.rstrip("\n") + sep + "\n" + target + "\n\n" + new_body.strip("\n") + "\n"

    # 查找下一个 H2：以 "## " 开头、但不是 "### " 等更深层级的行。
    next_h2 = None
    for i in range(h_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            next_h2 = i
            break

    before = lines[: h_idx + 1]
    after = lines[next_h2:] if next_h2 is not None else []
    body_lines = new_body.strip("\n").splitlines()

    pieces = list(before) + [""] + body_lines + [""]
    if after:
        pieces.extend(after)
    return "\n".join(pieces).rstrip("\n") + "\n"


class MemoryStore:
    """拥有 Markdown Profile/Episode 与 Structured Item sidecar 的唯一 Memory Store。

    旧名称 ``MEMORY.md`` / ``HISTORY.md`` 的职责分别迁移到 Workspace ``user_memory/profile/user.md`` 与
    ``user_memory/episodic/episodes.md``。Store 负责 Locked Profile Writes、Episode Append、Foresight、Tag
    Offsets、Section Selection 与 LLM Annotation/Refresh；文件路径和写入锁由实例持有。

    Phase 5 的 ``items.jsonl`` 仍由这个 Store 负责，不是第二个 Store 或 Retrieval Service；它保存
    带 schema、scope、provenance、verification 与 lifecycle status 的可复用条目。结构化写入通过
    ``write`` 的确定性资格门，检索通过 ``recall_memory`` 的 bounded lexical path；Markdown Profile
    与 Episode 接口保持兼容。

    生命周期与 Workspace 一致，没有显式 Start/Stop。多进程修改 `user.md` 或结构化 sidecar 必须使用
    `locked`；Episode Append 当前是直接追加。Store 写入成功不代表下次 Context 一定选择该 Section。
    """

    def __init__(
        self,
        workspace: Path,
        now_fn: Callable[[], datetime] | None = None,
        *,
        repository_id: str | None = None,
        project_id: str | None = None,
        user_id: str = "default",
        repository_root: Path | None = None,
    ):
        self.workspace = Path(workspace)
        self.repository_root = Path(repository_root or workspace)
        # 用户资料和事件日志位于 ``user_memory`` 支柱下。``memory_dir`` 是
        # ``memory_file.parent`` 的别名，供需推导同级路径的调用点使用，如下方的锁文件。
        self.memory_file = ensure_dir(self.workspace / "user_memory" / "profile") / "user.md"
        self.history_file = ensure_dir(self.workspace / "user_memory" / "episodic") / "episodes.md"
        self.memory_dir = self.memory_file.parent
        # 写入 user.md 的各进程共享同级锁文件。
        self.memory_lock_path = self.memory_file.with_suffix(self.memory_file.suffix + ".lock")

        # Phase 5 structured items live under the same existing MemoryStore
        # authority.  This is a sidecar representation of the Store, not a
        # second retrieval service; Markdown profile/episodes remain intact.
        self.structured_dir = ensure_dir(self.workspace / "user_memory" / "structured")
        self.structured_file = self.structured_dir / _STRUCTURED_MEMORY_FILE_NAME
        self.repository_identity = str(repository_id or _stable_repository_identity(self.repository_root))[:512]
        self.project_id = str(project_id).strip()[:256] if project_id else None
        self.user_id = str(user_id or "default")[:256]
        self._last_memory_diagnostics: dict[str, Any] = {}
        self._last_memory_write: MemoryWriteResult | None = None

        # ``consolidate`` 用它向归并 LLM 提示词注入 ``Current Time:``。如果没有它，
        # 即使 LLM 看到的会话项已使用伪时钟标记，其摘要段落时间戳仍会回退到墙上时间。
        self._now_fn = now_fn or datetime.now

    @property
    def memory_items_file(self) -> Path:
        """Stable diagnostic alias for the structured item file."""

        return self.structured_file

    @property
    def last_memory_diagnostics(self) -> dict[str, Any]:
        """Bounded diagnostics from the most recent structured recall."""

        return dict(self._last_memory_diagnostics)

    @property
    def last_memory_write(self) -> MemoryWriteResult | None:
        return self._last_memory_write

    @contextmanager
    def locked(self) -> Iterator[None]:
        """持有 Profile File 的 Exclusive Lock，防止 REPL + Gateway Concurrent Writers 互相覆盖。

        历史说明称为 ``fcntl`` / ``MEMORY.md`` Lock；当前通过 Cross-platform `portalocker` 保护 ``user.md``，
        Windows 也真实串行化。Usage：

            with memory.locked():
                cur = memory.read_long_term()
                memory.write_long_term(cur + "...")

        Context 只提供互斥，不自动 Read-modify-write；Caller 必须把整个组合操作放在 Block 内。
        """
        yield from self._fcntl_locked(self.memory_lock_path)

    def _fcntl_locked(self, lock_path: Path) -> Iterator[None]:
        # 跨平台建议锁（portalocker）在 Windows 上也能实现真正串行化，取代之前的 win32 空操作；
        # 旧实现会丢失对 user.md 的并发写入。
        from pico.utils.portable_lock import file_lock

        with file_lock(lock_path):
            yield

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Phase 5 structured item lifecycle
    # ------------------------------------------------------------------

    def _canonical_memory_item(
        self,
        item: Memory | Mapping[str, Any] | str | None,
        *,
        text: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        verification: str | None = None,
        confidence: str | None = None,
        normalized_key: str | None = None,
        provenance: Mapping[str, Any] | Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        source: str | None = None,
        source_type: str | None = None,
        tool_name: str | None = None,
        result_ref: str | None = None,
        observed_at: str | None = None,
        source_path: str | None = None,
        source_digest: str | None = None,
        evidence_digest: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        repo_identity: str | None = None,
    ) -> Memory:
        if isinstance(item, Memory):
            data: dict[str, Any] = {
                "text": item.text,
                "score": item.score,
                "metadata": dict(item.metadata),
                "kind": item.kind,
                "scope": item.scope,
                "verification": item.verification,
                "confidence": item.confidence,
                "status": item.status,
                "memory_id": item.memory_id,
                "normalized_key": item.normalized_key,
                "content_digest": item.content_digest,
                "provenance": dict(item.provenance),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "version": item.version,
                "supersedes": item.supersedes,
                "superseded_by": item.superseded_by,
                "invalidated_reason": item.invalidated_reason,
            }
        elif isinstance(item, Mapping):
            data = dict(item)
        elif isinstance(item, str):
            data = {"text": item}
        elif item is None:
            data = {}
        else:
            raise TypeError("memory item must be Memory, mapping, string, or None")

        if text is not None:
            data["text"] = text
        if kind is not None:
            data["kind"] = kind
        if scope is not None:
            data["scope"] = scope
        if verification is not None:
            data["verification"] = verification
        if confidence is not None:
            data["confidence"] = confidence
        if normalized_key is not None:
            data["normalized_key"] = normalized_key
        if metadata is not None:
            data["metadata"] = dict(metadata)

        raw_provenance = data.get("provenance")
        if isinstance(raw_provenance, Mapping):
            prov: dict[str, Any] = dict(raw_provenance)
        elif hasattr(raw_provenance, "to_dict"):
            prov = dict(raw_provenance.to_dict())
        else:
            prov = {}
        if provenance is not None:
            if isinstance(provenance, Mapping):
                prov.update(dict(provenance))
            elif hasattr(provenance, "to_dict"):
                prov.update(dict(provenance.to_dict()))
        for key, value in {
            "session_id": session_id,
            "turn_id": turn_id,
            "source": source,
            "source_type": source_type,
            "tool_name": tool_name,
            "result_ref": result_ref,
            "observed_at": observed_at,
            "source_path": source_path,
            "source_digest": source_digest,
            "evidence_digest": evidence_digest,
            "project_id": project_id,
            "user_id": user_id,
            "repo_identity": repo_identity,
        }.items():
            if value is not None:
                prov[key] = value

        now = _memory_now_iso(self._now_fn)
        normalized_kind = str(data.get("kind") or MemoryKind.UNCLASSIFIED.value).strip().casefold()
        normalized_scope = str(data.get("scope") or MemoryScope.TURN_LOCAL.value).strip().casefold().replace("-", "_")
        normalized_verification = (
            str(data.get("verification") or MemoryVerification.UNCERTAIN.value).strip().casefold().replace("-", "_")
        )
        normalized_confidence = str(data.get("confidence") or "unknown").strip().casefold()[:64]
        normalized_status = str(data.get("status") or MemoryStatus.ACTIVE.value).strip().casefold()

        # These defaults make provenance explicit for manually submitted facts,
        # while preserving a caller-supplied repository/user boundary.
        prov.setdefault("source", "runtime")
        prov.setdefault("source_type", "manual")
        prov.setdefault("observed_at", data.get("updated_at") or now)
        if normalized_scope in {"project", "repo_local"}:
            prov.setdefault("repo_identity", self.repository_identity)
        if normalized_scope == "project":
            prov.setdefault("project_id", self.project_id or prov.get("repo_identity"))
        if normalized_scope == "user":
            prov.setdefault("user_id", self.user_id)
        prov = normalize_memory_provenance(prov)

        claim = str(data.get("text") or "")
        key = normalize_memory_text(data.get("normalized_key") or claim)
        try:
            score = float(data.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            version = int(data.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1

        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        for timestamp_name, timestamp_value in (("created_at", created_at), ("updated_at", updated_at)):
            if timestamp_value is not None and (
                not isinstance(timestamp_value, str) or _memory_timestamp(timestamp_value) is None
            ):
                raise ValueError(f"{timestamp_name} must be an ISO timestamp")

        return Memory(
            text=claim,
            score=score,
            metadata=_bounded_memory_metadata(data.get("metadata")),
            kind=normalized_kind,
            scope=normalized_scope,
            verification=normalized_verification,
            confidence=normalized_confidence,
            status=normalized_status,
            memory_id=data.get("memory_id") if isinstance(data.get("memory_id"), str) else None,
            normalized_key=key,
            content_digest=memory_content_digest(claim),
            provenance=prov,
            created_at=created_at if isinstance(created_at, str) else now,
            updated_at=updated_at if isinstance(updated_at, str) else now,
            version=version,
            supersedes=data.get("supersedes") if isinstance(data.get("supersedes"), str) else None,
            superseded_by=data.get("superseded_by") if isinstance(data.get("superseded_by"), str) else None,
            invalidated_reason=(
                str(data.get("invalidated_reason"))[:512] if data.get("invalidated_reason") is not None else None
            ),
        )

    @staticmethod
    def _decode_structured_memory_record(record: Any) -> Memory:
        if not isinstance(record, dict) or record.get("schema") != MEMORY_ITEM_SCHEMA:
            raise ValueError("invalid memory item schema")
        required = ("text", "kind", "scope", "verification", "status", "memory_id", "normalized_key")
        if any(key not in record for key in required):
            raise ValueError("memory item is missing required fields")
        text = record.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > _STRUCTURED_MEMORY_MAX_TEXT:
            raise ValueError("memory item text is invalid")
        fields = {
            "kind": MEMORY_KINDS,
            "scope": MEMORY_SCOPES,
            "verification": MEMORY_VERIFICATIONS,
            "status": MEMORY_STATUSES,
        }
        for key, choices in fields.items():
            if not isinstance(record.get(key), str) or record[key] not in choices:
                raise ValueError(f"memory item {key} is invalid")
        if not isinstance(record.get("memory_id"), str) or not record["memory_id"]:
            raise ValueError("memory item id is invalid")
        if not isinstance(record.get("normalized_key"), str) or not record["normalized_key"]:
            raise ValueError("memory item normalized key is invalid")
        if record.get("content_digest") != memory_content_digest(text):
            raise ValueError("memory item content digest is invalid")
        provenance = record.get("provenance", {})
        if not isinstance(provenance, dict):
            raise ValueError("memory item provenance is invalid")
        version = record.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("memory item version is invalid")
        for key in ("created_at", "updated_at"):
            if record.get(key) is not None and _memory_timestamp(record.get(key)) is None:
                raise ValueError(f"memory item {key} is invalid")
        metadata = record.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("memory item metadata is invalid")
        return Memory(
            text=text,
            kind=record["kind"],
            scope=record["scope"],
            verification=record["verification"],
            confidence=record.get("confidence", "unknown"),
            status=record["status"],
            metadata=_bounded_memory_metadata(metadata),
            memory_id=record["memory_id"],
            normalized_key=record["normalized_key"],
            content_digest=record["content_digest"],
            provenance=normalize_memory_provenance(provenance),
            created_at=record.get("created_at"),
            updated_at=record.get("updated_at"),
            version=version,
            supersedes=record.get("supersedes"),
            superseded_by=record.get("superseded_by"),
            invalidated_reason=record.get("invalidated_reason"),
        )

    def _read_structured_memory_items(self) -> tuple[list[Memory], list[str]]:
        if not self.structured_file.exists():
            return [], []
        try:
            raw = self.structured_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return [], ["storage_unavailable"]
        items: list[Memory] = []
        errors: list[str] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                items.append(self._decode_structured_memory_record(record))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"line_{line_number}:{str(exc)[:120]}")
        return items, errors

    def _write_structured_memory_items(self, items: list[Memory]) -> None:
        self.structured_file.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            json.dumps(item.structured_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in items
        )
        if payload:
            payload += "\n"
        temporary = self.structured_file.with_name(self.structured_file.name + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.structured_file)
        finally:
            temporary.unlink(missing_ok=True)

    @trace.instrument("memory.write", extract=semconv.memory_write)
    def write(
        self,
        item: Memory | Mapping[str, Any] | str | None = None,
        *,
        text: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        verification: str | None = None,
        confidence: str | None = None,
        normalized_key: str | None = None,
        provenance: Mapping[str, Any] | Any | None = None,
        metadata: Mapping[str, Any] | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        source: str | None = None,
        source_type: str | None = None,
        tool_name: str | None = None,
        result_ref: str | None = None,
        observed_at: str | None = None,
        source_path: str | None = None,
        source_digest: str | None = None,
        evidence_digest: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        repo_identity: str | None = None,
    ) -> MemoryWriteResult:
        """Apply the explicit write policy and persist one structured item.

        The operation is idempotent for the deterministic tuple
        ``scope/kind/normalized_key/source/content_digest``.  A later
        eligible claim with the same conflict key supersedes the active older
        version.  No LLM extraction or semantic similarity is performed.
        """

        try:
            canonical = self._canonical_memory_item(
                item,
                text=text,
                kind=kind,
                scope=scope,
                verification=verification,
                confidence=confidence,
                normalized_key=normalized_key,
                provenance=provenance,
                metadata=metadata,
                session_id=session_id,
                turn_id=turn_id,
                source=source,
                source_type=source_type,
                tool_name=tool_name,
                result_ref=result_ref,
                observed_at=observed_at,
                source_path=source_path,
                source_digest=source_digest,
                evidence_digest=evidence_digest,
                project_id=project_id,
                user_id=user_id,
                repo_identity=repo_identity,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            result = MemoryWriteResult(False, "rejected", "invalid_item", diagnostics={"error": str(exc)[:160]})
            self._last_memory_write = result
            return result

        raw_metadata = item.metadata if isinstance(item, Memory) else item.get("metadata", {}) if isinstance(item, Mapping) else metadata
        raw_provenance = (
            item.provenance
            if isinstance(item, Memory)
            else item.get("provenance", {})
            if isinstance(item, Mapping)
            else provenance
        )
        if _looks_like_recovery_evidence(raw_metadata) or _looks_like_recovery_evidence(raw_provenance):
            canonical = replace(canonical, metadata={**canonical.metadata, "recovery_only": True})
        eligible, reason, policy = _memory_eligibility(
            canonical,
            repository_identity=self.repository_identity,
        )
        if not eligible:
            result = MemoryWriteResult(False, "rejected", reason, diagnostics={"eligible": False, **policy})
            self._last_memory_write = result
            return result

        canonical = replace(
            canonical,
            memory_id=_memory_id(canonical),
            status=MemoryStatus.ACTIVE.value,
            version=max(1, canonical.version),
        )

        try:
            with self.locked():
                items, errors = self._read_structured_memory_items()
                if errors:
                    result = MemoryWriteResult(
                        False,
                        "error",
                        "store_corrupt" if any(error.startswith("line_") for error in errors) else "storage_unavailable",
                        diagnostics={"errors": errors[:16]},
                    )
                    self._last_memory_write = result
                    return result

                duplicate = next(
                    (
                        existing
                        for existing in items
                        if _memory_dedupe_key(existing) == _memory_dedupe_key(canonical)
                        and existing.status == MemoryStatus.ACTIVE.value
                    ),
                    None,
                )
                if duplicate is not None:
                    result = MemoryWriteResult(
                        True,
                        "duplicate",
                        "duplicate_exact",
                        item=duplicate,
                        duplicate_of=duplicate.memory_id,
                        diagnostics={
                            "eligible": True,
                            "deduplicated": True,
                            "dedupe": "scope_kind_key_source_digest",
                        },
                    )
                    self._last_memory_write = result
                    return result

                conflicts = [
                    existing
                    for existing in items
                    if existing.status == MemoryStatus.ACTIVE.value
                    and _memory_conflict_key(existing) == _memory_conflict_key(canonical)
                    and existing.content_digest != canonical.content_digest
                ]
                if not conflicts:
                    inactive_duplicate = next(
                        (
                            existing
                            for existing in items
                            if _memory_dedupe_key(existing) == _memory_dedupe_key(canonical)
                        ),
                        None,
                    )
                    if inactive_duplicate is not None:
                        result = MemoryWriteResult(
                            True,
                            "duplicate",
                            "duplicate_inactive",
                            item=inactive_duplicate,
                            duplicate_of=inactive_duplicate.memory_id,
                            diagnostics={
                                "eligible": True,
                                "deduplicated": True,
                                "dedupe": "scope_kind_key_source_digest",
                                "existing_status": inactive_duplicate.status,
                            },
                        )
                        self._last_memory_write = result
                        return result
                if conflicts:
                    latest_version = max(existing.version for existing in conflicts)
                    canonical = replace(
                        canonical,
                        version=max(canonical.version, latest_version + 1),
                        supersedes=max(conflicts, key=lambda entry: (_memory_timestamp_key(entry.updated_at), entry.memory_id or "")).memory_id,
                    )
                superseded_ids = tuple(existing.memory_id for existing in conflicts if existing.memory_id)
                if conflicts:
                    superseded_by = canonical.memory_id
                    items = [
                        replace(
                            existing,
                            status=MemoryStatus.SUPERSEDED.value,
                            superseded_by=superseded_by,
                            updated_at=canonical.updated_at,
                        )
                        if existing in conflicts
                        else existing
                        for existing in items
                    ]
                items.append(canonical)
                self._write_structured_memory_items(items)
        except OSError as exc:
            result = MemoryWriteResult(False, "error", "storage_unavailable", diagnostics={"error": str(exc)[:160]})
            self._last_memory_write = result
            return result

        result = MemoryWriteResult(
            True,
            "superseded" if superseded_ids else "inserted",
            "accepted",
            item=canonical,
            superseded_ids=superseded_ids,
            diagnostics={
                **policy,
                "deduplicated": False,
                "superseded": bool(superseded_ids),
            },
        )
        self._last_memory_write = result
        return result

    def write_item(self, item: Memory | Mapping[str, Any] | str | None = None, **kwargs: Any) -> MemoryWriteResult:
        """Named compatibility alias for :meth:`write`."""

        return self.write(item, **kwargs)

    def write_memory(self, item: Memory | Mapping[str, Any] | str | None = None, **kwargs: Any) -> MemoryWriteResult:
        """Named compatibility alias for callers using the lifecycle vocabulary."""

        return self.write(item, **kwargs)

    def update_memory(
        self,
        memory_id: str,
        item: Memory | Mapping[str, Any] | str | None = None,
        **kwargs: Any,
    ) -> MemoryWriteResult:
        """Update one item through the same policy/supersession path as ``write``.

        Updates are intentionally not in-place mutations: the target's
        normalized claim key and provenance are inherited, then ``write``
        creates a new version and marks the old active item superseded when
        the content changes.  This keeps the lifecycle small and avoids an
        event-sourcing log or a second update store.
        """

        if not isinstance(memory_id, str) or not memory_id.strip():
            result = MemoryWriteResult(False, "rejected", "invalid_memory_id")
            self._last_memory_write = result
            return result
        items, errors = self._read_structured_memory_items()
        if errors:
            result = MemoryWriteResult(False, "error", "store_corrupt", diagnostics={"errors": errors[:16]})
            self._last_memory_write = result
            return result
        target = next((entry for entry in items if entry.memory_id == memory_id), None)
        if target is None:
            result = MemoryWriteResult(False, "rejected", "memory_not_found")
            self._last_memory_write = result
            return result

        payload: dict[str, Any] = {
            "text": target.text,
            "kind": target.kind,
            "scope": target.scope,
            "verification": target.verification,
            "confidence": target.confidence,
            "normalized_key": target.normalized_key,
            "metadata": dict(target.metadata),
            "provenance": dict(target.provenance),
        }
        if isinstance(item, Memory):
            payload.update(
                {
                    "text": item.text,
                    "kind": item.kind,
                    "scope": item.scope,
                    "verification": item.verification,
                    "confidence": item.confidence,
                    "metadata": dict(item.metadata),
                    "provenance": {**target.provenance, **item.provenance},
                }
            )
            if item.normalized_key:
                payload["normalized_key"] = item.normalized_key
        elif isinstance(item, Mapping):
            payload.update(dict(item))
            replacement_provenance = item.get("provenance")
            if isinstance(replacement_provenance, Mapping):
                replacement_provenance = dict(replacement_provenance)
            elif hasattr(replacement_provenance, "to_dict"):
                replacement_provenance = dict(replacement_provenance.to_dict())
            else:
                replacement_provenance = {}
            payload["provenance"] = {**target.provenance, **replacement_provenance}
        elif isinstance(item, str):
            payload["text"] = item
        elif item is not None:
            result = MemoryWriteResult(False, "rejected", "invalid_item")
            self._last_memory_write = result
            return result
        payload.update(kwargs)
        payload.pop("memory_id", None)
        payload.pop("schema", None)
        return self.write(payload)

    def update(self, memory_id: str, item: Memory | Mapping[str, Any] | str | None = None, **kwargs: Any) -> MemoryWriteResult:
        """Short compatibility alias for :meth:`update_memory`."""

        return self.update_memory(memory_id, item, **kwargs)

    def supersede_memory(
        self,
        memory_id: str,
        item: Memory | Mapping[str, Any] | str,
        **kwargs: Any,
    ) -> MemoryWriteResult:
        """Explicitly request a replacement; conflict handling remains Runtime-owned."""

        return self.update_memory(memory_id, item, **kwargs)

    def supersede(self, memory_id: str, item: Memory | Mapping[str, Any] | str, **kwargs: Any) -> MemoryWriteResult:
        return self.supersede_memory(memory_id, item, **kwargs)

    def read_memory_items(self, *, include_inactive: bool = True) -> list[Memory]:
        """Read valid structured items; malformed records fail closed and are skipped."""

        items, errors = self._read_structured_memory_items()
        if errors:
            self._last_memory_diagnostics = {"read_errors": errors[:16]}
        if include_inactive:
            return items
        return [item for item in items if item.status == MemoryStatus.ACTIVE.value]

    def list_memory_items(self, *, include_inactive: bool = True) -> list[Memory]:
        return self.read_memory_items(include_inactive=include_inactive)

    def _memory_scope_reason(
        self,
        item: Memory,
        *,
        scope: str | None,
        repo_identity: str,
        project_id: str | None,
        user_id: str | None,
    ) -> str | None:
        if scope is not None and item.scope != scope:
            return "scope_filter"
        provenance = normalize_memory_provenance(item.provenance)
        if item.scope in {"project", "repo_local"} and provenance.get("repo_identity") != repo_identity:
            return "wrong_scope"
        if item.scope == "project":
            effective_project = project_id if project_id is not None else self.project_id or repo_identity
            if provenance.get("project_id") != effective_project:
                return "wrong_scope"
        if item.scope == "user" and provenance.get("user_id") != (user_id or self.user_id):
            return "wrong_scope"
        return None

    def _memory_stale_reason(self, item: Memory) -> str | None:
        if item.status != MemoryStatus.ACTIVE.value:
            return item.status
        provenance = normalize_memory_provenance(item.provenance)
        source_path = provenance.get("source_path")
        if source_path:
            candidate = Path(source_path)
            resolved = candidate if candidate.is_absolute() else self.repository_root / candidate
            if not resolved.exists():
                return "source_missing"
        return None

    @trace.instrument("memory.recall", extract=semconv.memory_recall)
    def recall_memory(
        self,
        query: str = "",
        *,
        scope: str | None = None,
        repo_identity: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        top_k: int = _STRUCTURED_MEMORY_DEFAULT_TOP_K,
        max_chars: int = _STRUCTURED_MEMORY_DEFAULT_MAX_CHARS,
    ) -> list[Memory]:
        """Return deterministic, bounded, lexical structured Memory hits."""

        query_text = str(query or "")
        requested_scope = scope.casefold().replace("-", "_") if isinstance(scope, str) else None
        try:
            requested_top_k = int(top_k or 0)
            requested_max_chars = int(max_chars or 0)
        except (TypeError, ValueError, OverflowError):
            diagnostics = {
                "query": query_text[:300],
                "scope": requested_scope or "all",
                "backend": "local_structured_store",
                "repo_identity": (repo_identity or self.repository_identity)[:512],
                "top_k": 0,
                "max_chars": 0,
                "candidates": 0,
                "selected": 0,
                "excluded": {},
                "warnings": ["invalid_bounds"],
            }
            self._last_memory_diagnostics = diagnostics
            return []
        diagnostics: dict[str, Any] = {
            "query": query_text[:300],
            "scope": requested_scope or "all",
            "backend": "local_structured_store",
            "repo_identity": (repo_identity or self.repository_identity)[:512],
            "top_k": max(0, min(requested_top_k, _STRUCTURED_MEMORY_MAX_TOP_K)),
            "max_chars": max(0, min(requested_max_chars, _STRUCTURED_MEMORY_MAX_CHARS)),
            "candidates": 0,
            "eligible_count": 0,
            "scope_matches": 0,
            "selected": 0,
            "selected_count": 0,
            "stale_excluded": 0,
            "budget": max(0, min(requested_max_chars, _STRUCTURED_MEMORY_MAX_CHARS)),
            "excluded": {},
            "warnings": [],
        }
        if requested_scope is not None and requested_scope not in MEMORY_SCOPES:
            diagnostics["warnings"] = ["invalid_scope"]
            self._last_memory_diagnostics = diagnostics
            return []
        if diagnostics["top_k"] <= 0 or diagnostics["max_chars"] <= 0:
            diagnostics["warnings"] = ["bounded_empty_request"]
            self._last_memory_diagnostics = diagnostics
            return []

        items, errors = self._read_structured_memory_items()
        if errors:
            diagnostics["warnings"] = errors[:16]
        diagnostics["candidates"] = len(items)
        q_tokens = set(normalize_memory_text(query_text).split())
        current_repo = str(repo_identity or self.repository_identity)[:512]
        current_project = project_id if project_id is not None else self.project_id
        scored: list[tuple[float, float, str, Memory]] = []
        excluded: dict[str, int] = {}
        now = self._now_fn()
        for item in items:
            reason = self._memory_scope_reason(
                item,
                scope=requested_scope,
                repo_identity=current_repo,
                project_id=current_project,
                user_id=user_id,
            )
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            diagnostics["scope_matches"] += 1
            stale_reason = self._memory_stale_reason(item)
            if stale_reason:
                excluded["stale:" + stale_reason] = excluded.get("stale:" + stale_reason, 0) + 1
                diagnostics["stale_excluded"] += 1
                continue
            if item.verification not in {
                MemoryVerification.OBSERVED.value,
                MemoryVerification.VERIFIED.value,
                MemoryVerification.USER_PROVIDED.value,
            }:
                excluded["verification"] = excluded.get("verification", 0) + 1
                continue
            diagnostics["eligible_count"] += 1
            item_tokens = set(normalize_memory_text(item.text).split())
            overlap = len(q_tokens & item_tokens)
            if q_tokens and overlap == 0:
                excluded["not_relevant"] = excluded.get("not_relevant", 0) + 1
                continue
            relevance = overlap / max(1, len(q_tokens)) if q_tokens else 1.0
            scope_weight = {"project": 1.0, "repo_local": 0.95, "user": 0.9, "global": 0.75}.get(item.scope, 0.5)
            verification_weight = {
                MemoryVerification.VERIFIED.value: 1.0,
                MemoryVerification.OBSERVED.value: 0.9,
                MemoryVerification.USER_PROVIDED.value: 0.95,
            }.get(item.verification, 0.0)
            age_days = max(0.0, (now - (_memory_timestamp(item.updated_at) or now)).total_seconds() / 86_400)
            freshness = 1.0 / (1.0 + age_days / 365.0)
            score = round(min(1.0, 0.65 * min(1.0, relevance) + 0.20 * scope_weight + 0.10 * verification_weight + 0.05 * freshness), 6)
            scored.append((score, _memory_timestamp_key(item.updated_at), item.memory_id or "", item))

        scored.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
        selected: list[Memory] = []
        used_chars = 0
        for score, _updated, _id, item in scored:
            if len(selected) >= diagnostics["top_k"]:
                break
            item_chars = len(item.text) + 2
            if used_chars + item_chars > diagnostics["max_chars"]:
                excluded["retrieval_budget"] = excluded.get("retrieval_budget", 0) + 1
                continue
            selected.append(replace(item, score=score))
            used_chars += item_chars
        diagnostics["selected"] = len(selected)
        diagnostics["selected_count"] = len(selected)
        diagnostics["selected_ids"] = [item.memory_id for item in selected]
        diagnostics["excluded"] = excluded
        self._last_memory_diagnostics = diagnostics
        return selected

    def recall_structured_memory(self, query: str = "", **kwargs: Any) -> list[Memory]:
        """Named compatibility alias for :meth:`recall_memory`."""

        return self.recall_memory(query, **kwargs)

    def invalidate_memory(self, memory_id: str, reason: str, *, tombstone: bool = False) -> MemoryWriteResult:
        """Write an explicit invalidation/tombstone without deleting history."""

        if not isinstance(memory_id, str) or not memory_id.strip():
            result = MemoryWriteResult(False, "rejected", "invalid_memory_id")
            self._last_memory_write = result
            return result
        try:
            with self.locked():
                items, errors = self._read_structured_memory_items()
                if errors:
                    result = MemoryWriteResult(
                        False,
                        "error",
                        "store_corrupt",
                        diagnostics={"errors": errors[:16]},
                    )
                    self._last_memory_write = result
                    return result
                target = next((item for item in items if item.memory_id == memory_id), None)
                if target is None:
                    result = MemoryWriteResult(False, "rejected", "memory_not_found")
                    self._last_memory_write = result
                    return result
                if target.status != MemoryStatus.ACTIVE.value:
                    result = MemoryWriteResult(True, "noop", "already_inactive", item=target)
                    self._last_memory_write = result
                    return result
                next_status = MemoryStatus.TOMBSTONED.value if tombstone else MemoryStatus.INVALIDATED.value
                updated = replace(
                    target,
                    status=next_status,
                    invalidated_reason=str(reason or "explicit invalidation")[:512],
                    updated_at=_memory_now_iso(self._now_fn),
                )
                self._write_structured_memory_items([updated if item is target else item for item in items])
                result = MemoryWriteResult(
                    True,
                    "tombstoned" if tombstone else "invalidated",
                    "explicit_invalidation",
                    item=updated,
                )
                self._last_memory_write = result
                return result
        except OSError as exc:
            result = MemoryWriteResult(False, "error", "storage_unavailable", diagnostics={"error": str(exc)[:160]})
            self._last_memory_write = result
            return result

    def invalidate(self, memory_id: str, reason: str, **kwargs: Any) -> MemoryWriteResult:
        return self.invalidate_memory(memory_id, reason, **kwargs)

    def append_history(self, entry: str) -> None:
        if _looks_like_recovery_evidence(entry):
            logger.info("append_history: recovery evidence is not durable Memory")
            return
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    _FORESIGHT_MAX_KEEP_DEFAULT = 20

    def append_foresight(
        self,
        foresights: list[dict[str, Any]],
        *,
        max_keep: int | None = None,
    ) -> int:
        """把 LLM-emitted Foresight Predictions 持久化到 ``user.md`` 的 ``## Foresight``。

        Section 不存在时通过 ``_splice_h2_section`` Fallback 语义创建并置于文件末尾。先按
        ``(prediction text, src_ts)`` 做 Exact Dedup，再以
        Content-token Jaccard 拦截 Reworded Semantic Duplicates。总 Bullet Count 使用 FIFO Cap，默认
        ``max_keep=20``，超出时从前端丢弃 Oldest Entries，使 Section 可扫描。整个 Read-modify-write 在
        Memory Lock 下完成，和 Concurrent Consolidator/Personalizer 安全协调。

        返回 Post-dedupe 实际写入的新 Entry 数；Empty Input 或 All Dupes 返回 0 且不触碰文件。写入的是
        Prediction Evidence，不代表预测已发生；非法/缺失字段会以 ``?`` 可见保留供 Review。
        """
        if not foresights:
            return 0
        cap = max_keep if max_keep is not None else self._FORESIGHT_MAX_KEEP_DEFAULT
        gen_ts = self._now_fn().strftime("%Y-%m-%d %H:%M")

        with self.locked():
            current = self.read_long_term()
            sections = _parse_user_md_sections(current)

            # 原样保留 ## Foresight 分节中的已有条目，避免重写时反复改动格式。
            existing_bullets: list[str] = []
            if _FORESIGHT_HEADING in sections:
                for line in sections[_FORESIGHT_HEADING].splitlines():
                    if line.lstrip().startswith("-"):
                        existing_bullets.append(line.rstrip())

            existing_keys: set[tuple[str, str]] = set()
            existing_predictions: list[str] = []
            for line in existing_bullets:
                m = _FORESIGHT_BULLET_RE.match(line)
                if m:
                    pred_text = m.group("prediction").strip()
                    existing_keys.add((pred_text, m.group("src_ts").strip()))
                    existing_predictions.append(pred_text)

            new_bullets: list[str] = []
            written = 0
            semantic_skipped = 0
            for fs in foresights:
                pred = (fs.get("prediction") or "").strip()
                src_ts = (fs.get("src_ts") or "").strip()
                if not pred:
                    continue
                key = (pred, src_ts)
                if key in existing_keys:
                    continue
                # 语义去重。( prediction, src_ts ) 键只能识别完全相同的字符串；LLM 会对来自不同事件的
                # 同一主张换语重发，这些都会漏过。使用内容词元的 Jaccard 相似度拦截，
                # 也会拦截当前批次内的重复项。
                if _is_semantic_duplicate_foresight(pred, existing_predictions):
                    semantic_skipped += 1
                    continue
                new_bullets.append(_format_foresight_bullet(fs, gen_ts))
                existing_keys.add(key)
                existing_predictions.append(pred)
                written += 1
            if semantic_skipped:
                logger.info(
                    "append_foresight: skipped {} semantic-duplicate prediction(s)",
                    semantic_skipped,
                )

            if written == 0:
                return 0

            all_bullets = existing_bullets + new_bullets
            # FIFO：保留最新的 ``cap`` 项，丢弃最旧项
            if len(all_bullets) > cap:
                all_bullets = all_bullets[-cap:]

            body = "\n".join(all_bullets)
            base = current or "# Long-term Memory\n"
            # 始终置底变体：如果 ## Foresight 已位于文件中部，例如 annotate 早于任何
            # refresh_section 创建 ## Projects，则将其移到底部，使视觉顺序为：
            # 先放稳定的用户资料分节，最后放自动管理的 Foresight。
            new_content = _splice_h2_section_at_end(
                base,
                _FORESIGHT_HEADING,
                body,
            )
            if new_content != current:
                self.write_long_term(new_content)
            return written

    def read_history_since(self, since_ms: int) -> str:
        """返回 Leading Timestamp ``>= since_ms`` 的 HISTORY.md Entries。

        当前文件是 ``episodes.md``，历史接口仍称 HISTORY.md。Entries 按 `append_history` 以 Blank Line
        分段，Consolidator LLM Prompt 要求每段以 ``[YYYY-MM-DD HH:MM]`` 开头。方法按 ``\\n\\n`` Split、解析 Stamp，只
        保留不早于给定 Epoch Milliseconds 的 Paragraph。

        Malformed/Missing Stamp 无法可靠 Anchored in Time，因此 Drop；文件 Missing 或 Read Error 返回
        ``""``。返回内容只经过时间过滤，不保证每条 Episode 的事实质量。
        """
        if not self.history_file.exists():
            return ""
        try:
            raw = self.history_file.read_text(encoding="utf-8")
        except OSError:
            return ""

        kept: list[str] = []
        for paragraph in raw.split("\n\n"):
            stripped = paragraph.strip()
            if not stripped:
                continue
            ts_ms = _parse_history_paragraph_ts_ms(stripped)
            if ts_ms is None or ts_ms < since_ms:
                continue
            kept.append(stripped)
        return "\n\n".join(kept)

    # 共享辅助函数将用户资料和事件记录的修改限制在 MemoryStore 内。

    def read_history_tail(self, lines: int) -> str:
        """返回 HISTORY.md 最后 ``lines`` 条 Non-blank Lines。

        当前文件是 ``episodes.md``。``lines <= 0`` 返回全部 Non-blank Lines，Missing File 或 Read Error
        返回 ``""``。方法原属于已删除的 ``DefaultMemoryEngine`` Facade，现在是 `MemoryStore` Public
        Surface。它按行裁剪，不理解 Paragraph 或 Timestamp Boundaries。
        """
        if not self.history_file.exists():
            return ""
        try:
            raw = self.history_file.read_text(encoding="utf-8")
        except OSError:
            return ""
        non_blank = [line for line in raw.splitlines() if line.strip()]
        if lines <= 0:
            return "\n".join(non_blank)
        return "\n".join(non_blank[-lines:])

    def update_section(
        self,
        heading: str,
        body: str,
        *,
        at_end: bool = True,
    ) -> None:
        """Replace 或 Insert MEMORY.md 中一个 H2 Section。

        当前文件是 ``user.md``。必须在 :meth:`locked` 内调用；方法不自行 Acquire Lock，使 Caller 可以把
        Multiple Updates Group 在同一 Lock：

            with store.locked():
                store.update_section("## Preferences", body)

        ``at_end=True`` 默认使用 :func:`_splice_h2_section_at_end`，保证 Named Section 写后位于 File End；
        ``at_end=False`` 使用 :func:`_splice_h2_section` 保留 Existing Position。方法直接写盘、无返回值，
        不执行 CAS。
        """
        current = self.read_long_term()
        if at_end:
            new = _splice_h2_section_at_end(current, heading, body)
        else:
            new = _splice_h2_section(current, heading, body)
        self.write_long_term(new)

        # 感知分节的读取。

    _SECTION_READ_TOP_K = 2
    _NOTES_HEADING_PREFIX = "## Notes"

    def get_memory_context(
        self,
        current_message: str | None = None,
        *,
        repo_identity: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        structured_top_k: int = _STRUCTURED_MEMORY_DEFAULT_TOP_K,
        structured_max_chars: int = _STRUCTURED_MEMORY_DEFAULT_MAX_CHARS,
    ) -> str:
        """返回要嵌入 Agent System Prompt 的 Memory Block。

        ``current_message=None`` 或 Empty 时返回 Full ``user.md`` Dump，适合 Cold-start Session 或无 User
        Query Ping。提供 Message 时，解析 H2 Sections、按 Lexical Overlap 评分，选择默认 Top-K=2，并加入
        ``## Notes`` Catchall；保留的 Sections 按 Original File Order 呈现。

        Section Parsing/Selection 无结果时 Fall Back 到 Full Dump；文件空则返回空字符串。返回 Block 只
        表示选中候选，仍需 Context Assembler 真正放进 Provider Request。
        """
        # Both Markdown profile material and structured items are rendered by
        # this existing Store and returned to the single Memory segment.  The
        # item recall is bounded and fail-closed; it never reads Session,
        # EffectJournal, Recovery, or ToolResult payloads.
        if self.structured_file.exists():
            structured_hits = self.recall_memory(
                current_message or "",
                repo_identity=repo_identity,
                project_id=project_id,
                user_id=user_id,
                top_k=structured_top_k,
                max_chars=structured_max_chars,
            )
        else:
            # Avoid opening a trace/artifact path for the common empty-store
            # case while keeping the public recall operation instrumented.
            self._last_memory_diagnostics = {
                "query": str(current_message or "")[:300],
                "backend": "local_structured_store",
                "candidates": 0,
                "selected": 0,
                "excluded": {},
                "warnings": [],
            }
            structured_hits = []
        structured_block = ""
        if structured_hits:
            from pico.context_engine.segments.render import render_recalled_memory

            structured_block = render_recalled_memory(structured_hits)

        long_term = self.read_long_term()
        profile = ""
        if long_term:
            if not current_message or not current_message.strip():
                profile = f"## Long-term Memory\n{long_term}"
            else:
                sections = _parse_user_md_sections(long_term)
                if not sections:
                    profile = f"## Long-term Memory\n{long_term}"
                else:
                    selected = self._select_relevant_sections(
                        current_message,
                        sections,
                        top_k=self._SECTION_READ_TOP_K,
                    )
                    if not selected:
                        profile = f"## Long-term Memory\n{long_term}"
                    else:
                        body = "\n\n".join(
                            f"{heading}\n\n{section_body}".rstrip() for heading, section_body in selected.items()
                        )
                        profile = f"## Long-term Memory\n\n{body}\n"

        if not structured_block:
            return profile
        structured = f"## Structured Memory\n\n{structured_block}\n"
        if not profile:
            return structured
        return profile.rstrip() + "\n\n" + structured

    @classmethod
    def _select_relevant_sections(
        cls,
        query: str,
        sections: dict[str, str],
        top_k: int = 2,
    ) -> dict[str, str]:
        """为每个 Section 打分，保留 Score > 0 的 Top-K，并加入 ``## Notes`` Catchall。

        Score 为 0 的 Sections **NOT Included** as Filler，否则 Tied-zero Content 会 Leak 进 Prompt。Notes
        Heading Prefix 始终保留。Returned Dict 按 Source File Order 重建，确保 Predictable Rendering，而
        不是按 Score 排列。
        """
        scored = [(heading, body, _score_section_relevance(query, heading, body)) for heading, body in sections.items()]
        scored.sort(key=lambda x: x[2], reverse=True)
        keep_keys: set[str] = {h for h, _, score in scored[:top_k] if score > 0}
        for heading in sections:
            if heading.startswith(cls._NOTES_HEADING_PREFIX):
                keep_keys.add(heading)
        return {h: b for h, b in sections.items() if h in keep_keys}

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    @trace.instrument("memory.extract", extract=semconv.memory_extract)
    async def annotate(
        self,
        messages: list[dict],
        provider: LLMProvider,
        model: str,
        *,
        enable_foresight: bool = False,
    ) -> bool:
        """Light Path：只 Annotate 当前 Conversation Chunk。

        Produces 两类可选结果：带 ``#tags`` 的 Single-line ``episodes.md`` Entries；以及仅在
        ``enable_foresight=True`` 时写入 ``user.md ## Foresight`` 的 ``foresight_hint``。方法 **不**修改
        User Profile Sections；Tag 积累足够 New Events 后，Heavy Path ``maybe_refresh_hot_tags`` 才调用
        ``refresh_section``。

        默认 ``enable_foresight=False`` 会从 Tool Schema 完全移除 Prediction Slot。空 Messages 直接成功；
        LLM 未调用 Tool、Arguments 结构错误或 Provider Exception 返回 `False`。Process-only Episodes 在
        写回边界丢弃。返回 `True` 表示 Annotation Pipeline 完成，不保证写入了非空 Episode。
        """
        if not messages:
            return True

        now_str = self._now_fn().strftime("%Y-%m-%d %H:%M (%A)")
        if enable_foresight:
            slot_lines = (
                "- episode_summary: ARRAY of single-line entries "
                '"[YYYY-MM-DD HH:MM] <summary, <=100 chars> #tag1 #tag2".\n'
                "- foresight_hint: ARRAY of predictions; [] when no deferred / "
                "recurring signal."
            )
            example_tail = (
                "\nforesight_hint:\n"
                '  - {{"prediction": "User will revisit WebSocket leak '
                'fix after load test next week", "window": "5-7 days", '
                '"confidence": "medium", "src_ts": '
                '"2024-11-08 14:20"}}\n'
                '  - {{"prediction": "User runs every Saturday morning '
                '(recurring habit, 3+ observations)", "window": '
                '"recurring weekly", "confidence": "high", '
                '"src_ts": "2024-11-09 10:00"}}\n'
                '  - {{"prediction": "Q4 retrospective scheduled for '
                'next Friday", "window": "5 days", "confidence": '
                '"high", "src_ts": "2024-11-09 14:00"}}\n'
                "(Again: FORMAT examples from an unrelated domain. "
                "Produce predictions only for the conversation above.)\n"
            )
            sys_line = (
                "You are a conversation annotator. Call "
                "annotate_conversation exactly once with both slots filled "
                "(foresight_hint may be [])."
            )
        else:
            slot_lines = (
                "- episode_summary: ARRAY of single-line entries "
                '"[YYYY-MM-DD HH:MM] <summary, <=100 chars> #tag1 #tag2".'
            )
            example_tail = ""
            sys_line = (
                "You are a conversation annotator. Call annotate_conversation exactly once with episode_summary filled."
            )
        # 将最近使用的项目 slug 提供给 LLM，让它复用旧值，而不是每次调用都发明新变体，
        # 例如 #project-clawtrack-release、...-cli 和 ...-coverage。
        recent_tags = self.recent_project_tags(days=14, limit=12)
        if recent_tags:
            tag_history_lines = "\n".join(f"  - #{tag} ({n}x in last 14 days)" for tag, n in recent_tags)
            tag_history_block = (
                "\n## Project tags you've recently used — REUSE these "
                "slugs when describing the same project; do NOT invent "
                "new variants:\n" + tag_history_lines + "\n"
            )
        else:
            tag_history_block = ""

        prompt = f"""Annotate this conversation chunk. Call annotate_conversation with:

{slot_lines}

## Critical rules

1. **Each episode summary must include specific identifiers** — file
   names, function names, PR numbers, percentages, durations. Avoid
   vague verbs like "worked on" / "discussed" / "planned"; describe the
   concrete artifact, decision, or finding.
2. **Reuse project slugs across calls**. If a project slug already
   exists in the "tags you've recently used" list below, use it
   verbatim. Splitting one project into multiple slugs
   (#project-clawtrack-release / -cli / -docs) destroys the tag-based
   refresh trigger — pick ONE stable slug per project.
3. **Tag the WORK, not the codebase**: `#project-<work-slug>` where the
   slug names the topic. Use `#project-auth-refactor` not
   `#project-backend-api`.
4. **Process tags can't stand alone**. `#question`, `#habit`, `#answer`
   describe HOW the user is talking, not WHAT about. Every episode
   needs at least one CONTENT tag (a `#project-*` or one of {{#perf,
   #bug, #decision, #blocker, #deferred, #pivot, #pr, #review, #rfc,
   #design, #infra, #sql, #ml}}) IN ADDITION to any process tag.
5. **Avoid the generic #task tag**.
{tag_history_block}
## Current Time
{now_str}

## Conversation to Annotate
{self._format_messages(messages)}

## Output shape example
The examples below are from an UNRELATED domain (websocket / DB / feature-flag work). They demonstrate the FORMAT only. DO NOT copy any of their text or topics — generate entries that describe the actual conversation above.

episode_summary:
  - "[2024-11-08 14:20] Identified memory leak in WebSocketManager.broadcast(); ~200MB growth/hour under load #project-ws-stability #perf #bug"
  - "[2024-11-09 10:00] Migrated user_sessions from MyISAM to InnoDB (~12M rows, 4h offline window) #project-db-migration #infra #decision"
  - "[2024-11-11 16:30] Feature flag 'dark-mode-v2' ramped 10%->50% after 24h of steady metrics #project-feature-flag-rollout #pr #decision"
{example_tail}"""

        try:
            response = await provider.chat_with_retry(
                messages=[
                    {"role": "system", "content": sys_line},
                    {"role": "user", "content": prompt},
                ],
                tools=_build_annotate_tool(enable_foresight=enable_foresight),
                model=model,
                tool_choice="required",
            )

            if not response.has_tool_calls:
                logger.warning("annotate: LLM did not call annotate_conversation")
                return False

            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None:
                logger.warning("annotate: unexpected tool arguments")
                return False

            episodes = args.get("episode_summary") or []
            if isinstance(episodes, str):
                episodes = [episodes]
            n_written = 0
            n_dropped_process_only = 0
            for ep in episodes:
                line = _ensure_text(ep).strip()
                if not line:
                    continue
                if _looks_like_recovery_evidence(line):
                    logger.info("annotate: dropped recovery-only episode")
                    continue
                # 在边界处丢弃只描述过程的事件。提示词声明 #question、#habit 和 #answer 不能单独存在，
                # 但 LLM 仍有约 5% 的概率输出它们。放行会污染 refresh_section：标签热度触发刷新，
                # 却没有内容标签作为锚点，最终让 LLM 向 ## Projects 或 ## Notes 写入自由形式的猜测。
                if _is_process_only_episode(line):
                    n_dropped_process_only += 1
                    continue
                self.append_history(line)
                n_written += 1
            if n_dropped_process_only:
                logger.info(
                    "annotate: dropped {} process-only episode(s)",
                    n_dropped_process_only,
                )

            if enable_foresight:
                foresights = args.get("foresight_hint") or []
                if foresights:
                    # 在内存锁保护下持久化到 user.md 的 ## Foresight 分节；在线程中执行，
                    # 避免异步事件循环被文件 I/O 阻塞。
                    written = await asyncio.to_thread(
                        self.append_foresight,
                        foresights,
                    )
                    logger.info(
                        "annotate: {} foresight hint(s) emitted → "
                        "user.md ## Foresight ({} written, {} deduped/skipped)",
                        len(foresights),
                        written,
                        len(foresights) - written,
                    )

            logger.info(
                "annotate done for {} messages -> {} episode(s)",
                len(messages),
                n_written,
            )
            return True
        except Exception:
            logger.exception("annotate failed")
            return False

    # -------------------------------------------------------------------
    # 重量路径：由标签频率触发用户资料分节刷新。
    # -------------------------------------------------------------------

    @property
    def _tag_offsets_path(self) -> Path:
        """返回 ``episodes.md`` 旁的 ``.consolidation_offsets.json`` Path。

        文件记录每个 Tag 在 Last Refresh 时的 Episode Count，使下次只根据 Delta 判断是否需要 Profile
        Refresh。Property 只计算路径，不创建或读取文件。
        """
        return self.history_file.parent / ".consolidation_offsets.json"

    def read_tag_offsets(self) -> dict[str, int]:
        """读取 Last Section Refresh 时的 Per-tag Episode Count。

        Missing File 或 Bad JSON 返回 Empty Dict，按 ``never refreshed`` 处理并记录 Parse Warning。有效
        Payload 只保留可转成 Integer 的数值项；返回值是当前 Snapshot，不持有文件锁。
        """
        p = self._tag_offsets_path
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("tag offsets: failed to parse {}; starting fresh", p)
            return {}
        return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}

    def write_tag_offsets(self, offsets: dict[str, int]) -> None:
        """以 Temp + Rename 原子写入 Tag Offsets File。

        创建父目录，使用 Sorted/Indented JSON 保持可审计，然后替换目标。方法无返回值；OS/Serialization
        Error 向上传播，防止 Refresh 成功后 Offset 悄悄未保存。
        """
        p = self._tag_offsets_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(offsets, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(p)

    def count_tags(self) -> dict[str, int]:
        """统计整个 ``episodes.md`` 中每个 Tag 的 Total Occurrence Count。

        只处理可由 `_parse_episode_line` 解析的 Event Lines，Freeform/Invalid Lines 跳过；同一 Episode 中
        出现的每个 Tag 各加一。Missing File 返回空 Dict。Count 是 Refresh Trigger Evidence，不代表 Tag
        对应事实仍为当前状态。
        """
        if not self.history_file.exists():
            return {}
        counts: dict[str, int] = {}
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            parsed = _parse_episode_line(line)
            if not parsed:
                continue
            _, _, tags = parsed
            for t in tags:
                counts[t] = counts.get(t, 0) + 1
        return counts

    def recent_project_tags(
        self,
        *,
        days: int = 14,
        limit: int = 12,
    ) -> list[tuple[str, int]]:
        """返回最近 ``days`` 内最多 ``limit`` 个 ``(project-tag, count)`` Pairs，按 Frequency 排序。

        只统计 ``episodes.md`` 中 Parseable、Timestamp 不早于 Cutoff、且以 ``project-`` 开头的 Tags。
        `annotate()` 用结果 Seed Prompt 的 ``slugs you've already used``，促使 LLM 复用旧值，防止同一项目
        被拆成多个 ``#project-*-cli`` / ``-docs`` / ``-release`` Variants。Missing File 返回空列表。
        """
        from datetime import timedelta

        if not self.history_file.exists():
            return []
        cutoff = self._now_fn() - timedelta(days=days)
        counts: dict[str, int] = {}
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            parsed = _parse_episode_line(line)
            if not parsed:
                continue
            ts, _, tags = parsed
            ts_norm = ts.replace("T", " ")
            try:
                dt = datetime.strptime(ts_norm, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if dt < cutoff:
                continue
            for t in tags:
                if t.startswith("project-"):
                    counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]

    def hot_tags(self, threshold: int) -> list[tuple[str, int, int]]:
        """返回满足 ``current_count - last_offset >= threshold`` 的 Hot Tags。

        结果 Shape 是 ``[(tag, current_count, previous_offset), ...]``，按 Delta Descending 排序，使 Hottest
        Tag First Refresh。Threshold 非正时可能让所有已有 Tag 变 Hot，上层负责提供合理配置。
        """
        counts = self.count_tags()
        offsets = self.read_tag_offsets()
        hot: list[tuple[str, int, int]] = []
        for tag, current in counts.items():
            prev = offsets.get(tag, 0)
            if current - prev >= threshold:
                hot.append((tag, current, prev))
        hot.sort(key=lambda x: x[1] - x[2], reverse=True)
        return hot

    def _episodes_for_tag(
        self,
        tag: str,
        max_episodes: int = 50,
    ) -> list[str]:
        """返回携带给定 Tag 的最近最多 N 条 Episode Lines，并按 Chronological Order 排列。

        方法从 ``episodes.md`` Tail 反向扫描，只接受 Parseable Lines，达到 `max_episodes` 后停止，再反转为
        正序。Missing File 返回空列表。结果是 `refresh_section` 的 Evidence Window，不包含更早事件。
        """
        if not self.history_file.exists():
            return []
        matches: list[str] = []
        for line in reversed(self.history_file.read_text(encoding="utf-8").splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            parsed = _parse_episode_line(stripped)
            if not parsed:
                continue
            _, _, tags = parsed
            if tag in tags:
                matches.append(stripped)
                if len(matches) >= max_episodes:
                    break
        matches.reverse()
        return matches

    async def refresh_section(
        self,
        tag: str,
        provider: LLMProvider,
        model: str,
        max_episodes: int = 50,
    ) -> bool:
        """Heavy Path：依据 ``tag`` 的 Recent Episodes 重写 ``user.md`` 中 **ONE H2 Section**。

        LLM 根据 Current Profile 与最多 `max_episodes` 条相关事件，选择 Existing Target H2，确无匹配时才
        Create One，并输出 ``{section_heading, section_body}``。Prompt 强制把 Profile 当 Current Snapshot
        而非 Diary，要求 UPDATE/CONSOLIDATE/REMOVE 优先于 APPEND，且每条 Bullet 带
        ``[src: episodes.md @ ts]`` Evidence Link。Splicer 只替换该 Section Body，其他 Sections 保持
        Byte-identical。

        无 Relevant Episodes 返回 `True` 且不调用模型；Tool Call/Args/Heading 无效或异常返回 `False`。
        缺 Citation Bullets 在边界丢弃，Concurrent Profile Modification 会由 CAS Skip。返回成功表示刷新
        流程接受，不等于 LLM 生成的 Profile Claim 已人工验证。
        """
        relevant = self._episodes_for_tag(tag, max_episodes)
        if not relevant:
            logger.debug("refresh_section({}): no matching episodes", tag)
            return True

        current_profile = self.read_long_term()
        now_str = self._now_fn().strftime("%Y-%m-%d %H:%M (%A)")
        episodes_block = "\n".join(relevant)
        prompt = f"""Update ONE H2 section of user.md based on the recent
#{tag} episodes below. user.md is a PROFILE SNAPSHOT (current state per
topic), NOT an event log — episodes.md already keeps the event log.

<principles>
1. Explicit Evidence Required — only place a fact in user.md if you can
   cite an episode timestamp. No speculation, no inference from titles.
2. Quality Over Quantity — 5 accurate bullets > 15 noisy ones.
   An empty section is OK.
3. Inertia — existing bullets are correct unless a NEW episode
   contradicts them. UPDATE bullets in place rather than rewrite.
4. Reject Events — one-off events, emotional states ("anxiety",
   "frustration"), and transient process work ("in middle of debugging X")
   do NOT belong in user.md — they're already in episodes.md.
5. Profile Snapshot, Not Diary — every bullet answers "what is true
   about this user right now?", not "what happened on day X?".
6. Abstraction, Not Enumeration — a profile bullet captures a PATTERN,
   not a list of instances. When N episodes share a theme, write ONE
   bullet describing the abstraction; do NOT comma-list the instances
   inside the bullet.
   ✓ "Spends commute/break time on child-related research"
   ✗ "Researches English materials, breakfast recipes, sunscreen,
      dental care, vaccines, parent-child games, homework, time
      management"
   The 9-item enumeration above defeats the snapshot — each instance
   already lives in episodes.md; user.md only needs the theme.
</principles>

<section_schemas>
Each H2 section follows a semi-structured convention. Use **Field**:
prefix for required slots; bullets without prefix are ad-hoc additions.

## Identity (≤ 5 bullets — stable role/personal facts)
  - **Name**: ...
  - **Role**: ...
  - **Stack**: ...
  - **Location**: ...
  - **Key relations**: <name + role, e.g. "周晓棠 (girlfriend)">

## Preferences (≤ 5 bullets — working style / tools / quiet hours)
  - **Communication**: terse | verbose | mixed; emoji-friendly Y/N
  - **Tools**: <comma-separated preferences>
  - **Quiet hours**: <when not to interrupt>
  - ad-hoc preference bullets allowed

## Projects → ### <project-name> (each H3 has 4-6 bullets)
  - **Type**: work | side project | learning | personal
  - **Status**: <one-line current state>
  - **Recent work**: <2-3 descriptive items, NOT per-day events>
  - **Next**: <upcoming actions>
  - Optional: **Stack**, **Stakeholders**, **Deadline**

## Habits (≤ 6 bullets — recurring patterns, ≥ 2 observations to qualify)
  - **<pattern>** (confirmed by N obs; freq: weekly | daily | sporadic)
    Example: "Saturday morning run (confirmed by 4+ obs; freq: weekly)"

## Notes (≤ 8 bullets — important specific facts)
  - <birthday / deadline / preferred X / similar facts>

## Foresight — AUTO-MANAGED by a different path. DO NOT TARGET; do NOT
rewrite its contents.
</section_schemas>

<triage_each_episode>
Before deciding what to write, classify each new episode:

KEEP (write into user.md) if it represents:
  - identity / role / relationship fact → ## Identity
  - working preference confirmed → ## Preferences
  - project state change (status, deliverable, decision) → ## Projects
  - recurring pattern with ≥ 2 observations → ## Habits
  - dated commitment / deadline / specific fact → ## Notes

REJECT (stays in episodes.md only, do NOT add to user.md) if it's:
  - one-off event ("ran today", "had lunch", "PR merged" — the PR-merged
    detail goes in episodes.md; the project's Status field captures the
    end state, not the per-event)
  - emotional state ("anxious", "frustrated", "excited")
  - transient process ("in middle of debugging X", "testing Y")
  - in-progress detail that resolves soon
  - already covered by an existing bullet without new info
  - just a question the user asked
</triage_each_episode>

<update_protocol>
For each episode that PASSES triage, follow this order STRICTLY:

1. UPDATE first — find an existing bullet on the same subject; refine it
   in place to reflect the latest evidence.
   Example: existing "**Status**: pre-release testing"
            + new "PR #1287 merged: v1.0 released"
            → "**Status**: v1.0 released (5/15), gathering feedback"

2. CONSOLIDATE second — merge related bullets in the same section.
   Example: "**Recent work**: CLI bug" + new "doc generation broken"
            → "**Recent work**: CLI bug + doc generation broken"

   ANTI-PATTERN — DO NOT enumerate. When N episodes share a THEME but
   each adds a different specific instance, do NOT comma-list every
   instance inside the bullet.
   Existing "Researches child topics" + new episode "researched vaccines":
     ✗ bad:  "Researches child topics including English, breakfast,
              vaccines, dental care, sunscreen, ..."
     ✓ good: leave the bullet UNCHANGED — the theme is already captured;
             the specific vaccine instance lives in episodes.md.
   Apply this whenever you find yourself reaching for "including", "such
   as", "e.g.", or a comma-list of nouns inside one bullet.

3. REMOVE third — drop bullets obsoleted by new evidence.
   Example: "**Status**: pre-release anxiety" → DROP once "released" lands.

4. APPEND last — only if truly new topic AND under the section cap.

After processing, respect section caps (see <section_schemas>).
If you'd exceed a cap, CONSOLIDATE harder.
</update_protocol>

## Current Time
{now_str}

## Current user.md (UPDATE/CONSOLIDATE/REJECT — don't just append)
{current_profile or "(empty)"}

## Recent episodes tagged #{tag} ({len(relevant)} entries — fold into
the matching section after triage)
{episodes_block}

## Output
section_heading: the H2 line you're updating (verbatim; for project
   work use `## Projects` — the H3 sub-section goes inside section_body).
section_body: full new content for that H2, every bullet ending with
   `[src: episodes.md @ <ts>]`. Use the LATEST relevant ts when merging.
"""
        try:
            response = await provider.chat_with_retry(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You maintain a structured user profile in user.md, "
                            "NOT an event log. Follow the <principles>, "
                            "<section_schemas>, <triage_each_episode>, and "
                            "<update_protocol> blocks in the user message. "
                            "Prefer UPDATE over APPEND; respect per-section "
                            "size caps; reject events / emotions / transient "
                            "process work that already lives in episodes.md."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=_REFRESH_SECTION_TOOL,
                model=model,
                tool_choice="required",
            )
            if not response.has_tool_calls:
                logger.warning("refresh_section({}): LLM did not call tool", tag)
                return False
            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None or "section_heading" not in args or "section_body" not in args:
                logger.warning("refresh_section({}): unexpected tool args", tag)
                return False
            heading = _ensure_text(args["section_heading"]).strip()
            body = _ensure_text(args["section_body"])
            if not heading.startswith("## "):
                logger.warning(
                    "refresh_section({}): bad heading {!r}",
                    tag,
                    heading,
                )
                return False

                # 丢弃缺少 ``[src: episodes.md @ ts]`` 证据链接的用户资料条目。跳过 ## Foresight：
                # 它由 append_foresight 自动管理，使用圆括号形式的 src，方括号正则无法匹配；
                # 如果对它应用该过滤器，会清空整个分节。
            if heading != _FORESIGHT_HEADING:
                body, n_src_dropped = _drop_bullets_without_src(body)
                if n_src_dropped:
                    logger.warning(
                        "refresh_section({}): dropped {} bullet(s) missing [src:] link in section {!r}",
                        tag,
                        n_src_dropped,
                        heading,
                    )

                    # 软可观测性：分节超过模式硬上限时警告（Projects/Habits 为 6，Notes 为 8，
                    # 其他为 5）。不执行截断，因为可能丢失有用内容；只暴露偏移，
                    # 便于在问题持续时重新调整提示词。
            n_bullets = sum(1 for ln in body.splitlines() if ln.lstrip().startswith("-"))
            if n_bullets > 15:
                logger.warning(
                    "refresh_section({}): LLM produced {} bullets (>15) for "
                    "section {!r} — schema cap violated, profile may be "
                    "diary-style. Check episodes.md / prompt drift.",
                    tag,
                    n_bullets,
                    heading,
                )

            await asyncio.to_thread(
                self._splice_section_and_write,
                heading,
                body,
                current_profile,
            )
            logger.info(
                "refresh_section({}): section {!r} updated using {} episode(s) -> {} bullets",
                tag,
                heading,
                len(relevant),
                n_bullets,
            )
            return True
        except Exception:
            logger.exception("refresh_section({}) failed", tag)
            return False

    def _splice_section_and_write(
        self,
        heading: str,
        new_body: str,
        expected_prev: str,
    ) -> bool:
        """执行 CAS Write：仅当 ``user.md`` 仍等于 ``expected_prev`` 时 Splice ``new_body``。

        在 Memory Lock 内重新读取文件；发现 Concurrent Writer 已修改时记录日志、返回 `False`，留待 Next
        Round Retry。匹配时替换 ``heading`` Body，并确保 Auto-managed ``## Foresight`` 仍在文件末尾；
        Content 有变化才写盘。返回 `True` 表示 CAS 条件成立并完成该路径，即使新内容与旧内容相同。
        """
        with self.locked():
            current = self.read_long_term()
            if current != expected_prev:
                logger.info("refresh_section: concurrent modification detected; skipping write (will retry next round)")
                return False
            new_content = _splice_h2_section(current, heading, new_body)
            # 无论 refresh_section 将新分节插入何处，都将自动管理的 ## Foresight 保持在 user.md 底部。
            # 该操作幂等；Foresight 不存在或已位于最后时不执行任何操作。
            new_content = _ensure_foresight_at_end(new_content)
            if new_content != current:
                self.write_long_term(new_content)
            return True

    @trace.instrument("memory.profile_refresh", extract=semconv.memory_profile_refresh)
    async def maybe_refresh_hot_tags(
        self,
        provider: LLMProvider,
        model: str,
        threshold: int = 5,
    ) -> int:
        """扫描 ``episodes.md``，刷新 New-episode Delta 达到 ``threshold`` 的 Tags。

        Refreshes 按 Hottest First Serial 执行，避免同一 ``user.md`` 上内部 Race。每个 Tag 只有
        `refresh_section` 成功后才推进 Offset；失败不推进，使下轮可 Retry。返回实际成功处理的 Section
        Count；Count 不表示生成了多少新事实，也不保证各 Tag 映射到不同 H2。
        """
        hot = self.hot_tags(threshold)
        if not hot:
            return 0
        offsets = self.read_tag_offsets()
        refreshed = 0
        for tag, current_count, _prev in hot:
            ok = await self.refresh_section(tag, provider, model)
            if ok:
                offsets[tag] = current_count
                self.write_tag_offsets(offsets)
                refreshed += 1
            else:
                # 失败时不推进偏移量，下一轮将重试。
                logger.warning(
                    "maybe_refresh_hot_tags: tag {!r} refresh failed; offset not advanced",
                    tag,
                )
        return refreshed


class MemoryConsolidator:
    """拥有 Consolidation Policy、Per-session Locking 与 Session Offset Updates。

    Consolidator 连接 `MemoryStore`、LLM Provider、Session Manager、Context Builder 与 Tool Definitions。
    当 Normal Prompt 达到 Context Window 时，它在 User-turn Boundary 选择 Old Chunk，先 Annotate 到
    Episodes，再推进 `session.last_consolidated` 并持久化 Session；至少一个 Chunk 成功后才触发 Hot-tag
    Profile Refresh。

    每个 Session Key 使用共享 Async Lock，避免同进程重复归并。它拥有“何时归并、归并到哪里”的状态，
    Store 拥有文件内容，Session Manager 拥有 Offset Durability。Annotation 成功与 Prompt 已缩到目标值
    分属不同证据。
    """

    _MAX_CONSOLIDATION_ROUNDS = 5

    # 自上次刷新后，某标签累积足够多的新事件时，触发用户资料分节刷新。
    # 低于阈值时事件仍会累积，但 user.md 保持不变。
    _REFRESH_HOT_TAG_THRESHOLD = 5

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        now_fn: Callable[[], datetime] | None = None,
        *,
        enable_foresight: bool = False,
        store: MemoryStore | None = None,
    ):
        # The AgentLoop passes its ContextBuilder-owned Store here so profile,
        # episodes, and structured items have one Memory authority. Standalone
        # callers retain the historical constructor behaviour.
        self.store = store or MemoryStore(workspace, now_fn=now_fn)
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        # 为 True 时，annotate() 会让 LLM 与事件一起生成预见预测，并持久化到
        # user.md 的 ## Foresight。默认关闭。
        self.enable_foresight = enable_foresight
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """返回一个 Session 共用的 Consolidation `asyncio.Lock`。

        Locks 存在 WeakValueDictionary 中；有活跃持有者/引用时同 Key 复用，完全不用后可被 GC，避免长期
        Session ID 无界积累。该锁只协调当前 Process，不替代 `MemoryStore` 的 Cross-process File Lock。
        """
        lock = self._locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_key] = lock
        return lock

    async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
        """Light Path：把 Selected Message Chunk Annotate 进 ``episodes.md``。

        这里 **不** Rewrite Profile；所有 Annotation Rounds 结束后再由 :meth:`maybe_refresh_hot_tags` 处理，
        让 LLM 每次只看到一个 Tag 的 Relevant Context。返回值直接反映 `MemoryStore.annotate` Pipeline 是否
        成功，不表示一定写入了 Episode。
        """
        return await self.store.annotate(
            messages,
            self.provider,
            self.model,
            enable_foresight=self.enable_foresight,
        )

    async def maybe_refresh_hot_tags(self) -> int:
        """刷新自 Last Refresh 后 Backing Tag 已 Heated Up 的 Profile Sections。

        使用 Class 固定 Threshold 调用 Store Heavy Path，返回成功处理的 Section 数。它应在 Annotation
        Batch 后调用，而非每条 Message 都触发昂贵 LLM Rewrite。
        """
        return await self.store.maybe_refresh_hot_tags(
            self.provider,
            self.model,
            threshold=self._REFRESH_HOT_TAG_THRESHOLD,
        )

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """选择能移除足够 Old Prompt Tokens 的 User-turn Boundary。

        从 ``session.last_consolidated`` 开始累加 `estimate_message_tokens`，只在下一条 User Message 之前
        形成 Safe Boundary，避免把一个 User/Assistant/Tool Interaction 从中间切开。返回
        ``(end_index, removed_tokens)``；没有可用 Boundary、起点在尾部或请求移除量非正时返回 `None`。
        """
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        """估算 Normal Session History View 的 Current Prompt Size。

        方法使用真实 `session.get_history`、Context Message Builder、Channel/Chat ID 与 Tool Definitions
        组装 ``[token-probe]`` Request，再通过 Provider Counter → Tiktoken Chain 返回 ``(tokens, source)``。
        该估算比单条求和更接近实际 Prompt，但仍不是 Provider Usage Receipt。
        """
        history = session.get_history(max_messages=0)
        channel, chat_id = session.key.split(":", 1) if ":" in session.key else (None, None)
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive_unconsolidated(self, session: Session) -> bool:
        """为 ``/new``-style Session Rollover 归档完整 Unconsolidated Tail。

         在 Per-session Lock 内 Snapshot ``last_consolidated:`` Tail，Annotate 到 ``episodes.md``，成功后运行
        一轮 Hot-tag Section Refresh，使 Profile 有机会反映刚关闭 Session。空 Tail 返回 `True`；失败时不
         伪装归档完成。方法本身不推进 Session Offset，因为 Rollover 管理后续 Session Lifecycle。
        """
        lock = self.get_lock(session.key)
        async with lock:
            snapshot = session.messages[session.last_consolidated :]
            if not snapshot:
                return True
            ok = await self.consolidate_messages(snapshot)
            if ok:
                await self.maybe_refresh_hot_tags()
            return ok

    @trace.instrument("memory.consolidate", extract=semconv.memory_consolidate)
    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
        """循环 Archive Old Messages，直到 Prompt Fits Within Half Context Window 或无法继续。

        空 Session/无效 Window 直接返回。只有 Initial Estimate 达到或超过完整 Context Window 才启动，目标
        是 Window 的一半；每轮最多 `_MAX_CONSOLIDATION_ROUNDS`，选择 Safe User Boundary、Annotate Chunk、
        推进并保存 `last_consolidated`，再重新估算。任一步失败、无 Boundary 或 Estimate 无效都会停止，
        不删除原 Messages。

        至少成功 Annotate 一个 Chunk 后，整次调用最多执行一次 Hot-tag Refresh。方法无返回值，Caller
        需要通过 Session Offset、Episodes/Profile 文件与日志区分“触发过”“持久化过”和“已达到目标”。
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            target = self.context_window_tokens // 2
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return
            if estimated < self.context_window_tokens:
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return

            chunks_annotated = 0
            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    break

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    break

                end_idx = boundary[0]
                chunk = session.messages[session.last_consolidated : end_idx]
                if not chunk:
                    break

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.consolidate_messages(chunk):
                    break
                chunks_annotated += 1
                session.last_consolidated = end_idx
                self.sessions.save(session)

                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    break

            # 只要至少标注了一个块，就让活跃标签有机会刷新对应的用户资料分节。
            # 无论触发多少轮标注，每次 ``maybe_consolidate_by_tokens`` 调用最多执行一次。
            if chunks_annotated:
                await self.maybe_refresh_hot_tags()
