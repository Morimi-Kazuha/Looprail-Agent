"""Real local CLI assembly and subprocess tests; only Provider is substituted."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from looprail.cli._file_working_scope import local_cli_file_scope
from looprail.cli._runtime_assembly import assemble_runtime
from looprail.config.loader import set_config_path
from looprail.config.looprail import FileWorkingConfig, LooprailConfig, MemoryConfig, load_looprail_config
from looprail.config.paths import RuntimePaths
from looprail.config.schema import Config
from looprail.memory_engine.file_working import FileWorkingMemory


@pytest.fixture
def private_home():
    # Use a disposable private profile directory; OS Temp can have shared ACLs.
    import ctypes

    profile = ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.shell32.SHGetFolderPathW(None, 0x28, None, 0, profile) == 0
    profile_root = Path(profile.value)
    with tempfile.TemporaryDirectory(prefix="looprail-p31-", dir=profile_root) as value:
        root = Path(value)
        assert root.resolve().parent == profile_root.resolve()
        assert root.name.startswith("looprail-p31-")
        yield root


def provider(read=False):
    import runpy

    return runpy.run_path(str(Path(__file__).with_name("test_file_working_runtime.py")))["Provider"](read=read)


@pytest.mark.parametrize("key", ["file_working", "fileWorking"])
def test_disk_config_enable_and_default(tmp_path, key):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({key: {"enabled": True}}))
    try:
        assert load_looprail_config(config).file_working.enabled
        config.write_text("{}")
        assert not load_looprail_config(config).file_working.enabled
    finally:
        set_config_path(None)


@pytest.mark.skipif(os.name != "nt", reason="Windows principal contract")
def test_os_principal_stable_private_root_and_insecure_root(private_home, tmp_path, monkeypatch):
    first = local_cli_file_scope(private_home, tmp_path)
    assert first is not None
    monkeypatch.setenv("USERNAME", "forged-user")
    monkeypatch.setenv("USER", "forged-user")
    assert local_cli_file_scope(private_home, tmp_path) == first
    another = private_home / "other"
    another.mkdir()
    assert local_cli_file_scope(another, tmp_path) != first
    assert local_cli_file_scope(private_home, private_home) is None
    with patch("looprail.cli._file_working_scope.subprocess.run", side_effect=OSError):
        assert local_cli_file_scope(private_home, tmp_path) is None


@pytest.mark.parametrize("enabled,local", [(False, True), (True, False)])
async def test_other_hosts_and_disabled_never_probe(tmp_path, enabled, local):
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "host"
    state.mkdir()
    cfg = Config()
    ec = LooprailConfig(memory=MemoryConfig(backend=None), file_working=FileWorkingConfig(enabled=enabled))
    with patch(
        "looprail.cli._file_working_scope.local_cli_file_scope", side_effect=AssertionError("unexpected identity probe")
    ):
        runtime = assemble_runtime(
            cfg,
            ec,
            provider=provider(),
            cron_service=None,
            interactive=False,
            paths=RuntimePaths(repo, state),
            local_cli=local,
        )
    try:
        if enabled:
            assert not runtime.agent_loop.file_working.cross_session
        else:
            assert runtime.agent_loop.file_working is None
        assert not list(state.glob("**/entries.json"))
    finally:
        await runtime.close()


async def test_unavailable_identity_skips_cli_cache(tmp_path):
    repo, state = tmp_path / "repo", tmp_path / "host"
    repo.mkdir()
    state.mkdir()
    ec = LooprailConfig(memory=MemoryConfig(backend=None), file_working=FileWorkingConfig(enabled=True))
    with patch("looprail.cli._file_working_scope.local_cli_file_scope", return_value=None):
        runtime = assemble_runtime(
            Config(),
            ec,
            provider=provider(),
            cron_service=None,
            interactive=False,
            paths=RuntimePaths(repo, state),
            local_cli=True,
        )
    try:
        assert runtime.agent_loop.file_working is None
        assert not list(state.glob("**/entries.json"))
    finally:
        await runtime.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_real_acl_shared_state_is_rejected(private_home, tmp_path):
    import base64

    assert local_cli_file_scope(private_home, tmp_path)
    # Modify ONLY this fixture's disposable directory. Production never changes
    # permissions. BUILTIN Users data-read permission must reject admission.
    code = r"""
    $p = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $acl = [IO.Directory]::GetAccessControl($p.path)
    $sid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'ReadAndExecute', 'Allow')
    $acl.AddAccessRule($rule)
    [IO.Directory]::SetAccessControl($p.path, $acl)
    """
    result = subprocess.run(
        [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(code.encode("utf-16-le")).decode(),
        ],
        input=json.dumps({"path": str(private_home)}).encode(),
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert local_cli_file_scope(private_home, tmp_path) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows principal contract")
async def test_real_cli_factory_cross_session(private_home, tmp_path):
    from looprail.spine import ChatType, Origin, Source, TurnRequest

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def actual_cli(): return 17\n")
    config = Config()
    config.agents.defaults.model = "fake"
    ec = LooprailConfig(memory=MemoryConfig(backend=None), file_working=FileWorkingConfig(enabled=True))

    def req(name):
        return TurnRequest(
            origin=Origin.USER,
            source=Source(channel="cli", chat_id=name, sender_id="user", chat_type=ChatType.DM),
            text="Explain app.py",
        )

    p = provider(read=True)
    runtime = assemble_runtime(
        config,
        ec,
        provider=p,
        cron_service=None,
        interactive=False,
        paths=RuntimePaths(repo, private_home),
        local_cli=True,
    )
    try:
        assert runtime.agent_loop.file_working.cross_session
        await runtime.agent_loop._process_message(req("A"), session_key="cli:A")
        cache = runtime.agent_loop.file_working.path
        assert cache.exists()
        scope = runtime.agent_loop.file_working.scope
    finally:
        await runtime.close()
    p = provider()
    runtime = assemble_runtime(
        config,
        ec,
        provider=p,
        cron_service=None,
        interactive=False,
        paths=RuntimePaths(repo, private_home),
        local_cli=True,
    )
    try:
        assert runtime.agent_loop.file_working.scope == scope
        await runtime.agent_loop._process_message(req("B"), session_key="cli:B")
        assert "file-working-evidence" in json.dumps(p.calls[-1])
        assert "def actual_cli()" in json.dumps(p.calls[-1])
        other = tmp_path / "other-repo"
        other.mkdir()
        (other / "app.py").write_text("def actual_cli(): return 17\n")
        assert not FileWorkingMemory(
            other, private_home, access_scope_id=local_cli_file_scope(private_home, repo)
        ).recall("app.py", "B")[0]
        # Another principal cannot discover the first principal's partition.
        assert not FileWorkingMemory(repo, private_home, access_scope_id="different-test-principal").recall(
            "app.py", "B"
        )[0]
        (repo / "app.py").write_text("changed\n")
        await runtime.agent_loop._process_message(req("C"), session_key="cli:C")
        assert "file-working-evidence" not in json.dumps(p.calls[-1])
        assert len(p.calls) == 2
    finally:
        await runtime.close()


_CLI_CHILD = r"""
import json, os, runpy, sys
from pathlib import Path
from looprail.providers.base import LLMProvider, LLMResponse, ToolCallRequest
import looprail.cli.agent_commands as cli
from looprail.token_wise import pricing
pricing._fetch_openrouter_models=lambda: {}
class Deterministic(LLMProvider):
    def __init__(self):
        super().__init__(api_key="offline-fake")
        self.calls=[]
    def get_default_model(self): return "fake"
    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append(messages)
        Path(os.environ['P31_MESSAGES']).write_text(json.dumps(self.calls,ensure_ascii=True),encoding='utf-8')
        if os.environ['P31_READ']=='1' and len(self.calls)==1:
            return LLMResponse(content="",tool_calls=[ToolCallRequest(id="cli-read",name="read_file",arguments={"path":"app.py"})])
        return LLMResponse(content="offline done")
