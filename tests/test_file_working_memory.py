"""File evidence safety contracts; no model or external service."""

import json
import os
from unittest.mock import patch

import pytest

from looprail.agent.tools.execution import ToolExecutionContext
from looprail.agent.tools.filesystem import ReadFileTool
from looprail.agent.tools.registry import ToolRegistry
from looprail.memory_engine.file_working import (
    MAX_ENTRIES,
    MAX_EXCERPT_BYTES,
    MAX_SOURCE_BYTES,
    MAX_STORAGE_BYTES,
    FileWorkingMemory,
    digest,
)
from looprail.utils.portable_lock import file_lock


@pytest.fixture
def rig(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    service = FileWorkingMemory(repo, tmp_path / "host", access_scope_id="host-user-a")
    reader = ReadFileTool(repo, repo)
    registry = ToolRegistry()
    registry.register(reader)
    registry.bind_file_observer(reader, service)
    return repo, service, registry


async def read(rig, name="app.py", **kwargs):
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    return await rig[2].execute(
        "read_file",
        {"path": name, **kwargs},
        context=ToolExecutionContext(session_key="A", turn_id="turn", call_id="call"),
    )


@pytest.mark.parametrize(
    "raw,offset,limit",
    [
        (b"", 1, None),
        (b"hello\nworld\n", 1, None),
        (b"a\r\nb\r\n", 1, 1),
        ("你好\n再见\n".encode(), 2, 1),
        (b"a\rb\rc", 1, None),
        (b"x" * 128001 + b"\ntail", 1, None),
        ((b"x" * 100 + b"\n") * 2000, 1, None),
        (b"one\ntwo\nthree", 2, 1),
        (b"a", 4, None),
    ],
    ids=["empty", "full", "crlf", "utf8", "cr", "long_line", "truncated", "page", "eof"],
)
async def test_output_and_real_ranges(rig, raw, offset, limit):
    repo, service, registry = rig
    (repo / "app.py").write_bytes(raw)
    old = await ReadFileTool(repo, repo).execute("app.py", offset=offset, limit=limit)
    result = await read(rig, offset=offset, limit=limit)
    assert str(result).startswith(str(old))
    receipt = getattr(result, "file_observation", None)
    if str(old).startswith("Error"):
        assert receipt is None
        return
    assert str(result) == str(old)
    assert receipt.source_digest == digest(raw)
    assert receipt.byte_length == len(raw)
    assert sum(len(q.exact_text.encode()) + 1 for q in receipt.excerpts) <= MAX_EXCERPT_BYTES
    for q in receipt.excerpts:
        assert f"{q.start_line}| {q.exact_text}" in result
    if offset > 1 or limit or len(raw) > 128000:
        assert not receipt.complete_file_observed


@pytest.mark.parametrize("mutation", ["edit", "same_stat", "delete", "rename", "touch"])
async def test_current_full_version(rig, mutation):
    repo, service, _ = rig
    path = repo / "app.py"
    path.write_text("old value\n", encoding="utf-8")
    await read(rig)
    assert service.recall("app.py", "B")[1]["file_working_hits"] == 1
    before = path.stat()
    if mutation in {"edit", "same_stat"}:
        path.write_text("new value\n", encoding="utf-8")
        if mutation == "same_stat":
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif mutation == "delete":
        path.unlink()
    elif mutation == "rename":
        path.rename(repo / "other.py")
    else:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 100000000))
    text, metrics = service.recall("app.py", "B")
    assert metrics["file_working_hits"] == (1 if mutation == "touch" else 0)
    assert "validation_ms" in metrics


@pytest.mark.parametrize(
    "name", [".env", ".env.local", "credentials.json", "private.key", "id_rsa", "auth.json", "secrets.txt"]
)
async def test_sensitive_sources_never_persist(rig, name):
    repo, service, _ = rig
    (repo / name).write_text("sensitive example", encoding="utf-8")
    assert not (await read(rig, name)).failed
    assert not service.path.exists()


async def test_scope_repo_worktree_and_session_isolation(rig, tmp_path):
    repo, service, _ = rig
    (repo / "app.py").write_text("def hello(): pass")
    await read(rig)
    assert FileWorkingMemory(repo, tmp_path / "host", access_scope_id="host-user-a").recall("app.py", "B")[0]
    assert not FileWorkingMemory(repo, tmp_path / "host", access_scope_id="host-user-b").recall("app.py", "B")[0]
    other = tmp_path / "worktree"
    other.mkdir()
    (other / "app.py").write_text("def hello(): pass")
    assert not FileWorkingMemory(other, tmp_path / "host", access_scope_id="host-user-a").recall("app.py", "B")[0]
    local = FileWorkingMemory(repo, tmp_path / "host")
    local.observe(
        local.receipt(repo / "app.py", (repo / "app.py").read_bytes(), 0, 1, ToolExecutionContext(session_key="A"))
    )
    assert local.recall("app.py", "A")[0]
    assert not local.recall("app.py", "B")[0]


