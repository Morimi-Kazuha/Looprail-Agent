"""Local Windows CLI principal and conservative Host-state ACL admission.

No request/config identity is authentication. Windows reads its inherited OS
token and actual directory security descriptors; unsupported/uncertain cases
retain the existing Session-local fallback. This never changes an ACL.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
from pathlib import Path

# Only the current token user, SYSTEM, and local Administrators are trusted.
# Ancestors must not grant other principals deletion/ACL takeover; the actual
# state subtree must additionally exclude data reads/writes by other users.
_PROBE = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
if (!$sid -or $identity.IsAnonymous -or !$identity.IsAuthenticated) { exit 2 }
$trusted = @($sid, 'S-1-5-18', 'S-1-5-32-544')
# These are owner-relative ACEs; owner itself is checked separately.
$trustedRules = $trusted + @('S-1-3-0', 'S-1-3-4')
$state = [IO.Path]::GetFullPath($inputData.state)
$pending = [Collections.Generic.Queue[string]]::new()
$pending.Enqueue($state)
$count = 0
while ($pending.Count -gt 0) {
    $path = $pending.Dequeue()
    $count += 1
    if ($count -gt 4096) { exit 11 }
    $attributes = [IO.File]::GetAttributes($path)
    if (($attributes -band 1024) -ne 0) { exit 3 }
    $acl = if (($attributes -band 16) -ne 0) { [IO.Directory]::GetAccessControl($path) } else { [IO.File]::GetAccessControl($path) }
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $trusted) { exit 4 }
    if ($acl.Sddl -notmatch 'D:') { exit 5 }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne 'Allow') { continue }
        # Inherit-only rules also matter: new cache files inherit permissions.
        if ($rule.IdentityReference.Value -notin $trustedRules -and
            ([long]$rule.FileSystemRights -band 0xD00D015F) -ne 0) { exit 6 }
    }
    if (($attributes -band 16) -ne 0) {
        foreach ($item in [IO.Directory]::EnumerateFileSystemEntries($path)) {
            if ($path -ne $state -or [IO.Path]::GetFileName($item) -eq 'file-working') {
                $pending.Enqueue($item)
            }
        }
    }
}
$parent = [IO.Directory]::GetParent($state)
while ($null -ne $parent) {
    if (($parent.Attributes -band 1024) -ne 0) { exit 7 }
    $acl = [IO.Directory]::GetAccessControl($parent.FullName)
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    # TrustedInstaller owns some OS ancestor directories.
    if ($owner -notin $trusted -and $owner -ne 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464') { exit 8 }
    if ($acl.Sddl -notmatch 'D:') { exit 9 }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -ne 'Allow' -or ([int]$rule.PropagationFlags -band 2) -ne 0) { continue }
        if ($rule.IdentityReference.Value -notin $trustedRules -and
            ([long]$rule.FileSystemRights -band 0x100D0040) -ne 0) { exit 10 }
    }
    $parent = $parent.Parent
}
@{sid=$sid;state=$state} | ConvertTo-Json -Compress
"""


def local_cli_file_scope(state: Path, workspace: Path) -> str | None:
    """Return an OS-derived scope only for an already private external root."""
    if os.name != "nt":
        return None
    try:
        from looprail.memory_engine.file_working import no_links

        # Reject aliases before resolve; never create or repair a state root.
        no_links(state.absolute())
        state = state.resolve(strict=True)
        if not state.is_dir() or state.is_relative_to(workspace.resolve()):
            return None
        # Remote/mapped storage is outside this local Host proof.
        from ctypes import wintypes

        drive_type = ctypes.windll.kernel32.GetDriveTypeW
        drive_type.argtypes = [wintypes.LPCWSTR]
        drive_type.restype = wintypes.UINT
        if drive_type(state.anchor) != 3:  # DRIVE_FIXED
            return None
        system_dir = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetSystemDirectoryW(system_dir, len(system_dir)):
            return None
        executable = Path(system_dir.value) / "WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(_PROBE.encode("utf-16-le")).decode("ascii"),
            ],
            input=json.dumps({"state": str(state)}).encode("ascii"),
            capture_output=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        proof = json.loads(result.stdout)
        if not str(proof["sid"]).startswith("S-1-") or Path(proof["state"]) != state:
            return None
        return f"local-cli-windows-v1:{proof['sid']}:{os.path.normcase(str(state))}"
    except Exception:
        return None
