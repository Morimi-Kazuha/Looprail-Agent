from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from scripts import verify_distribution

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_distribution.py"


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    return verify_distribution._isolated_environment(tmp_path / "home", tmp_path / "cache")


def test_release_dist_target_uses_fail_closed_distribution_verifier(tmp_path: Path) -> None:
    result = subprocess.run(
        ["make", "-n", "release-dist"],
        cwd=REPO_ROOT,
        env={**os.environ, "PICO_RELEASE_OUTPUT": str(tmp_path / "release")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scripts/verify_distribution.py" in result.stdout
    assert "--extras base,channel-feishu,channel-qq,channel-wecom,channels,sandbox" in result.stdout


def _write_wheel(
    path: Path,
    extra_files: dict[str, str] | None = None,
    remove_files: set[str] | None = None,
) -> Path:
    files = {
        "pico/__init__.py": "",
        "pico/templates/AGENTS.md": "# Agents",
        "pico/templates/SOUL.md": "# Soul",
        "pico/tracing/viewer/server.js": "",
        "pico/tracing/viewer/ui/app.js": "",
        "pico/ui-tui/dist/entry.js": "",
        "pico_harness-0.1.7.dist-info/METADATA": (
            "Metadata-Version: 2.4\n"
            "Name: pico-harness\n"
            "Version: 0.1.7\n"
            "Provides-Extra: channel-feishu\n"
            "Provides-Extra: channel-qq\n"
            "Provides-Extra: channel-wecom\n"
            "Provides-Extra: channels\n"
            "Provides-Extra: sandbox\n"
        ),
        "pico_harness-0.1.7.dist-info/entry_points.txt": "[console_scripts]\npico = pico.cli.commands:run\n",
        "pico_harness-0.1.7.dist-info/licenses/LICENSE": "Apache-2.0",
        "pico_harness-0.1.7.dist-info/licenses/LICENSES/MIT-hermes-agent.txt": "MIT",
        "pico_harness-0.1.7.dist-info/licenses/LICENSES/MIT-ink.txt": "MIT",
        "pico_harness-0.1.7.dist-info/licenses/LICENSES/MIT-nanobot.txt": "MIT",
        "pico_harness-0.1.7.dist-info/licenses/LICENSES/README.md": "# Third-party licenses",
        "pico_harness-0.1.7.dist-info/licenses/NOTICES.md": "# Notices",
        "pico_harness-0.1.7.dist-info/RECORD": "",
    }
    files.update(extra_files or {})
    for name in remove_files or set():
        files.pop(name)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _write_sdist(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            body = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return path


def _write_fake_gateway(
    path: Path,
    *,
    health_body: str = '{"status":"ok"}',
    ignore_shutdown: bool = False,
) -> Path:
    signal_setup = (
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)"
        if ignore_shutdown
        else "signal.signal(signal.SIGINT, stop)"
    )
    path.write_text(
        f"""#!{sys.executable}
import http.server
import signal
import sys

if "--check" in sys.argv:
    print("TUI check passed")
    raise SystemExit(0)

port = int(sys.argv[sys.argv.index("--port") + 1])

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {health_body!r}.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

def stop(*_args):
    raise SystemExit(0)

{signal_setup}
server = http.server.HTTPServer(("127.0.0.1", port), Handler)
try:
    server.serve_forever()
finally:
    server.server_close()
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_rejects_nonempty_output_root_without_deleting_contents(tmp_path: Path) -> None:
    output_root = tmp_path / "probe"
    output_root.mkdir()
    sentinel = output_root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output-root",
            str(output_root),
            "--entrypoint",
            "pico",
            "--extras",
            "base",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "output root must be empty" in result.stderr.lower()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_rejects_output_root_inside_checkout() -> None:
    output_root = REPO_ROOT / ".distribution-probe-test"
    assert not output_root.exists()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output-root",
            str(output_root),
            "--entrypoint",
            "pico",
            "--extras",
            "base",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "outside the source checkout" in result.stderr.lower()
    assert not output_root.exists()


def test_wheel_manifest_rejects_node_modules(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {"pico/node_modules/typescript/package.json": "{}"},
    )

    with pytest.raises(verify_distribution.VerificationError, match="node_modules"):
        snapshot = verify_distribution._wheel_snapshot(wheel)
        verify_distribution._validate_wheel(snapshot, "pico")


@pytest.mark.parametrize(
    "artifact",
    [
        "pico/package.json",
        "pico/package-lock.json",
        "pico/.npmrc",
        "pico/npm-debug.log",
        "pico/reports/distribution.html",
        "ui-tui/src/entry.tsx",
        "pico/generated/tool.ts",
        "pico/distribution-report.json",
        "pico/verification-report.html",
        "pico/htmlcov/index.html",
        "pico/coverage.xml",
    ],
)
def test_wheel_manifest_rejects_local_build_and_report_artifacts(
    tmp_path: Path,
    artifact: str,
) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {artifact: ""},
    )

    with pytest.raises(
        verify_distribution.VerificationError,
        match="local build or report artifact",
    ):
        verify_distribution._validate_wheel(
            verify_distribution._wheel_snapshot(wheel),
            "pico",
        )


def test_wheel_manifest_requires_complete_attribution_set(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        remove_files={"pico_harness-0.1.7.dist-info/licenses/NOTICES.md"},
    )

    with pytest.raises(verify_distribution.VerificationError, match="required attribution"):
        verify_distribution._validate_wheel(
            verify_distribution._wheel_snapshot(wheel),
            "pico",
        )


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico/bridge/package.json",
        "pico/channels/adapters/whatsapp/channel.py",
        "pico/channels/adapters/telegram/channel.py",
        "pico/channels/adapters/slack/channel.py",
        "pico/channels/adapters/discord/channel.py",
        "pico/channels/adapters/matrix/channel.py",
        "pico/channels/adapters/mochat/channel.py",
        "pico/channels/adapters/dingtalk/channel.py",
        "pico/channels/adapters/email/channel.py",
        "pico/channels/adapters/weixin/channel.py",
    ],
)
def test_wheel_manifest_rejects_removed_channel_runtime(tmp_path: Path, runtime_file: str) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {runtime_file: ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Channel runtime"):
        snapshot = verify_distribution._wheel_snapshot(wheel)
        verify_distribution._validate_wheel(snapshot, "pico")


def test_wheel_manifest_rejects_removed_media_generation_runtime(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {"pico/agent/tools/media_gen.py": ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed media generation runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico/skill_hub/client.py",
        "pico/agent/tools/skill_hub.py",
        "pico/memory_engine/skill_forge/hub_source.py",
    ],
)
def test_wheel_manifest_rejects_removed_skill_hub_runtime(tmp_path: Path, runtime_file: str) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {runtime_file: ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Skill Hub runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico/agent/tools/deep_research.py",
        "pico/cli/deep_research_commands.py",
        "pico/config/update_tools.py",
    ],
)
def test_wheel_manifest_rejects_removed_deep_research_runtime(tmp_path: Path, runtime_file: str) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {runtime_file: ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Deep Research runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico/proactive_engine/sentinel/__init__.py",
        "pico/cli/sentinel_commands.py",
        "pico/cli/_proactive_stack.py",
        "pico/memory_engine/consolidate/behaviors_extractor.py",
    ],
)
def test_wheel_manifest_rejects_removed_sentinel_runtime(tmp_path: Path, runtime_file: str) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {runtime_file: ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Sentinel runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico/proactive_engine/schedulers/heartbeat/service.py",
        "pico/proactive_engine/system_events.py",
        "pico/proactive_engine/wake.py",
        "pico/templates/HEARTBEAT.md",
    ],
)
def test_wheel_manifest_rejects_removed_heartbeat_runtime(tmp_path: Path, runtime_file: str) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {runtime_file: ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Heartbeat runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


def test_wheel_manifest_rejects_removed_cli_runtime(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {"pico/cli/upgrade_commands.py": ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed CLI runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico_harness-0.1.7/bridge/package.json",
        "pico_harness-0.1.7/pico/channels/adapters/whatsapp/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/telegram/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/slack/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/discord/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/matrix/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/mochat/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/dingtalk/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/email/channel.py",
        "pico_harness-0.1.7/pico/channels/adapters/weixin/channel.py",
    ],
)
def test_sdist_manifest_rejects_removed_channel_runtime(tmp_path: Path, runtime_file: str) -> None:
    sdist = _write_sdist(tmp_path / "pico_harness-0.1.7.tar.gz", {runtime_file: ""})

    with pytest.raises(verify_distribution.VerificationError, match="sdist contains removed Channel runtime"):
        verify_distribution._validate_sdist(sdist)


def test_sdist_manifest_rejects_removed_media_generation_runtime(tmp_path: Path) -> None:
    sdist = _write_sdist(
        tmp_path / "pico_harness-0.1.7.tar.gz",
        {"pico_harness-0.1.7/pico/agent/tools/media_gen.py": ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed media generation runtime"):
        verify_distribution._validate_sdist(sdist)


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico_harness-0.1.7/pico/skill_hub/client.py",
        "pico_harness-0.1.7/pico/agent/tools/skill_hub.py",
        "pico_harness-0.1.7/pico/memory_engine/skill_forge/hub_source.py",
    ],
)
def test_sdist_manifest_rejects_removed_skill_hub_runtime(tmp_path: Path, runtime_file: str) -> None:
    sdist = _write_sdist(tmp_path / "pico_harness-0.1.7.tar.gz", {runtime_file: ""})

    with pytest.raises(verify_distribution.VerificationError, match="removed Skill Hub runtime"):
        verify_distribution._validate_sdist(sdist)


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico_harness-0.1.7/pico/agent/tools/deep_research.py",
        "pico_harness-0.1.7/pico/cli/deep_research_commands.py",
        "pico_harness-0.1.7/pico/config/update_tools.py",
    ],
)
def test_sdist_manifest_rejects_removed_deep_research_runtime(tmp_path: Path, runtime_file: str) -> None:
    sdist = _write_sdist(tmp_path / "pico_harness-0.1.7.tar.gz", {runtime_file: ""})

    with pytest.raises(verify_distribution.VerificationError, match="removed Deep Research runtime"):
        verify_distribution._validate_sdist(sdist)


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico_harness-0.1.7/pico/proactive_engine/sentinel/__init__.py",
        "pico_harness-0.1.7/pico/cli/sentinel_commands.py",
        "pico_harness-0.1.7/pico/cli/_proactive_stack.py",
        "pico_harness-0.1.7/pico/memory_engine/consolidate/behaviors_extractor.py",
    ],
)
def test_sdist_manifest_rejects_removed_sentinel_runtime(tmp_path: Path, runtime_file: str) -> None:
    sdist = _write_sdist(tmp_path / "pico_harness-0.1.7.tar.gz", {runtime_file: ""})

    with pytest.raises(verify_distribution.VerificationError, match="removed Sentinel runtime"):
        verify_distribution._validate_sdist(sdist)


@pytest.mark.parametrize(
    "runtime_file",
    [
        "pico_harness-0.1.7/pico/proactive_engine/schedulers/heartbeat/service.py",
        "pico_harness-0.1.7/pico/proactive_engine/system_events.py",
        "pico_harness-0.1.7/pico/proactive_engine/wake.py",
        "pico_harness-0.1.7/pico/templates/HEARTBEAT.md",
    ],
)
def test_sdist_manifest_rejects_removed_heartbeat_runtime(tmp_path: Path, runtime_file: str) -> None:
    sdist = _write_sdist(tmp_path / "pico_harness-0.1.7.tar.gz", {runtime_file: ""})

    with pytest.raises(verify_distribution.VerificationError, match="removed Heartbeat runtime"):
        verify_distribution._validate_sdist(sdist)


def test_sdist_manifest_rejects_removed_cli_runtime(tmp_path: Path) -> None:
    sdist = _write_sdist(
        tmp_path / "pico_harness-0.1.7.tar.gz",
        {"pico_harness-0.1.7/pico/cli/upgrade_commands.py": ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="sdist contains removed CLI runtime"):
        verify_distribution._validate_sdist(sdist)


def test_wheel_metadata_rejects_removed_channel_extra(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {
            "pico_harness-0.1.7.dist-info/METADATA": (
                "Metadata-Version: 2.4\nName: pico-harness\nVersion: 0.1.7\nProvides-Extra: channel-telegram\n"
            )
        },
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Channel extra"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


def test_wheel_metadata_requires_exact_retained_channel_extras(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {
            "pico_harness-0.1.7.dist-info/METADATA": (
                "Metadata-Version: 2.4\nName: pico-harness\nVersion: 0.1.7\n"
                "Provides-Extra: channel-feishu\n"
                "Provides-Extra: channel-qq\n"
                "Provides-Extra: channel-wecom\n"
                "Provides-Extra: channel-experimental\n"
            )
        },
    )

    with pytest.raises(verify_distribution.VerificationError, match="exactly the retained Channel extras"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


def test_wheel_metadata_rejects_removed_channel_dependency(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {
            "pico_harness-0.1.7.dist-info/METADATA": (
                "Metadata-Version: 2.4\nName: pico-harness\nVersion: 0.1.7\n"
                "Provides-Extra: channel-feishu\n"
                "Provides-Extra: channel-qq\n"
                "Provides-Extra: channel-wecom\n"
                "Requires-Dist: slack-sdk>=3.39\n"
            )
        },
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed Channel package"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


def test_wheel_rejects_bundled_everos_runtime(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {"pico/plugin/memory/everos/backend.py": ""},
    )

    with pytest.raises(verify_distribution.VerificationError, match="removed EverOS runtime"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


def test_wheel_rejects_direct_everos_dependency(tmp_path: Path) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        {
            "pico_harness-0.1.7.dist-info/METADATA": (
                "Metadata-Version: 2.4\n"
                "Name: pico-harness\n"
                "Version: 0.1.7\n"
                "Provides-Extra: channel-feishu\n"
                "Provides-Extra: channel-qq\n"
                "Provides-Extra: channel-wecom\n"
                "Requires-Dist: everos==1.1.2\n"
            )
        },
    )

    with pytest.raises(verify_distribution.VerificationError, match="depends directly on removed package"):
        verify_distribution._validate_wheel(verify_distribution._wheel_snapshot(wheel), "pico")


@pytest.mark.parametrize(
    ("extra_files", "remove_files"),
    [
        ({"pico/ui-tui/src/app.ts": ""}, set()),
        ({}, {"pico/ui-tui/dist/entry.js"}),
    ],
)
def test_wheel_manifest_requires_exactly_one_tui_bundle(
    tmp_path: Path,
    extra_files: dict[str, str],
    remove_files: set[str],
) -> None:
    wheel = _write_wheel(
        tmp_path / "pico_harness-0.1.7-py3-none-any.whl",
        extra_files,
        remove_files,
    )

    with pytest.raises(verify_distribution.VerificationError, match="exactly one TUI bundle"):
        snapshot = verify_distribution._wheel_snapshot(wheel)
        verify_distribution._validate_wheel(snapshot, "pico")


def test_base_and_extras_use_distinct_environment_roots(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "pico_harness-0.1.7-py3-none-any.whl")
    snapshot = verify_distribution._wheel_snapshot(wheel)

    plans = verify_distribution._build_install_plans(
        tmp_path / "environments",
        wheel,
        (
            "base",
            "channel-feishu",
            "channel-qq",
            "channel-wecom",
            "channels",
            "sandbox",
        ),
        snapshot,
    )

    assert [plan.name for plan in plans] == [
        "base",
        "channel-feishu",
        "channel-qq",
        "channel-wecom",
        "channels",
        "sandbox",
    ]
    assert len({plan.root for plan in plans}) == 6
    assert plans[0].requirement == str(wheel)
    assert plans[1].requirement == f"{wheel}[channel-feishu]"
    assert set(plans[1].forbidden_imports) == {"botpy", "boxlite", "wecom_aibot_sdk"}
    assert plans[-1].required_imports == ("boxlite",)
    assert set(plans[-1].forbidden_imports) == {"botpy", "lark_oapi", "wecom_aibot_sdk"}


@pytest.mark.parametrize(
    "targets",
    [
        ("base", "channel-feishu", "channel-qq", "channel-wecom", "channels"),
        ("base", "channel-feishu", "channel-qq", "channel-wecom", "sandbox"),
        ("base", "channel-feishu", "channel-qq", "channel-wecom", "channels", "channels"),
        ("base", "channel-feishu", "channel-qq", "channel-wecom", "channels", "tools"),
    ],
)
def test_install_plans_require_all_six_canonical_targets(
    tmp_path: Path,
    targets: tuple[str, ...],
) -> None:
    wheel = _write_wheel(tmp_path / "pico_harness-0.1.7-py3-none-any.whl")

    with pytest.raises(
        verify_distribution.VerificationError,
        match="exactly the canonical targets",
    ):
        verify_distribution._build_install_plans(
            tmp_path / "environments",
            wheel,
            targets,
            verify_distribution._wheel_snapshot(wheel),
        )


def test_probe_environment_isolates_home_pythonpath_and_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/test/bin")
    monkeypatch.setenv("PYTHONPATH", "/leaked/checkout")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("LARK_APP_SECRET", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY_PICO", "secret")
    monkeypatch.setenv("OPENAI_API_KEY_PICO", "secret")
    monkeypatch.setenv("NPM_TOKEN", "secret")
    monkeypatch.setenv("UV_INDEX_PASSWORD", "secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")

    env = verify_distribution._isolated_environment(
        tmp_path / "home",
        tmp_path / "cache",
    )

    assert env["HOME"] == str(tmp_path / "home")
    assert env["PATH"] == "/test/bin"
    assert env["PYTHONPATH"] == ""
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["npm_config_cache"] == str(tmp_path / "cache" / "npm")
    assert env["UV_CACHE_DIR"] == str(tmp_path / "cache" / "uv")
    for name in (
        "DEEPSEEK_API_KEY",
        "LARK_APP_SECRET",
        "ANTHROPIC_API_KEY_PICO",
        "OPENAI_API_KEY_PICO",
        "NPM_TOKEN",
        "UV_INDEX_PASSWORD",
        "SSH_AUTH_SOCK",
    ):
        assert name not in env


def test_run_records_expected_exit_codes_and_acceptance(tmp_path: Path) -> None:
    records: list[dict] = []

    verify_distribution._run(
        [sys.executable, "-c", "raise SystemExit(1)"],
        cwd=tmp_path,
        log_dir=tmp_path / "logs",
        records=records,
        env=_isolated_env(tmp_path),
        expected=(1,),
    )

    assert records[0]["exit_code"] == 1
    assert records[0]["expected_exit_codes"] == [1]
    assert records[0]["accepted"] is True
    assert records[0]["outcome"] == "completed"


@pytest.mark.parametrize(
    ("command", "timeout", "outcome"),
    [
        ([sys.executable, "-c", "import time; time.sleep(1)"], 0.01, "timeout"),
        (["missing-distribution-verifier-command"], 1, "spawn_error"),
    ],
)
def test_run_records_launch_failures(
    tmp_path: Path,
    command: list[str],
    timeout: float,
    outcome: str,
) -> None:
    records: list[dict] = []

    with pytest.raises(verify_distribution.VerificationError, match="command could not run"):
        verify_distribution._run(
            command,
            cwd=tmp_path,
            log_dir=tmp_path / "logs",
            records=records,
            env=_isolated_env(tmp_path),
            timeout=timeout,
        )

    assert len(records) == 1
    assert records[0]["exit_code"] is None
    assert records[0]["accepted"] is False
    assert records[0]["outcome"] == outcome
    assert Path(records[0]["log"]).is_file()


def test_git_source_capture_scrubs_credentials_and_disables_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-secret")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/tmp/untrusted-diff-helper")
    calls: list[tuple[list[str], dict[str, str]]] = []
    run = subprocess.run

    def capture_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        calls.append((command, env))
        return run(command, **kwargs)

    monkeypatch.setattr(verify_distribution.subprocess, "run", capture_run)

    verify_distribution._git_output(
        ["diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--"],
        _isolated_env(tmp_path),
    )

    command, env = calls[0]
    assert command[1:4] == ["-c", "core.fsmonitor=false", "diff"]
    assert "--no-ext-diff" in command
    assert "--no-textconv" in command
    assert "DEEPSEEK_API_KEY" not in env
    assert "GIT_EXTERNAL_DIFF" not in env


def test_source_snapshot_includes_nonignored_files_without_touching_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.name", "Pico Test"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.email", "pico@example.com"], cwd=checkout, check=True)
    (checkout / ".gitignore").write_text(".env\nnode_modules/\ndist/\n", encoding="utf-8")
    (checkout / "tracked.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=checkout, check=True)
    (checkout / "tracked.txt").write_text("after\n", encoding="utf-8")
    (checkout / "new.py").write_text("value = 1\n", encoding="utf-8")
    (checkout / ".env").write_text("API_KEY=ignored\n", encoding="utf-8")
    (checkout / "node_modules").mkdir()
    (checkout / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")
    (checkout / "dist").mkdir()
    (checkout / "dist" / "ignored.js").write_text("ignored\n", encoding="utf-8")
    monkeypatch.setattr(verify_distribution, "REPO_ROOT", checkout)

    env = _isolated_env(tmp_path)
    state = verify_distribution._capture_source_state(env)
    snapshot = tmp_path / "snapshot"
    verify_distribution._copy_source_snapshot(snapshot, state)

    assert state["clean"] is False
    assert "tracked.txt" in state["changed_paths"]
    assert "new.py" in state["changed_paths"]
    assert (snapshot / "tracked.txt").read_text(encoding="utf-8") == "after\n"
    assert (snapshot / "new.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (snapshot / "node_modules").exists()
    assert not (snapshot / "dist").exists()
    assert not (snapshot / ".env").exists()
    assert (checkout / "node_modules" / "ignored.js").exists()


def test_source_state_detects_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.name", "Pico Test"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.email", "pico@example.com"], cwd=checkout, check=True)
    source = checkout / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=checkout, check=True)
    monkeypatch.setattr(verify_distribution, "REPO_ROOT", checkout)
    env = _isolated_env(tmp_path)
    before = verify_distribution._capture_source_state(env)
    source.write_text("value = 2\n", encoding="utf-8")

    with pytest.raises(verify_distribution.VerificationError, match="changed during verification"):
        verify_distribution._assert_source_unchanged(before, env)


@pytest.mark.parametrize("target", ["absolute", "outside", "missing"])
def test_source_manifest_rejects_symlinks_outside_manifest(tmp_path: Path, target: str) -> None:
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    if target == "absolute":
        link_target = str(outside)
    elif target == "outside":
        link_target = "../outside.txt"
    else:
        unlisted = root / "unlisted.txt"
        unlisted.write_text("unlisted\n", encoding="utf-8")
        link_target = "unlisted.txt"
    os.symlink(link_target, root / "link")

    with pytest.raises(verify_distribution.VerificationError, match="escapes the source manifest"):
        verify_distribution._manifest_entries(root, ("link",))


def test_required_import_probe_executes_module_initialization(tmp_path: Path) -> None:
    (tmp_path / "broken_probe.py").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
    code = f"import sys;sys.path.insert(0, {str(tmp_path)!r})\n" + verify_distribution._import_probe_code(
        ("broken_probe",), ()
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(completed.stdout)

    assert report["present"]["broken_probe"] is False
    assert report["errors"]["broken_probe"] == "RuntimeError: broken import"


def test_sdist_wheel_must_match_direct_wheel_resources(tmp_path: Path) -> None:
    (tmp_path / "direct").mkdir()
    direct = verify_distribution._wheel_snapshot(
        _write_wheel(tmp_path / "direct" / "pico_harness-0.1.7-py3-none-any.whl")
    )
    rebuilt_path = tmp_path / "rebuilt" / "pico_harness-0.1.7-py3-none-any.whl"
    rebuilt_path.parent.mkdir()
    rebuilt = verify_distribution._wheel_snapshot(_write_wheel(rebuilt_path, {"pico/templates/SOUL.md": "changed"}))

    with pytest.raises(verify_distribution.VerificationError, match="sdist wheel differs"):
        verify_distribution._verify_wheel_equivalence(direct, rebuilt)


def test_unconfigured_doctor_exit_one_is_expected() -> None:
    expected_path = Path("/isolated/home/.pico/config.json")
    completed = subprocess.CompletedProcess(
        args=["pico", "doctor", "--json"],
        returncode=1,
        stdout=json.dumps(
            {
                "paths": {
                    "config_exists": False,
                    "config_path": str(expected_path),
                }
            }
        ),
        stderr="",
    )

    report = verify_distribution._validate_doctor_probe(completed, expected_path)

    assert report["paths"]["config_exists"] is False


def test_package_probe_requires_contracted_cli_surface(tmp_path: Path) -> None:
    plan = verify_distribution.InstallPlan("base", tmp_path, "pico.whl", ())
    payload = {
        "package": str(tmp_path / "lib" / "pico" / "__init__.py"),
        "tui": str(tmp_path / "lib" / "pico" / "ui-tui" / "dist" / "entry.js"),
        "legacy_namespace_available": False,
        "public_commands": sorted(verify_distribution._PUBLIC_CLI_COMMANDS),
        "registered_commands": sorted(verify_distribution._REGISTERED_CLI_COMMANDS),
    }
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    report = verify_distribution._validate_package_probe(completed, plan)

    assert set(report["public_commands"]) == verify_distribution._PUBLIC_CLI_COMMANDS


def test_package_probe_rejects_removed_public_command(tmp_path: Path) -> None:
    plan = verify_distribution.InstallPlan("base", tmp_path, "pico.whl", ())
    payload = {
        "package": str(tmp_path / "lib" / "pico" / "__init__.py"),
        "tui": str(tmp_path / "lib" / "pico" / "ui-tui" / "dist" / "entry.js"),
        "public_commands": sorted(verify_distribution._PUBLIC_CLI_COMMANDS | {"upgrade"}),
        "registered_commands": sorted(verify_distribution._REGISTERED_CLI_COMMANDS | {"upgrade"}),
    }
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")

    with pytest.raises(verify_distribution.VerificationError, match="public CLI drifted"):
        verify_distribution._validate_package_probe(completed, plan)


def test_pico_module_probe_requires_pico_version_output(tmp_path: Path) -> None:
    plan = verify_distribution.InstallPlan("base", tmp_path, "pico.whl", ())
    healthy = subprocess.CompletedProcess(
        args=[sys.executable, "-I", "-m", "pico", "--version"],
        returncode=0,
        stdout="Pico v0.1.7\n",
        stderr="",
    )

    verify_distribution._validate_pico_module_probe(healthy, plan)

    stale = subprocess.CompletedProcess(
        args=[sys.executable, "-I", "-m", "pico", "--version"],
        returncode=0,
        stdout="Other v0.1.7\n",
        stderr="",
    )
    with pytest.raises(verify_distribution.VerificationError, match="cannot run the Pico module entrypoint"):
        verify_distribution._validate_pico_module_probe(stale, plan)


def test_product_path_probe_requires_pico_roots(tmp_path: Path) -> None:
    import hashlib

    home = tmp_path / "home"
    cwd = tmp_path / "workspace"
    digest = hashlib.sha256(str(cwd.resolve()).encode("utf-8")).hexdigest()[:12]
    report = {
        "product_home": str(home / ".pico"),
        "workspace_state": str(home / ".pico" / "projects" / f"workspace-{digest}"),
        "plugin_user": str(home / ".pico" / "plugins"),
        "plugin_project": None,
        "plugin_entrypoints": "pico.plugins",
    }

    verify_distribution._validate_product_paths(
        report,
        probe_home=home,
        probe_cwd=cwd,
    )

    report["workspace_state"] = str(cwd / ".other")
    with pytest.raises(verify_distribution.VerificationError, match="state roots drifted"):
        verify_distribution._validate_product_paths(
            report,
            probe_home=home,
            probe_cwd=cwd,
        )

    report["workspace_state"] = str(home / ".pico" / "projects" / f"workspace-{digest}")
    report["plugin_project"] = str(cwd / ".pico" / "plugins")
    with pytest.raises(verify_distribution.VerificationError, match="plugin_project"):
        verify_distribution._validate_product_paths(
            report,
            probe_home=home,
            probe_cwd=cwd,
        )


def test_distribution_handoff_publishes_exact_wheel_and_base_environment(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "dist" / "pico_harness-0.1.7-py3-none-any.whl"
    python = tmp_path / "envs" / "base" / "bin" / "python"
    entrypoint = tmp_path / "envs" / "base" / "bin" / "pico"
    for path in (wheel, python, entrypoint):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    environments = [
        {
            "name": "channel-feishu",
            "root": str(tmp_path / "envs" / "channel-feishu"),
            "python": str(tmp_path / "envs" / "channel-feishu" / "bin" / "python"),
            "entrypoint": str(tmp_path / "envs" / "channel-feishu" / "bin" / "pico"),
        },
        {
            "name": "base",
            "root": str(python.parents[1]),
            "python": str(python),
            "entrypoint": str(entrypoint),
        },
    ]

    handoff = verify_distribution._build_distribution_handoff(wheel, environments)

    assert handoff == {
        "wheel": str(wheel),
        "base_environment": {
            "root": str(python.parents[1]),
            "python": str(python),
            "entrypoint": str(entrypoint),
        },
    }


def test_gateway_probe_config_is_offline_and_disables_optional_runtime_paths() -> None:
    config = verify_distribution._gateway_probe_config()

    assert config == {
        "agents": {
            "defaults": {
                "model": "ollama/pico-distribution-probe",
                "provider": "ollama",
            }
        },
        "channels": {
            "feishu": {"enabled": False},
            "qq": {"enabled": False},
            "wecom": {"enabled": False},
        },
        "tools": {
            "sandbox": {"backend": "none"},
        },
        "plugins": {"disabled": []},
        "memory": {"backend": None},
        "skillForge": {
            "enabled": False,
            "router": {"enabled": False},
        },
        "runtime": {"checkpoint": {"policy": "never"}},
        "tracing": {"enabled": False},
    }


def test_gateway_health_requires_the_public_ok_contract() -> None:
    payload = verify_distribution._validate_gateway_health(
        200,
        "application/json",
        b'{"status":"ok"}',
    )

    assert payload == {"status": "ok"}


@pytest.mark.parametrize(
    ("status", "content_type", "body"),
    [
        (503, "application/json", b'{"status":"ok"}'),
        (200, "text/plain", b'{"status":"ok"}'),
        (200, "application/json", b'{"status":"starting"}'),
        (200, "application/json", b"not-json"),
    ],
)
def test_gateway_health_rejects_contract_drift(
    status: int,
    content_type: str,
    body: bytes,
) -> None:
    with pytest.raises(verify_distribution.VerificationError):
        verify_distribution._validate_gateway_health(status, content_type, body)


def test_gateway_probe_supervises_health_and_bounded_shutdown(tmp_path: Path) -> None:
    executable = _write_fake_gateway(tmp_path / "pico")
    records: list[dict] = []

    report = verify_distribution._probe_installed_gateway(
        executable,
        root=tmp_path / "gateway-probe",
        cwd=tmp_path,
        env=_isolated_env(tmp_path),
        log_dir=tmp_path / "logs",
        records=records,
        readiness_timeout=5.0,
        stability_seconds=0.05,
        shutdown_timeout=2.0,
        cleanup_timeout=0.5,
    )

    assert report["endpoint"] == f"http://127.0.0.1:{report['port']}/health"
    assert report["health_attempts"] >= 2
    assert report["stable_seconds"] == 0.05
    assert report["shutdown_signal"] == "SIGINT"
    assert report["shutdown_exit_code"] == 0
    assert report["forced"] is False
    assert Path(report["config"]).is_file()
    assert Path(report["workspace"]).is_dir()
    assert records[-1]["accepted"] is True
    assert records[-1]["outcome"] == "healthy_and_stopped"
    assert records[-1]["gateway"] == report


def test_gateway_probe_records_early_exit_and_reaps_the_process(tmp_path: Path) -> None:
    executable = tmp_path / "pico"
    executable.write_text(
        f"#!{sys.executable}\nraise SystemExit(17)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    records: list[dict] = []

    with pytest.raises(verify_distribution.VerificationError, match="early_exit"):
        verify_distribution._probe_installed_gateway(
            executable,
            root=tmp_path / "gateway-probe",
            cwd=tmp_path,
            env=_isolated_env(tmp_path),
            log_dir=tmp_path / "logs",
            records=records,
            readiness_timeout=1.0,
            stability_seconds=0.01,
            shutdown_timeout=0.2,
            cleanup_timeout=0.2,
        )

    assert records[-1]["accepted"] is False
    assert records[-1]["outcome"] == "early_exit"
    assert records[-1]["exit_code"] == 17


def test_gateway_probe_fails_when_health_contract_drifts(tmp_path: Path) -> None:
    executable = _write_fake_gateway(
        tmp_path / "pico",
        health_body='{"status":"starting"}',
    )
    records: list[dict] = []

    with pytest.raises(verify_distribution.VerificationError, match="health_mismatch"):
        verify_distribution._probe_installed_gateway(
            executable,
            root=tmp_path / "gateway-probe",
            cwd=tmp_path,
            env=_isolated_env(tmp_path),
            log_dir=tmp_path / "logs",
            records=records,
            readiness_timeout=2.0,
            stability_seconds=0.01,
            shutdown_timeout=0.5,
            cleanup_timeout=0.2,
        )

    assert records[-1]["accepted"] is False
    assert records[-1]["outcome"] == "health_mismatch"
    assert records[-1]["gateway"]["forced"] is False


def test_gateway_probe_readiness_timeout_is_recorded_and_reaped(tmp_path: Path) -> None:
    executable = tmp_path / "pico"
    executable.write_text(
        f"""#!{sys.executable}
import signal
import time

def stop(*_args):
    raise SystemExit(0)

signal.signal(signal.SIGINT, stop)
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    records: list[dict] = []

    with pytest.raises(verify_distribution.VerificationError, match="readiness_timeout"):
        verify_distribution._probe_installed_gateway(
            executable,
            root=tmp_path / "gateway-probe",
            cwd=tmp_path,
            env=_isolated_env(tmp_path),
            log_dir=tmp_path / "logs",
            records=records,
            readiness_timeout=0.05,
            stability_seconds=0.01,
            shutdown_timeout=0.5,
            cleanup_timeout=0.2,
        )

    assert records[-1]["accepted"] is False
    assert records[-1]["outcome"] == "readiness_timeout"
    assert records[-1]["gateway"]["forced"] is False


def test_gateway_probe_forced_cleanup_is_a_failure(tmp_path: Path) -> None:
    executable = _write_fake_gateway(
        tmp_path / "pico",
        ignore_shutdown=True,
    )
    records: list[dict] = []

    with pytest.raises(verify_distribution.VerificationError, match="shutdown_timeout"):
        verify_distribution._probe_installed_gateway(
            executable,
            root=tmp_path / "gateway-probe",
            cwd=tmp_path,
            env=_isolated_env(tmp_path),
            log_dir=tmp_path / "logs",
            records=records,
            readiness_timeout=2.0,
            stability_seconds=0.01,
            shutdown_timeout=0.05,
            cleanup_timeout=0.05,
        )

    assert records[-1]["accepted"] is False
    assert records[-1]["outcome"] == "shutdown_timeout"
    assert records[-1]["gateway"]["forced"] is True
    assert records[-1]["exit_code"] == -signal.SIGKILL


def test_gateway_force_kill_uses_popen_on_windows(monkeypatch) -> None:
    class FakeProcess:
        pid = 123
        killed = False

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()
    monkeypatch.setattr(verify_distribution.os, "name", "nt")

    verify_distribution._kill_gateway_process(process)

    assert process.killed is True


def test_runtime_surface_probes_run_only_in_the_installed_base_environment(
    tmp_path: Path,
) -> None:
    executable = _write_fake_gateway(tmp_path / "pico")
    base = verify_distribution.InstallPlan(
        "base",
        tmp_path / "envs" / "base",
        "pico.whl",
        (),
    )
    records: list[dict] = []

    result = verify_distribution._probe_base_runtime_surfaces(
        base,
        executable=executable,
        probe_cwd=tmp_path,
        probe_env=_isolated_env(tmp_path),
        log_dir=tmp_path / "logs",
        records=records,
    )

    assert result["gateway"]["forced"] is False
    assert records[-2]["command"] == [str(executable), "--check"]
    assert records[-2]["accepted"] is True
    assert records[-1]["outcome"] == "healthy_and_stopped"

    extra = verify_distribution.InstallPlan(
        "channel-feishu",
        tmp_path / "envs" / "channel-feishu",
        "pico.whl[channel-feishu]",
        ("lark_oapi",),
    )
    extra_records: list[dict] = []
    assert (
        verify_distribution._probe_base_runtime_surfaces(
            extra,
            executable=executable,
            probe_cwd=tmp_path,
            probe_env=_isolated_env(tmp_path / "extra"),
            log_dir=tmp_path / "extra-logs",
            records=extra_records,
        )
        == {}
    )
    assert extra_records == []


def test_installed_tui_probe_has_a_bounded_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, *, timeout=1800, **_kwargs):
        captured["command"] = command
        captured["timeout"] = timeout

    monkeypatch.setattr(verify_distribution, "_run", fake_run)
    monkeypatch.setattr(
        verify_distribution,
        "_probe_installed_gateway",
        lambda *_args, **_kwargs: {"forced": False},
    )
    plan = verify_distribution.InstallPlan(
        "base",
        tmp_path / "envs" / "base",
        "pico.whl",
        (),
    )

    verify_distribution._probe_base_runtime_surfaces(
        plan,
        executable=tmp_path / "envs" / "base" / "bin" / "pico",
        probe_cwd=tmp_path,
        probe_env=_isolated_env(tmp_path),
        log_dir=tmp_path / "logs",
        records=[],
    )

    assert captured["command"] == [str(tmp_path / "envs" / "base" / "bin" / "pico"), "--check"]
    assert captured["timeout"] == 30