@pytest.mark.parametrize("failure", ["corrupt", "schema", "oversized", "quote", "scope", "range", "lock", "permission"])
async def test_cache_and_validation_fail_closed(rig, failure):
    repo, service, _ = rig
    (repo / "app.py").write_text("original\n")
    await read(rig)
    entries = json.loads(service.path.read_text(encoding="utf-8"))
    if failure == "corrupt":
        service.path.write_text("{")
    elif failure == "oversized":
        service.path.write_bytes(b" " * (MAX_STORAGE_BYTES + 1))
    elif failure in {"schema", "quote", "scope", "range"}:
        if failure == "schema":
            entries[0]["schema_version"] = 99
        elif failure == "quote":
            entries[0]["receipt"]["excerpts"][0]["exact_text"] = "forged"
        elif failure == "scope":
            entries[0]["receipt"]["access_scope_id"] = "forged"
        else:
            entries[0]["receipt"]["returned_ranges"] = [[10, 20]]
        service.path.write_text(json.dumps(entries), encoding="utf-8")
    elif failure == "lock":
        with file_lock(service.path.parent / "store.lock", blocking=False):
            assert not service.recall("app.py", "B")[0]
        return
    else:
        with patch.object(service, "snapshot", side_effect=PermissionError):
            assert not service.recall("app.py", "B")[0]
        return
    assert not service.recall("app.py", "B")[0]


async def test_bounds_unrelated_binary_large_and_traversal(rig):
    repo, service, _ = rig
    with pytest.raises(ValueError):
        service.eligible_path("../repo/app.py")
    with pytest.raises(ValueError):
        FileWorkingMemory(repo, repo / ".cache")
    for raw in [b"\x00hello", b"x" * (MAX_SOURCE_BYTES + 1)]:
        (repo / "app.py").write_bytes(raw)
        await read(rig)
        assert not service.path.exists()
    for index in range(MAX_ENTRIES + 2):
        name = f"file{index}.py"
        (repo / name).write_text("text\n")
        await read(rig, name)
    entries = json.loads(service.path.read_text(encoding="utf-8"))
    assert len(entries) == MAX_ENTRIES
    assert service.path.stat().st_size <= MAX_STORAGE_BYTES
    assert not service.recall("unrelated question", "B")[0]


async def test_links_and_windows_junction(rig, tmp_path):
    repo, service, _ = rig
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("outside")
    if os.name == "nt":
        import subprocess

        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(repo / "alias"), str(target)], capture_output=True)
        assert result.returncode == 0
    else:
        (repo / "alias").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        service.eligible_path("alias/app.py")


async def test_same_snapshot_and_write_failure(rig):
    repo, service, _ = rig
    path = repo / "app.py"
    path.write_bytes(b"old\n")
    original = service.receipt

    def mutate(*args):
        path.write_text("new\n")
        return original(*args)

    with patch.object(service, "receipt", side_effect=mutate):
        result = await read(rig)
    assert "old" in result
    assert result.file_observation.source_digest == digest(b"old\n")
    assert not service.recall("app.py", "B")[0]
    with patch("looprail.memory_engine.file_working.atomic_replace", side_effect=OSError("disk full")):
        assert not (await read(rig)).failed


async def test_case_aliases_candidates_and_touch_metadata(rig):
    repo, service, _ = rig
    for index in range(5):
        name = f"app{index}.py"
        (repo / name).write_text("def retry(): pass\n", encoding="utf-8")
        await read(rig, name)
    before = json.loads(service.path.read_text(encoding="utf-8"))
    text, metrics = service.recall("app0.py app1.py app2.py app3.py app4.py", "B")
    assert 0 < metrics["file_working_hits"] <= 3
    assert metrics["file_working_tokens"] <= 1500
    after = json.loads(service.path.read_text(encoding="utf-8"))
    assert any(a["last_used_at"] > b["last_used_at"] for a, b in zip(after, before))
    if os.name == "nt":
        result = await read(rig, "APP0.PY")
        assert result.file_observation.canonical_relative_path == "app0.py"


async def test_concurrent_change_and_expired_evidence(rig):
    from looprail.memory_engine.file_working import TTL_SECONDS, stable_bytes

    repo, service, _ = rig
    path = repo / "app.py"
    path.write_bytes(b"before")
    original = os.fstat
    called = 0

    def changing(fd):
        nonlocal called
        called += 1
        if called == 2:
            path.write_bytes(b"after and longer")
        return original(fd)

    with patch("looprail.memory_engine.file_working.os.fstat", side_effect=changing):
        with pytest.raises(ValueError, match="unstable"):
            stable_bytes(path)
    await read(rig)
    data = json.loads(service.path.read_text(encoding="utf-8"))
    data[0]["created_at"] -= TTL_SECONDS + 1
    service.path.write_text(json.dumps(data), encoding="utf-8")
    assert not service.recall("app.py", "B")[0]
