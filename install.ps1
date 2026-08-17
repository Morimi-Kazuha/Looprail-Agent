# Pico 国内 Windows PowerShell 一键安装脚本。
#
# 远程：设置 PICO_GITEE_TOKEN 后，使用 README 中的鉴权命令。
#
# 目标：让全新 Windows 机器无需管理员权限即可运行 `pico`。脚本具备幂等性，
# 会复用已有工具并只补齐缺项：
#   1. uv            （Python 工具链与包管理器）
#   2. Node.js >= 22 （TUI 运行时；系统缺少时私有安装）
#   3. pico          （作为全局 uv 工具安装）
#
# 私有仓库阶段设置 PICO_GITEE_TOKEN；也可用 PICO_WHEEL_URL 固定 wheel。

$ErrorActionPreference = "Stop"

$MinNodeMajor = 22
$PicoHome = if ($env:PICO_HOME) { $env:PICO_HOME } else { Join-Path $HOME ".pico" }
$NodeRuntimeDir = Join-Path $PicoHome "runtime"
$PicoGiteeOwner = if ($env:PICO_GITEE_OWNER) { $env:PICO_GITEE_OWNER } else { "htxoffical" }
$PicoGiteeRepo = if ($env:PICO_GITEE_REPO) { $env:PICO_GITEE_REPO } else { "pico-harness" }
$PicoNodeMirror = if ($env:PICO_NODE_MIRROR) { $env:PICO_NODE_MIRROR.TrimEnd('/') } else { "https://mirrors.aliyun.com/nodejs-release" }
$PicoNodeChecksumBase = if ($env:PICO_NODE_CHECKSUM_BASE) { $env:PICO_NODE_CHECKSUM_BASE.TrimEnd('/') } else { "https://nodejs.org/dist" }
$PicoNpmRegistry = if ($env:PICO_NPM_REGISTRY) { $env:PICO_NPM_REGISTRY } else { "https://registry.npmmirror.com" }
$PicoPyPIIndex = if ($env:PICO_PYPI_INDEX) { $env:PICO_PYPI_INDEX } else { "https://pypi.tuna.tsinghua.edu.cn/simple" }
$PicoUvInstallUrl = if ($env:PICO_UV_INSTALL_URL) { $env:PICO_UV_INSTALL_URL } else { "https://astral.sh/uv/install.ps1" }

function Write-Info([string]$Message) {
    Write-Host ">" $Message -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "OK" $Message -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Warning $Message
}

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Add-ProcessPath([string]$PathToAdd) {
    if (-not $PathToAdd) { return }
    if (-not (Test-Path $PathToAdd)) { return }
    $parts = $env:PATH -split ';'
    if ($parts -notcontains $PathToAdd) {
        $env:PATH = "$PathToAdd;$env:PATH"
    }
}

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Ensure-Uv {
    $uv = Find-Uv
    if ($uv) {
        Write-Ok "uv is installed ($(& $uv --version))"
        Add-ProcessPath (Split-Path $uv -Parent)
        return $uv
    }

    Write-Info "uv not found; installing..."
    Invoke-Expression (Invoke-RestMethod $PicoUvInstallUrl)
    $uv = Find-Uv
    if (-not $uv) {
        Fail "uv was installed but is still not available. Check PATH (expected ~/.local/bin)."
    }
    Add-ProcessPath (Split-Path $uv -Parent)
    Write-Ok "uv installed"
    return $uv
}

function Get-NodeArch {
    switch ($env:PROCESSOR_ARCHITECTURE) {
        "ARM64" { return "arm64" }
        "AMD64" { return "x64" }
        default { Fail "Unsupported Windows architecture: $env:PROCESSOR_ARCHITECTURE" }
    }
}

function Test-NodeOk([string]$NodePath) {
    if (-not $NodePath) { return $false }
    if (-not (Test-Path $NodePath)) { return $false }
    try {
        $version = (& $NodePath --version).Trim()
        $major = [int](($version.TrimStart("v") -split "\.")[0])
        return $major -ge $MinNodeMajor
    } catch {
        return $false
    }
}

