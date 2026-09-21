"""Contract checks for the public Looprail installers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GITHUB_REPOSITORY = "Morimi-Kazuha/Looprail-Agent"
GITHUB_LATEST_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
GITHUB_WHEEL_URL = (
    f"https://github.com/{GITHUB_REPOSITORY}/releases/download/v0.1.7/looprail-0.1.7-py3-none-any.whl"
)


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_uses_final_github_release_endpoint(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")

    assert GITHUB_REPOSITORY in source
    assert GITHUB_LATEST_API in source
    assert "releases/latest" in source
    assert "LOOPRAIL_WHEEL_URL" in source
    assert "LOOPRAIL_GITEE_" not in source
    assert "gitee.com/api/v5/repos" not in source
    assert "install_pico" not in source
    assert "pico_harness-" not in source
    assert "looprail-harness" not in source
    assert "github.com/Morimi-Kazuha/Forge-Agent" not in source
    assert "myna" not in source.lower()
    assert "--with-executables-from" not in source
    assert "looprail onboard --skip-memory" in source


def test_installer_keeps_required_mirror_and_checksum_configuration() -> None:
    for name in ("install.sh", "install.ps1"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "LOOPRAIL_NPM_REGISTRY" in source
        assert "LOOPRAIL_NODE_CHECKSUM_BASE" in source
        assert "LOOPRAIL_PYPI_INDEX" in source
        assert "https://nodejs.org/dist" in source


def test_public_repository_urls_are_finalized() -> None:
    for name in ("README.md", "README.zh-CN.md", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert f"https://github.com/{GITHUB_REPOSITORY}" in source
        assert "Forge-Agent" not in source


def test_posix_installer_has_valid_shell_syntax() -> None:
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable")
    result = subprocess.run(
        [shell, "-n", str(ROOT / "install.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_powershell_installer_has_valid_syntax(tmp_path: Path) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    installer = str(ROOT / "install.ps1").replace("'", "''")
    probe = tmp_path / "parse-installer.ps1"
    probe.write_text(
        ""
        "$tokens = $null\n"
        "$errors = $null\n"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{installer}', [ref]$tokens, [ref]$errors) | Out-Null\n"
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(probe)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _run_posix_resolver(
    tmp_path: Path,
    body: str,
    *,
    release_json: str = "",
    curl_exit: int = 0,
    wheel_url: str = "",
) -> subprocess.CompletedProcess[str]:
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        "#!/bin/sh\n"
        'if [ "${FAKE_CURL_EXIT:-0}" -ne 0 ]; then exit "$FAKE_CURL_EXIT"; fi\n'
        'printf "%s" "${FAKE_CURL_BODY:-}"\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    env = os.environ.copy()
    env["LOOPRAIL_INSTALLER_TEST_MODE"] = "1"
    env["FAKE_CURL_BODY"] = release_json
    env["FAKE_CURL_EXIT"] = str(curl_exit)
    env["LOOPRAIL_WHEEL_URL"] = wheel_url
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    shell_path = str(ROOT / "install.sh").replace("\\", "/")
    script = f'. "{shell_path}"\n{body}\n'
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("POSIX sh is unavailable")
    return subprocess.run(
        [shell, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_posix_installer_selects_a_looprail_wheel_from_mocked_release(tmp_path: Path) -> None:
    release_json = (
        '{"assets":['
        '{"browser_download_url":"https://github.com/Morimi-Kazuha/Looprail-Agent/releases/download/v0.1.7/source.zip"},'
        f'{{"browser_download_url":"{GITHUB_WHEEL_URL}"}}'
        "]}"
    )
    result = _run_posix_resolver(
        tmp_path,
        'resolve_looprail_wheel\nprintf "%s\\n" "$wheel_url"',
        release_json=release_json,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith(GITHUB_WHEEL_URL)


def test_posix_installer_override_skips_github_api(tmp_path: Path) -> None:
    result = _run_posix_resolver(
        tmp_path,
        'resolve_looprail_wheel\nprintf "%s\\n" "$wheel_url"',
        curl_exit=22,
        wheel_url="https://example.test/looprail-override.whl",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "https://example.test/looprail-override.whl"


@pytest.mark.parametrize(
    ("release_json", "curl_exit", "expected"),
    [
        ('{"assets":[]}', 0, "does not contain a Looprail wheel"),
        ("", 22, "Could not query the GitHub latest release"),
    ],
)
def test_posix_installer_fails_closed_for_release_resolution_errors(
    tmp_path: Path,
    release_json: str,
    curl_exit: int,
    expected: str,
) -> None:
    result = _run_posix_resolver(
        tmp_path,
        "resolve_looprail_wheel",
        release_json=release_json,
        curl_exit=curl_exit,
    )
    assert result.returncode != 0
    assert expected.lower() in result.stderr.lower()


def test_powershell_installer_selects_a_mocked_release_asset(tmp_path: Path) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    installer = str(ROOT / "install.ps1").replace("'", "''")
    expected = GITHUB_WHEEL_URL.replace("'", "''")
    probe = tmp_path / "resolve-installer.ps1"
    probe.write_text(
        f"$env:LOOPRAIL_WHEEL_URL = ''\n. '{installer}'\n"
        "function Invoke-RestMethod {\n"
        "    param([string]$Uri, [hashtable]$Headers, [string]$Method)\n"
        f"    if ($Uri -ne '{GITHUB_LATEST_API}') {{ throw ('unexpected latest endpoint: ' + $Uri) }}\n"
        "    if ($Method -ne 'Get' -or $Headers.Accept -ne 'application/vnd.github+json') { throw 'GitHub request contract drifted' }\n"
        "    [pscustomobject]@{ assets = @(\n"
        "        [pscustomobject]@{ name = 'source.zip'; browser_download_url = 'https://github.com/Morimi-Kazuha/Looprail-Agent/releases/download/v0.1.7/source.zip' },\n"
        f"        [pscustomobject]@{{ name = 'looprail-0.1.7-py3-none-any.whl'; browser_download_url = '{expected}' }}\n"
        "    ) }\n"
        "}\n"
        f"$result = Resolve-LooprailReleaseAssets\nif ($result -ne '{expected}') {{ throw 'unexpected wheel selection' }}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LOOPRAIL_WHEEL_URL"] = ""
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(probe)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def _run_powershell_resolver(tmp_path: Path, behavior: str, *, wheel_url: str = "") -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    installer = str(ROOT / "install.ps1").replace("'", "''")
    probe = tmp_path / "resolver-contract.ps1"
    probe.write_text(
        f"$env:LOOPRAIL_WHEEL_URL = '{wheel_url}'\n. '{installer}'\n"
        f"{behavior}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(probe)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_powershell_installer_override_skips_github_api(tmp_path: Path) -> None:
    override = "https://example.test/looprail-override.whl"
    result = _run_powershell_resolver(
        tmp_path,
        "function Invoke-RestMethod { throw 'latest API must not be called' }\n"
        "$result = Resolve-LooprailReleaseAssets\n"
        "if ($result -ne $env:LOOPRAIL_WHEEL_URL) { throw 'override was not selected' }\n",
        wheel_url=override,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("mock_release", "expected"),
    [
        (
            "function Invoke-RestMethod { throw 'mock HTTP failure' }\n",
            "Could not query the GitHub latest release",
        ),
        (
            "function Invoke-RestMethod { return [pscustomobject]@{ assets = @() } }\n",
            "does not contain a Looprail wheel",
        ),
    ],
)
def test_powershell_installer_fails_closed_for_release_errors(
    tmp_path: Path,
    mock_release: str,
    expected: str,
) -> None:
    result = _run_powershell_resolver(
        tmp_path,
        mock_release
        + "try { $null = Resolve-LooprailReleaseAssets; throw 'resolver unexpectedly succeeded' } "
        + "catch { Write-Output ('RESOLVER_ERROR:' + $_.Exception.Message) }\n",
    )
    assert result.returncode == 0, result.stderr
    assert expected.lower() in result.stdout.lower()


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_fails_closed_when_node_checksum_is_unavailable(name: str) -> None:
    source = (ROOT / name).read_text(encoding="utf-8")

    assert "skipping checksum verification" not in source.lower()
    assert "could not fetch node shasums256.txt" in source.lower()
    assert "https://nodejs.org/dist" in source
