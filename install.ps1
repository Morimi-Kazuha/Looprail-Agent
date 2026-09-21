# Looprail 国内 Windows PowerShell 一键安装脚本。
#
# 源码检出：README 中记录了公开的源码检出流程。
# 远程 release 从最终公开 GitHub 仓库的 latest release API 解析。
#
# 目标：让全新 Windows 机器无需管理员权限即可运行 `looprail`。脚本具备幂等性，
# 会复用已有工具并只补齐缺项：
#   1. uv            （Python 工具链与包管理器）
#   2. Node.js >= 22 （TUI 运行时；系统缺少时私有安装）
#   3. looprail          （作为全局 uv 工具安装）
#
# 如果尚未发布 GitHub Release，也可以用 LOOPRAIL_WHEEL_URL 固定一个受信任的 wheel。

$ErrorActionPreference = "Stop"

$MinNodeMajor = 22
$LooprailHome = if ($env:LOOPRAIL_HOME) { $env:LOOPRAIL_HOME } else { Join-Path $HOME ".looprail" }
$NodeRuntimeDir = Join-Path $LooprailHome "runtime"
$LooprailGithubRepository = "Morimi-Kazuha/Looprail-Agent"
$LooprailGithubReleaseApi = "https://api.github.com/repos/Morimi-Kazuha/Looprail-Agent/releases/latest"
$LooprailGithubHeaders = @{
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "Looprail-installer"
}
$LooprailNodeMirror = if ($env:LOOPRAIL_NODE_MIRROR) { $env:LOOPRAIL_NODE_MIRROR.TrimEnd('/') } else { "https://mirrors.aliyun.com/nodejs-release" }
$LooprailNodeChecksumBase = if ($env:LOOPRAIL_NODE_CHECKSUM_BASE) { $env:LOOPRAIL_NODE_CHECKSUM_BASE.TrimEnd('/') } else { "https://nodejs.org/dist" }
$LooprailNpmRegistry = if ($env:LOOPRAIL_NPM_REGISTRY) { $env:LOOPRAIL_NPM_REGISTRY } else { "https://registry.npmmirror.com" }
$LooprailPyPIIndex = if ($env:LOOPRAIL_PYPI_INDEX) { $env:LOOPRAIL_PYPI_INDEX } else { "https://pypi.tuna.tsinghua.edu.cn/simple" }
$LooprailUvInstallUrl = if ($env:LOOPRAIL_UV_INSTALL_URL) { $env:LOOPRAIL_UV_INSTALL_URL } else { "https://astral.sh/uv/install.ps1" }

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
    Invoke-Expression (Invoke-RestMethod $LooprailUvInstallUrl)
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
        $index = Invoke-RestMethod "$LooprailNodeMirror/index.json"
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
        Write-Ok "Existing Looprail private Node found ($privateNode)"
        Add-ProcessPath (Split-Path $privateNode -Parent)
        return $privateNode
    }

    Write-Info "Node.js >= $MinNodeMajor not found; downloading private runtime..."
    $arch = Get-NodeArch
    $version = Get-LatestNodeV22
    $pkg = "node-$version-win-$arch"
    $url = "$LooprailNodeMirror/$version/$pkg.zip"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("looprail-node-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tmp "node.zip"

    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    New-Item -ItemType Directory -Path $NodeRuntimeDir -Force | Out-Null

    try {
        Write-Info "  $url"
        Invoke-WebRequest $url -OutFile $zipPath

        try {
            $sums = (Invoke-WebRequest "$LooprailNodeChecksumBase/$version/SHASUMS256.txt").Content
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

function Resolve-LooprailReleaseAssets {
    $wheelOverride = $env:LOOPRAIL_WHEEL_URL
    if (-not [string]::IsNullOrWhiteSpace($wheelOverride)) {
        return $wheelOverride.Trim()
    }

    Write-Info "Resolving the current Looprail release..."
    try {
        $release = Invoke-RestMethod -Uri $LooprailGithubReleaseApi -Headers $LooprailGithubHeaders -Method Get
    } catch {
        Fail "Could not query the GitHub latest release at $LooprailGithubReleaseApi. The repository may not have a published release yet, or GitHub is unavailable. Set LOOPRAIL_WHEEL_URL to a trusted wheel URL. Details: $($_.Exception.Message)"
    }

    $assetUrlPattern = "^https://github\.com/$([regex]::Escape($LooprailGithubRepository))/releases/download/[^/]+/looprail-[^/]+\.whl$"
    $looprailAsset = @($release.assets) |
        Where-Object {
            $_.name -match '^looprail-[^/]+\.whl$' -and
            $_.browser_download_url -match $assetUrlPattern
        } |
        Select-Object -First 1
    if (-not $looprailAsset) {
        Fail "The GitHub latest release does not contain a Looprail wheel. Publish a release asset or set LOOPRAIL_WHEEL_URL to a trusted wheel URL."
    }
    return $looprailAsset.browser_download_url
}

function Install-Looprail([string]$UvPath, [string]$NodePath) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
    $pyproject = Join-Path $scriptDir "pyproject.toml"
    if ((Test-Path $pyproject) -and (Select-String -Path $pyproject -Pattern '^name = "looprail"' -Quiet)) {
        Write-Info "Detected local Looprail source checkout; installing editable: $scriptDir"
        $entry = Join-Path $scriptDir "ui-tui\dist\entry.js"
        if (-not (Test-Path $entry)) {
            $nodeDir = Split-Path $NodePath -Parent
            Add-ProcessPath $nodeDir
            $npm = Get-Command npm -ErrorAction SilentlyContinue
            if ($npm) {
                Write-Info "Building TUI bundle (ui-tui/dist/entry.js)..."
                Push-Location (Join-Path $scriptDir "ui-tui")
                try {
                    & $npm.Source ci --registry $LooprailNpmRegistry
                    & $npm.Source run build
                } finally {
                    Pop-Location
                }
            } else {
                Write-Warn "Found node but not npm; skipping TUI bundle build"
            }
        }
        $previousIndex = $env:UV_DEFAULT_INDEX
        $env:UV_DEFAULT_INDEX = $LooprailPyPIIndex
        try {
            & $UvPath tool install --force -e "$scriptDir[channels]"
            if ($LASTEXITCODE -ne 0) { throw "channel extras install failed" }
        } catch {
            Write-Warn "Channel dependencies failed to install; installed base looprail only. Some channels stay unavailable (see: looprail channels list)."
            & $UvPath tool install --force -e "$scriptDir"
            if ($LASTEXITCODE -ne 0) { Fail "Looprail install failed." }
        } finally {
            $env:UV_DEFAULT_INDEX = $previousIndex
        }
    } else {
        $wheelUrl = Resolve-LooprailReleaseAssets
        $wheelSource = $wheelUrl
        Write-Info "  installing $wheelSource"
        $previousIndex = $env:UV_DEFAULT_INDEX
        $env:UV_DEFAULT_INDEX = $LooprailPyPIIndex
        try {
            & $UvPath tool install --force "looprail[channels] @ $wheelSource"
            if ($LASTEXITCODE -ne 0) { throw "channel extras install failed" }
        } catch {
            Write-Warn "Channel dependencies failed to install; installed base looprail only. Some channels stay unavailable (see: looprail channels list)."
            & $UvPath tool install --force $wheelSource
            if ($LASTEXITCODE -ne 0) { Fail "Looprail install failed." }
        } finally {
            $env:UV_DEFAULT_INDEX = $previousIndex
        }
    }
    & $UvPath tool update-shell | Out-Null
    Write-Ok "Looprail installed"
}

function Main {
    $uv = Ensure-Uv
    $node = Ensure-Node
    Install-Looprail $uv $node

    $toolBin = Join-Path $HOME ".local\bin"
    Add-ProcessPath $toolBin

    Write-Host ""
    Write-Ok "All set. Open a new PowerShell window, enter a Git repository, then run:"
    Write-Host ""
    Write-Host "    looprail onboard --skip-memory    # configure Provider and first Turn"
    Write-Host "    looprail            # enter the TUI"
    Write-Host "    looprail run -m `"hello`""
    Write-Host ""
    if (($env:PATH -split ';') -notcontains $toolBin) {
        Write-Warn "Current PATH does not include $toolBin. Restart PowerShell if 'looprail' is not found."
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Main
}