cli.make_provider=lambda config: Deterministic()
runpy.run_module('looprail',run_name='__main__')
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows principal contract")
@pytest.mark.parametrize("enabled", [True, False])
def test_cli_subprocess_two_invocations(private_home, tmp_path, enabled):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def cli_evidence(): return 42\n")
    config = private_home / "config.json"
    config.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": "fake"}},
                "fileWorking": {"enabled": enabled},
                "memory": {"backend": None},
                "tools": {"restrictToWorkspace": True},
            }
        )
    )
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1])
    env.update(
        LOOPRAIL_HOME=str(private_home),
        PYTHONPATH=source,
        PYTHONIOENCODING="utf-8",
        LOOPRAIL_TRACING_DIR=str(private_home / "trace"),
    )

    def invoke(name, read=False):
        output = private_home / f"messages-{name}.json"
        env.update(P31_MESSAGES=str(output), P31_READ="1" if read else "0")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _CLI_CHILD,
                "run",
                "--config",
                str(config),
                "--session",
                f"cli:{name}",
                "-m",
                "Explain app.py",
                "--no-markdown",
                "--verbosity",
                "quiet",
            ],
            cwd=repo,
            env=env,
            capture_output=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        return json.loads(output.read_text(encoding="utf-8"))

    a = invoke("A", read=True)
    assert len(a) == 2
    caches = list(private_home.glob("projects/*/file-working/*/*/entries.json"))
    assert len(caches) == (1 if enabled else 0)
    b = invoke("B")
    assert len(b) == 1
    assert ("file-working-evidence" in json.dumps(b)) == enabled
    if enabled:
        assert "def cli_evidence()" in json.dumps(b)
        data = json.loads(caches[0].read_text(encoding="utf-8"))
        assert data[0]["receipt"]["session_key"] == "cli:A"
    (repo / "app.py").write_text("changed source\n")
    c = invoke("C")
    assert len(c) == 1
    assert "file-working-evidence" not in json.dumps(c)
