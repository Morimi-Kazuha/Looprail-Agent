"""Contract checks for the public Pico installers."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_uses_gitee_release_without_private_memory_repository(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")

    assert "gitee.com/api/v5/repos" in source
    assert "PICO_GITEE_REPO" in source
    assert "releases/latest" in source
    assert "PICO_WHEEL_URL" in source
    assert "PICO_GITEE_TOKEN" in source
    assert "PICO_NPM_REGISTRY" in source
    assert "PICO_NODE_CHECKSUM_BASE" in source
    assert "PICO_PYPI_INDEX" in source
    assert "github.com" not in source.lower()
    assert "myna" not in source.lower()
    assert "--with-executables-from" not in source
    assert "pico onboard --skip-memory" in source


def test_posix_installer_downloads_private_wheel_before_uv_install() -> None:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "gitee_curl" in source
    assert '-H "Authorization: Bearer $PICO_GITEE_TOKEN"' not in source
    assert '"$wheel_url" -o "$wheel_path"' in source
    assert 'uv tool install --force "pico-harness[channels] @ $wheel_source"' in source


def test_powershell_installer_downloads_private_wheel_before_uv_install() -> None:
    source = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert '"Authorization"] = "Bearer $env:PICO_GITEE_TOKEN"' in source
    assert "Invoke-WebRequest $wheelUrl -Headers $headers -OutFile $wheelPath" in source
    assert '"pico-harness[channels] @ $wheelSource"' in source


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_fails_closed_when_node_checksum_is_unavailable(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")

    assert "skipping checksum verification" not in source.lower()
    assert "could not fetch node shasums256.txt" in source.lower()
    assert "https://nodejs.org/dist" in source
