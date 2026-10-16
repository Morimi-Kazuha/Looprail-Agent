"""Bounded historical file evidence; never a long-term MemoryStore input.

Only a host-bound builtin reader can issue receipts. Reuse always opens the
current authorized source and checks its complete bytes and every saved quote.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from looprail.memory_engine.consolidate.consolidator import repository_identity_for_path
from looprail.security.trust import wrap_untrusted
from looprail.utils.atomic_io import atomic_replace
from looprail.utils.helpers import estimate_prompt_tokens
from looprail.utils.portable_lock import file_lock

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_EXCERPT_BYTES = 2048
MAX_ENTRIES = 128
MAX_STORAGE_BYTES = 2 * 1024 * 1024
MAX_HITS = 3
MAX_TOKENS = 1500
TTL_SECONDS = 24 * 3600


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_lines(raw: bytes) -> list[str]:
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()


def no_links(path: Path) -> None:
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked or reparse path")


def stable_bytes(path: Path) -> bytes:
    """Check pathname/descriptor identity before and after one bounded read."""
    no_links(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_BYTES:
        raise ValueError("not a bounded regular source")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(MAX_SOURCE_BYTES + 1)
        after_fd = os.fstat(stream.fileno())
    no_links(path)
    after = path.stat()

    def fields(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)

    # Windows stat and fstat expose different ctime semantics on some Python
    # builds. Compare ctime only within the same API, retaining both fences.
    if (
        len(raw) > MAX_SOURCE_BYTES
        or not (fields(before) == fields(opened) == fields(after_fd) == fields(after))
        or before.st_ctime_ns != after.st_ctime_ns
        or opened.st_ctime_ns != after_fd.st_ctime_ns
    ):
        raise ValueError("unstable source")
    return raw


@dataclass(frozen=True)
class FileExcerpt:
    start_line: int
    end_line: int
    exact_text: str


@dataclass(frozen=True)
class FileObservationReceipt:
    repo_identity: str
    access_scope_id: str
    canonical_relative_path: str
    source_digest: str
    byte_length: int
    total_lines: int
    returned_ranges: tuple[tuple[int, int], ...]
    complete_file_observed: bool
    excerpts: tuple[FileExcerpt, ...]
    session_key: str
    turn_id: str | None
    call_id: str | None
    effect_id: str | None
    observed_at: float


@dataclass
class FileWorkingEntry:
    schema_version: int
    receipt: FileObservationReceipt
    created_at: float
    last_used_at: float
    status: str = "candidate"


class FileWorkingMemory:
    def __init__(self, workspace: Path, state: Path, *, access_scope_id: str | None = None):
        no_links(workspace.absolute())
        self.workspace = workspace.resolve()
        ancestor = state.absolute()
        while not ancestor.exists():
            ancestor = ancestor.parent
        no_links(ancestor)
        state = state.resolve()
        # A repo-controlled ordinary cache is never a valid host state root.
        if state == self.workspace or state.is_relative_to(self.workspace):
            raise ValueError("file working memory needs external host state")
        no_links(self.workspace)
        self.repo_identity = repository_identity_for_path(self.workspace)
        self.cross_session = bool(access_scope_id and access_scope_id.strip())
        self.scope = digest((access_scope_id or uuid.uuid4().hex).encode())
        self.path = state / "file-working" / self.repo_identity / self.scope / "entries.json"
        self.last_metrics = {}

    def eligible_path(self, name: str) -> Path:
        candidate = Path(name)
        if ".." in candidate.parts:
            raise ValueError("traversal")
        candidate = candidate if candidate.is_absolute() else self.workspace / candidate
        no_links(candidate)
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(self.workspace)
        secret = re.compile(
            r"(^\.env($|\.)|credential|secret|token|password|auth|^id_(rsa|dsa|ecdsa|ed25519)|\.(pem|key|p12|pfx|keystore)$)",
            re.I,
        )
        if any(secret.search(p) or p.lower() in {".git", ".ssh", ".aws", ".gnupg"} for p in relative.parts):
            raise ValueError("sensitive path")
        return resolved

    def snapshot(self, name: str):
        path = self.eligible_path(name)
        raw = stable_bytes(path)
        if b"\x00" in raw:
            raise ValueError("binary source")
        return path, raw

    def receipt(self, path, raw, start, end, context):
        lines = source_lines(raw)
        excerpts, used = [], 0
        # Entire original lines only. A line exceeding the bound is omitted.
        for index in range(start, end):
            line = lines[index]
            size = len(line.encode("utf-8")) + 1
            if used + size <= MAX_EXCERPT_BYTES:
                excerpts.append(FileExcerpt(index + 1, index + 1, line))
                used += size
        return FileObservationReceipt(
            self.repo_identity,
            self.scope,
            os.path.normcase(str(path.relative_to(self.workspace))).replace("\\", "/"),
            digest(raw),
            len(raw),
            len(lines),
            ((start + 1, end),) if end > start else (),
            start == 0 and end == len(lines),
            tuple(excerpts),
            context.session_key,
            context.turn_id,
            context.call_id,
            context.effect_id,
            time.time(),
        )

    def _read(self):
        if not self.path.exists():
            return []
        no_links(self.path)
        if self.path.stat().st_size > MAX_STORAGE_BYTES:
            raise ValueError("cache too large")
        entries = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise ValueError("invalid cache")
        for entry in entries:
            if entry["schema_version"] != 1:
                raise ValueError("unknown schema")
        return entries

    def observe(self, receipt: FileObservationReceipt):
        """Called only by the trusted Registry binding after successful terminal."""
        if receipt.repo_identity != self.repo_identity or receipt.access_scope_id != self.scope:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            no_links(self.path.parent)
            with file_lock(self.path.parent / "store.lock", blocking=False):
                entries = self._read()
                entries = [
                    e for e in entries if e["receipt"]["canonical_relative_path"] != receipt.canonical_relative_path
                ]
                now = time.time()
                entries.append(asdict(FileWorkingEntry(1, receipt, now, now)))
                entries.sort(key=lambda e: e["last_used_at"])
                entries = [e for e in entries if now - e["created_at"] <= TTL_SECONDS][-MAX_ENTRIES:]
                while len(json.dumps(entries, ensure_ascii=False).encode()) > MAX_STORAGE_BYTES:
                    entries.pop(0)
                atomic_replace(self.path, json.dumps(entries, ensure_ascii=False))
        except Exception:
            # This optional cache must never change a successful tool outcome.
            return

    def recall(self, query: str, session_key: str):
        started = time.perf_counter()
        metrics = {"file_working_hits": 0, "validation_bytes": 0, "file_working_tokens": 0}
        texts = []
        quote_blocks = []
        used_versions = set()
        try:
            if not self.path.exists():
                return "", metrics
            with file_lock(self.path.parent / "store.lock", blocking=False):
                entries = self._read()
            terms = set(re.findall(r"[\w./-]{3,}", query.casefold()))
            ranked = []
            for entry in entries:
                r = entry["receipt"]
                if (
                    r["repo_identity"] != self.repo_identity
                    or r["access_scope_id"] != self.scope
                    or not self.cross_session
                    and r["session_key"] != session_key
                    or entry["status"] != "candidate"
                    or time.time() - entry["created_at"] > TTL_SECONDS
                ):
                    continue
                path_terms = set(re.findall(r"[\w./-]{3,}", r["canonical_relative_path"].casefold()))
                score = len(terms & path_terms) + (
                    10 if r["canonical_relative_path"].casefold() in query.casefold() else 0
                )
                if score:
                    ranked.append((score, entry))
            for _, entry in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:MAX_HITS]:
                try:
                    r = entry["receipt"]
                    _, raw = self.snapshot(r["canonical_relative_path"])
                    metrics["validation_bytes"] += len(raw)
                    if digest(raw) != r["source_digest"]:
                        continue
                    lines = source_lines(raw)
                    quotes = r["excerpts"]
                    if not quotes or sum(len(q["exact_text"].encode()) + 1 for q in quotes) > MAX_EXCERPT_BYTES:
                        continue
                    for q in quotes:
                        lo, hi = q["start_line"], q["end_line"]
                        if not (1 <= lo <= hi <= len(lines)) or not any(
                            a <= lo <= hi <= b for a, b in r["returned_ranges"]
                        ):
                            raise ValueError("invalid quote range")
                        if "\n".join(lines[lo - 1 : hi]) != q["exact_text"]:
                            raise ValueError("ungrounded quote")
                    body = (
                        f"Historical file observation: {r['canonical_relative_path']}\n"
                        f"sha256={r['source_digest']} ranges={r['returned_ranges']} validated_at={time.time()}\n"
                        "source_version_matched + quote_grounded; semantic_verified=false\n"
                        + "\n".join(f"{q['start_line']}-{q['end_line']}| {q['exact_text']}" for q in quotes)
                    )
                    text = wrap_untrusted(body, source="file-working-evidence")
                    tokens = estimate_prompt_tokens([{"role": "system", "content": "\n".join([*texts, text])}])
                    if tokens <= MAX_TOKENS:
                        used_versions.add((r["canonical_relative_path"], r["source_digest"]))
                        texts.append(text)
                        quote_blocks.append([f"{q['start_line']}| {q['exact_text']}" for q in quotes])
                        metrics["file_working_tokens"] = tokens
                except Exception:
                    continue
            if used_versions:
                try:
                    with file_lock(self.path.parent / "store.lock", blocking=False):
                        current = self._read()
                        for entry in current:
                            r = entry["receipt"]
                            if (r["canonical_relative_path"], r["source_digest"]) in used_versions:
                                entry["last_used_at"] = time.time()
                        atomic_replace(self.path, json.dumps(current, ensure_ascii=False))
                except Exception:
                    pass
            metrics["file_working_hits"] = len(texts)
            metrics["file_working_quote_blocks"] = quote_blocks
            return "\n\n".join(texts), metrics
        except Exception:
            return "", metrics
        finally:
            metrics["validation_ms"] = (time.perf_counter() - started) * 1000
            self.last_metrics = metrics