function Find-PrivateNode {
    $candidates = @()
    $direct = Join-Path $NodeRuntimeDir "node\node.exe"
    $directBin = Join-Path $NodeRuntimeDir "node\bin\node.exe"
    if (Test-Path $direct) { $candidates += $direct }
    if (Test-Path $directBin) { $candidates += $directBin }
    if (Test-Path $NodeRuntimeDir) {
        $candidates += Get-ChildItem $NodeRuntimeDir -Directory -Filter "node-v22*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                @(
                    (Join-Path $_.FullName "node.exe"),
                    (Join-Path $_.FullName "bin\node.exe")
                )
            }
    }
    foreach ($candidate in $candidates) {
        if (Test-NodeOk $candidate) { return $candidate }
    }
    return $null
}

function Get-LatestNodeV22 {
    try {
        $index = Invoke-RestMethod "$PicoNodeMirror/index.json"
        $entry = $index | Where-Object { $_.version -like "v22.*" } | Select-Object -First 1
        if ($entry -and $entry.version) { return $entry.version }
    } catch {
        Write-Warn "Could not query Node.js release index; falling back to v22.20.0"
    }
    return "v22.20.0"
}

function Ensure-Node {
    $systemNode = Get-Command node -ErrorAction SilentlyContinue
    if ($systemNode -and (Test-NodeOk $systemNode.Source)) {
        Write-Ok "Node.js meets requirements ($(& $systemNode.Source --version))"
        return $systemNode.Source
    }

    $privateNode = Find-PrivateNode
    if ($privateNode) {
        Write-Ok "Existing Pico private Node found ($privateNode)"
        Add-ProcessPath (Split-Path $privateNode -Parent)
        return $privateNode
    }

    Write-Info "Node.js >= $MinNodeMajor not found; downloading private runtime..."
    $arch = Get-NodeArch
    $version = Get-LatestNodeV22
    $pkg = "node-$version-win-$arch"
    $url = "$PicoNodeMirror/$version/$pkg.zip"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("pico-node-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tmp "node.zip"

    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    New-Item -ItemType Directory -Path $NodeRuntimeDir -Force | Out-Null

    try {
        Write-Info "  $url"
        Invoke-WebRequest $url -OutFile $zipPath

        try {
            $sums = (Invoke-WebRequest "$PicoNodeChecksumBase/$version/SHASUMS256.txt").Content
        } catch {
            Fail "Could not fetch Node SHASUMS256.txt: $_"
        }
        $line = ($sums -split "`n") | Where-Object { $_ -match "\s+$([regex]::Escape("$pkg.zip"))$" } | Select-Object -First 1
        if (-not $line) {
            Fail "SHASUMS256.txt did not list $pkg.zip."
        }
        $expected = (($line.Trim()) -split "\s+")[0].ToLowerInvariant()
        $actual = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($expected -ne $actual) {
            Fail "Node checksum mismatch (expected $expected, got $actual)."
        }
        Write-Ok "Node zip SHA256 verified"

        Expand-Archive $zipPath -DestinationPath $tmp -Force
        $src = Join-Path $tmp $pkg
        $dest = Join-Path $NodeRuntimeDir $pkg
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Move-Item $src $dest

        $node = Join-Path $dest "node.exe"
        if (-not (Test-NodeOk $node)) {
            Fail "Downloaded Node runtime is not usable on this machine."
        }
        Add-ProcessPath $dest
        Write-Ok "Node private runtime ready: $dest"
        return $node
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Resolve-PicoReleaseAssets {
    if ($env:PICO_WHEEL_URL) {
        return $env:PICO_WHEEL_URL
    }
    Write-Info "Resolving the latest Pico release from Gitee..."
    $headers = @{}
    if ($env:PICO_GITEE_TOKEN) {
        $headers["Authorization"] = "Bearer $env:PICO_GITEE_TOKEN"
    }
    $releaseApi = "https://gitee.com/api/v5/repos/$PicoGiteeOwner/$PicoGiteeRepo/releases/latest"
    $release = Invoke-RestMethod $releaseApi -Headers $headers
    $picoAsset = $release.assets | Where-Object { $_.browser_download_url -match "/pico_harness-[^/]+\.whl$" } | Select-Object -First 1
    if (-not $picoAsset) {
        Fail "Could not resolve the latest Pico wheel from Gitee. For a private repository, set PICO_GITEE_TOKEN; alternatively set PICO_WHEEL_URL."
    }
    return $picoAsset.browser_download_url
}

function Install-Pico([string]$UvPath, [string]$NodePath) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
    $pyproject = Join-Path $scriptDir "pyproject.toml"
    if ((Test-Path $pyproject) -and (Select-String -Path $pyproject -Pattern '^name = "pico-harness"' -Quiet)) {
        Write-Info "Detected local Pico source checkout; installing editable: $scriptDir"
        $entry = Join-Path $scriptDir "ui-tui\dist\entry.js"
        if (-not (Test-Path $entry)) {
            $nodeDir = Split-Path $NodePath -Parent
            Add-ProcessPath $nodeDir
            $npm = Get-Command npm -ErrorAction SilentlyContinue
            if ($npm) {
                Write-Info "Building TUI bundle (ui-tui/dist/entry.js)..."
                Push-Location (Join-Path $scriptDir "ui-tui")
                try {
                    & $npm.Source ci --registry $PicoNpmRegistry
                    & $npm.Source run build
                } finally {
                    Pop-Location
                }
            } else {
                Write-Warn "Found node but not npm; skipping TUI bundle build"
            }
        }
        $previousIndex = $env:UV_DEFAULT_INDEX
        $env:UV_DEFAULT_INDEX = $PicoPyPIIndex
        try {
            & $UvPath tool install --force -e "$scriptDir[channels]"
            if ($LASTEXITCODE -ne 0) { throw "channel extras install failed" }
        } catch {
            Write-Warn "Channel dependencies failed to install; installed base pico only. Some channels stay unavailable (see: pico channels list)."
            & $UvPath tool install --force -e "$scriptDir"
            if ($LASTEXITCODE -ne 0) { Fail "Pico install failed." }
        } finally {
            $env:UV_DEFAULT_INDEX = $previousIndex
        }
    } else {
        $wheelUrl = Resolve-PicoReleaseAssets
        $wheelSource = $wheelUrl
        $wheelTemp = $null
        if ($env:PICO_GITEE_TOKEN -and $wheelUrl.StartsWith("https://gitee.com/")) {
            $wheelName = [IO.Path]::GetFileName(([uri]$wheelUrl).AbsolutePath)
            if (-not $wheelName.EndsWith(".whl")) {
                Fail "Resolved Gitee asset is not a wheel: $wheelName"
            }
            $wheelTemp = Join-Path ([IO.Path]::GetTempPath()) ("pico-wheel-" + [guid]::NewGuid().ToString("N"))
            New-Item -ItemType Directory -Path $wheelTemp -Force | Out-Null
            $wheelPath = Join-Path $wheelTemp $wheelName
            $headers = @{ "Authorization" = "Bearer $env:PICO_GITEE_TOKEN" }
            Write-Info "Downloading private Gitee release wheel..."
            Invoke-WebRequest $wheelUrl -Headers $headers -OutFile $wheelPath
            $wheelSource = $wheelPath
        }
        Write-Info "  installing $wheelSource"
        $previousIndex = $env:UV_DEFAULT_INDEX
        $env:UV_DEFAULT_INDEX = $PicoPyPIIndex
        try {
            & $UvPath tool install --force "pico-harness[channels] @ $wheelSource"
            if ($LASTEXITCODE -ne 0) { throw "channel extras install failed" }
        } catch {
            Write-Warn "Channel dependencies failed to install; installed base pico only. Some channels stay unavailable (see: pico channels list)."
            & $UvPath tool install --force $wheelSource
            if ($LASTEXITCODE -ne 0) { Fail "Pico install failed." }
        } finally {
            $env:UV_DEFAULT_INDEX = $previousIndex
            if ($wheelTemp -and (Test-Path $wheelTemp)) {
                Remove-Item $wheelTemp -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    & $UvPath tool update-shell | Out-Null
    Write-Ok "Pico installed"
}

function Main {
    $uv = Ensure-Uv
    $node = Ensure-Node
    Install-Pico $uv $node

    $toolBin = Join-Path $HOME ".local\bin"
    Add-ProcessPath $toolBin

    Write-Host ""
    Write-Ok "All set. Open a new PowerShell window, enter a Git repository, then run:"
    Write-Host ""
    Write-Host "    pico onboard --skip-memory    # configure Provider and first Turn"
    Write-Host "    pico            # enter the TUI"
    Write-Host "    pico run -m `"hello`""
    Write-Host ""
    if (($env:PATH -split ';') -notcontains $toolBin) {
        Write-Warn "Current PATH does not include $toolBin. Restart PowerShell if 'pico' is not found."
    }
}

Main
