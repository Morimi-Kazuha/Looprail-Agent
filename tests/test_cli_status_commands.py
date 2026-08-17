"""CLI tests for ``pico status``.

The command reads the active config + prints provider status. Tests use a
sandboxed tmp config via ``set_config_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pico.cli.commands import app
from pico.config.loader import set_config_path

runner = CliRunner()


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.json"
    set_config_path(cfg)
    yield cfg
    set_config_path(None)  # type: ignore[arg-type]


def test_status_help_works() -> None:
    """``pico status --help`` lists the command without error."""
    r = runner.invoke(app, ["status", "--help"])
    assert r.exit_code == 0
    assert "Show Pico status" in r.stdout


def test_status_without_config_still_runs(tmp_config: Path) -> None:
    """Status runs even when no config file exists (load_config returns defaults)."""
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    assert "Pico Status" in r.stdout
    assert "Config:" in r.stdout
    assert "Workspace:" in r.stdout
    assert "State:" in r.stdout


def test_status_reports_current_project_workspace(
    tmp_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    product_home = tmp_path / "pico-home"
    monkeypatch.setenv("PICO_HOME", str(product_home))
    monkeypatch.chdir(project)

    r = runner.invoke(app, ["status"])

    assert r.exit_code == 0
    output = r.stdout.replace("\n", "")
    assert str(project) in output
    assert str(product_home / "projects") in output


def test_status_with_existing_config_shows_model(tmp_config: Path) -> None:
    """When the config file exists, the active model + provider rows are listed."""
    from pico.config.loader import save_config
    from pico.config.schema import Config

    cfg_obj = Config()
    cfg_obj.providers.anthropic.api_key = "test-anthropic-key"
    save_config(cfg_obj)

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    assert "Model:" in r.stdout
    assert "Anthropic" in r.stdout


def test_status_marks_oauth_providers_distinctly(tmp_config: Path) -> None:
    """OAuth-based providers (openai_codex, github_copilot) display ``OAuth`` flag."""
    from pico.config.loader import save_config
    from pico.config.schema import Config

    save_config(Config())

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0

    assert "OAuth" in r.stdout


def test_status_reports_explicit_memory_off(tmp_config: Path) -> None:
    from pico.config.loader import save_config
    from pico.config.schema import Config
    from pico.config.update import set_memory_backend

    save_config(Config())
    set_memory_backend(None)

    r = runner.invoke(app, ["status"])

    assert r.exit_code == 0
    assert "Memory:" in r.stdout
    assert "disabled" in r.stdout
