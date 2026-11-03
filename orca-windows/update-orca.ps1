<#
.SYNOPSIS
  Windows Orca desktop updater (per-user NSIS install).

.DESCRIPTION
  Windows counterpart of common-task/orca-server/update-orca.sh (Linux ARM64 AppImage).
  This machine runs the Orca desktop app installed by the per-user NSIS installer at
  %LOCALAPPDATA%\Programs\orca, so the update path is:
    resolve version -> download orca-windows-setup.exe -> verify sha512/size against
    latest.yml -> (optional) close Orca -> run installer with /S -> verify new version.

  Default mode only stages and verifies the installer; it never closes Orca, so the
  current agent session is not interrupted. Use -Apply to close, install, and relaunch.

.PARAMETER Version
  Target release tag (e.g. v1.4.247). Defaults to the latest GitHub release.

.PARAMETER Force
  Install even when the local version already matches (force reinstall).

.PARAMETER Redownload
  Ignore the cached installer and download a fresh copy.

.PARAMETER Apply
  Close all Orca processes, run the installer silently, then relaunch Orca.

.PARAMETER NoRelaunch
  With -Apply, do not start Orca afterwards.

.PARAMETER Yes
  Skip the interactive confirmation for -Apply.

.PARAMETER InstallDir
  Installation directory. Defaults to %LOCALAPPDATA%\Programs\orca.

.PARAMETER DryRun
  Resolve versions and print the plan; do not download or install.

.PARAMETER Verify
  Verify an already downloaded installer (size + sha512) and exit.

.PARAMETER CleanCache
  Delete the local manual download cache after a successful run.

.PARAMETER SelfTest
  Run the offline assertion checks for the pure helpers and exit.

.EXAMPLE
  update-orca -DryRun

.EXAMPLE
  update-orca -Apply

.EXAMPLE
  update-orca -Version v1.4.247 -Force -Apply
#>
[CmdletBinding()]
param(
    [string]$Version,
    [switch]$Force,
    [switch]$Redownload,
    [switch]$Apply,
    [switch]$NoRelaunch,
    [switch]$Yes,
    [string]$InstallDir,
    [switch]$DryRun,
    [string]$Verify,
    [switch]$CleanCache,
    [switch]$SelfTest,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'

$Repo = 'stablyai/orca'
$AssetName = 'orca-windows-setup.exe'
$CacheRoot = Join-Path $env:LOCALAPPDATA 'orca-updater\manual'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\orca' }
$AppExe = Join-Path $InstallDir 'Orca.exe'

function Write-Info { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Ok { param([string]$Message) Write-Host "[ OK ] $Message" -ForegroundColor Green }
function Write-Warn { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Fail {
    param([string]$Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    exit 1
}

if ($Help) {
    Get-Help $PSCommandPath -Detailed
    exit 0
}

function ConvertTo-Version {
    param([string]$Text)
    if (-not $Text) { return $null }
    $digits = ($Text.Trim().TrimStart('v', 'V') -replace '[^0-9.].*$', '').TrimEnd('.')
    # [version] accepts 2-4 parts only; keep the first four.
    $digits = (($digits -split '\.') | Select-Object -First 4) -join '.'
    $parsed = $null
    if (-not [version]::TryParse($digits, [ref]$parsed)) { return $null }
    # Normalize so 1.4.247, 1.4.247.0 and 1.4.247.0.0 compare as equal.
    $build = [Math]::Max($parsed.Build, 0)
    $revision = [Math]::Max($parsed.Revision, 0)
    return [version]::new($parsed.Major, $parsed.Minor, $build, $revision)
}

function Get-LocalVersion {
    param([string]$Dir)
    $pkg = Join-Path $Dir 'resources\app.asar.unpacked\out\package.json'
    if (Test-Path $pkg) {
        try {
            $json = Get-Content $pkg -Raw | ConvertFrom-Json
            if ($json.version) { return [string]$json.version }
        } catch {
            Write-Warn "无法解析 $pkg，回退到 Orca.exe 版本信息"
        }
    }
    $exe = Join-Path $Dir 'Orca.exe'
    if (Test-Path $exe) {
        return (Get-Item $exe).VersionInfo.ProductVersion
    }
    return $null
}

function Invoke-GitHubJson {
    param([string]$Uri)
    $headers = @{ 'User-Agent' = 'orca-update-windows'; 'Accept' = 'application/vnd.github+json' }
    try {
        return Invoke-RestMethod -Uri $Uri -Headers $headers -UseBasicParsing -TimeoutSec 30
    } catch {
        # Fall back to the authenticated CLI when plain REST is blocked.
        if (Get-Command gh -ErrorAction SilentlyContinue) {
            try {
                $raw = gh api ($Uri -replace '^https://api\.github\.com/', '') 2>$null
                if ($LASTEXITCODE -eq 0 -and $raw) { return $raw | ConvertFrom-Json }
            } catch { }
        }
        throw
    }
}

function Get-ReleaseInfo {
    param([string]$Tag)
    if ($Tag) {
        $rel = Invoke-GitHubJson "https://api.github.com/repos/$Repo/releases/tags/$Tag"
    } else {
        $rel = Invoke-GitHubJson "https://api.github.com/repos/$Repo/releases/latest"
    }
    $asset = $null
    if ($rel.assets) {
        $asset = $rel.assets | Where-Object { $_.name -eq $AssetName } | Select-Object -First 1
    }
    $tag = [string]$rel.tag_name
    if (-not $asset) {
        $assetUrl = "https://github.com/$Repo/releases/download/$tag/$AssetName"
    } else {
        $assetUrl = $asset.browser_download_url
    }
    return [pscustomobject]@{
        Tag      = $tag
        Version  = $tag
        AssetUrl = $assetUrl
    }
}

function Get-ManifestInfo {
    param([string]$Tag)
    $uri = "https://github.com/$Repo/releases/download/$Tag/latest.yml"
    $text = $null
    try {
        $text = (Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 30).Content
    } catch {
        return $null
    }
    if ($text -is [byte[]]) { $text = [System.Text.Encoding]::UTF8.GetString($text) }
    $sha = [regex]::Match($text, 'sha512:\s*(\S+)').Groups[1].Value
    $size = [regex]::Match($text, 'size:\s*(\d+)').Groups[1].Value
    if (-not $sha) { return $null }
    return [pscustomobject]@{
        Sha512 = $sha
        Size   = if ($size) { [int64]$size } else { 0 }
        Source = $uri
    }
}

function Get-Base64Sha512 {
    param([string]$Path)
    $sha = [System.Security.Cryptography.SHA512]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return [Convert]::ToBase64String($sha.ComputeHash($stream))
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Format-Size {
    param([int64]$Bytes)
    if ($Bytes -lt 1MB) { return ('{0:N0} KB' -f ($Bytes / 1KB)) }
    return ('{0:N1} MB' -f ($Bytes / 1MB))
}

function Invoke-Download {
    param([string]$Url, [string]$OutFile, [int64]$ExpectedSize)
    $part = "$OutFile.part"
    if (Test-Path $part) { Remove-Item $part -Force }
    Add-Type -AssemblyName System.Net.Http -ErrorAction SilentlyContinue
    try {
        $client = New-Object System.Net.Http.HttpClient
        $client.DefaultRequestHeaders.Add('User-Agent', 'orca-update-windows')
        $resp = $client.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).Result
        $resp.EnsureSuccessStatusCode() | Out-Null
        $total = 0
        if ($resp.Content.Headers.ContentLength) { $total = [int64]$resp.Content.Headers.ContentLength }
        if ($total -le 0) { $total = $ExpectedSize }
        $in = $resp.Content.ReadAsStreamAsync().Result
        $out = [System.IO.File]::Create($part)
        $buffer = New-Object byte[] (1MB)
        $done = 0
        $lastPct = -1
        try {
            while (($read = $in.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $out.Write($buffer, 0, $read)
                $done += $read
                if ($total -gt 0) {
                    $pct = [int](($done * 100) / $total)
                    if ($pct -ne $lastPct) {
                        $lastPct = $pct
                        Write-Host ("`r      下载中 {0,3}%  {1} / {2}" -f $pct, (Format-Size $done), (Format-Size $total)) -NoNewline
                    }
                }
            }
        } finally {
            $out.Dispose()
            $in.Dispose()
            $resp.Dispose()
            $client.Dispose()
        }
        Write-Host ''
    } catch {
        Write-Warn "流式下载失败（$($_.Exception.Message)），改用 Invoke-WebRequest"
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $Url -OutFile $part -UseBasicParsing
    }
    Move-Item -LiteralPath $part -Destination $OutFile -Force
}

function Test-Installer {
    param([string]$Path, $Manifest)
    if (-not (Test-Path $Path)) { Fail "安装包不存在: $Path" }
    $file = Get-Item $Path
    if (-not $Manifest) {
        Write-Warn '未取得 latest.yml，跳过 sha512 校验（仅依赖 HTTPS 传输）'
        return
    }
    if ($Manifest.Size -gt 0 -and $file.Length -ne $Manifest.Size) {
        Fail ("安装包大小不符: 期望 {0} 字节，实际 {1} 字节" -f $Manifest.Size, $file.Length)
    }
    $actual = Get-Base64Sha512 -Path $Path
    if ($actual -ne $Manifest.Sha512) {
        Fail ("sha512 校验失败: 期望 {0}，实际 {1}" -f $Manifest.Sha512, $actual)
    }
    Write-Ok ("sha512 校验通过（{0}）" -f (Format-Size $file.Length))
}

function Test-InstallerMatch {
    param([string]$Path, $Manifest)
    if (-not (Test-Path $Path)) { return $false }
    if (-not $Manifest) { return $true }
    $file = Get-Item $Path
    if ($Manifest.Size -gt 0 -and $file.Length -ne $Manifest.Size) { return $false }
    return ((Get-Base64Sha512 -Path $Path) -eq $Manifest.Sha512)
}

function Get-OrcaProcesses {
    $procs = @(Get-Process -Name Orca -ErrorAction SilentlyContinue)
    return $procs
}

function Stop-OrcaApp {
    param([string]$Dir)
    $procs = Get-OrcaProcesses
    if (-not $procs) { Write-Info 'Orca 未在运行'; return }
    Write-Info ("正在关闭 {0} 个 Orca 进程（当前 agent 会话会中断）..." -f $procs.Count)
    foreach ($p in $procs) {
        try { $null = $p.CloseMainWindow() } catch { }
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        $alive = @(Get-OrcaProcesses)
        if ($alive.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
    $alive = @(Get-OrcaProcesses)
    if ($alive.Count -gt 0) {
        Write-Warn ("仍有 {0} 个进程未退出，强制结束" -f $alive.Count)
        foreach ($p in $alive) { try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { } }
        Start-Sleep -Seconds 2
    }
    if (@(Get-OrcaProcesses).Count -gt 0) { Fail '无法关闭 Orca 进程，请手动退出后重试' }
    # Make sure the install directory is no longer locked before running the installer.
    $exe = Join-Path $Dir 'Orca.exe'
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        try {
            $fs = [System.IO.File]::Open($exe, 'Open', 'ReadWrite', 'None')
            $fs.Dispose()
            Write-Ok '安装目录已解锁'
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    Fail "安装目录仍被占用: $Dir"
}

function Invoke-SelfTest {
    # Offline, assertion-style checks for the pure helpers (no network, no install).
    $result = @{ Checks = 0; Failures = New-Object System.Collections.Generic.List[string] }
    $check = {
        param($condition, $name)
        $result.Checks++
        if (-not $condition) { $result.Failures.Add($name) }
    }

    & $check ((ConvertTo-Version 'v1.4.247') -eq (ConvertTo-Version '1.4.247.0.0')) 'same version normalizes equally'
    & $check ((ConvertTo-Version '1.4.247') -gt (ConvertTo-Version '1.4.246')) 'newer version compares greater'
    & $check ($null -eq (ConvertTo-Version 'garbage')) 'unparsable version returns null'
    & $check ((Format-Size 2048) -match 'KB$') 'kilobyte formatting'
    & $check ((Format-Size 208971240) -match 'MB$') 'megabyte formatting'

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('orca-selftest-' + [guid]::NewGuid().ToString('N') + '.bin')
    try {
        [System.IO.File]::WriteAllBytes($tmp, [byte[]](1..64))
        $sha = Get-Base64Sha512 -Path $tmp
        & $check ($sha.Length -eq 88 -and $sha.EndsWith('==')) 'sha512 is base64 of a 64 byte digest'
        $good = [pscustomobject]@{ Sha512 = $sha; Size = 64 }
        & $check ((Test-InstallerMatch -Path $tmp -Manifest $good) -eq $true) 'matching installer accepted'
        & $check ((Test-InstallerMatch -Path $tmp -Manifest ([pscustomobject]@{ Sha512 = $sha; Size = 65 })) -eq $false) 'size mismatch rejected'
        & $check ((Test-InstallerMatch -Path $tmp -Manifest ([pscustomobject]@{ Sha512 = 'AAA'; Size = 64 })) -eq $false) 'hash mismatch rejected'
        & $check ((Test-InstallerMatch -Path "$tmp.missing" -Manifest $good) -eq $false) 'missing file rejected'
    } finally {
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
    }

    & $check ($AssetName -eq 'orca-windows-setup.exe') 'asset name pinned'
    & $check ($Repo -eq 'stablyai/orca') 'release repo pinned'

    if ($result.Failures.Count -gt 0) {
        foreach ($f in $result.Failures) { Write-Host "  FAIL  $f" -ForegroundColor Red }
        Fail ("SelfTest 失败：{0}/{1}" -f $result.Failures.Count, $result.Checks)
    }
    Write-Ok ("SelfTest 通过：{0}/{0}" -f $result.Checks)
}

# ---------------------------------------------------------------- main flow ---

# Allow dot-sourcing for tests: only the functions are loaded in that case.
if ($MyInvocation.InvocationName -eq '.') { return }

if ($SelfTest) {
    Invoke-SelfTest
    exit 0
}

if ($Verify) {
    $tagForVerify = $Version
    if (-not $tagForVerify) {
        try { $tagForVerify = (Invoke-GitHubJson "https://api.github.com/repos/$Repo/releases/latest").tag_name } catch { }
    }
    $manifest = $null
    if ($tagForVerify) { $manifest = Get-ManifestInfo -Tag $tagForVerify }
    Write-Info "校验文件: $Verify"
    if ($manifest) { Write-Info ("对照清单: {0}" -f $manifest.Source) }
    Test-Installer -Path $Verify -Manifest $manifest
    exit 0
}

if (-not (Test-Path $AppExe)) {
    Fail "未找到 Orca 安装目录（$InstallDir）。若装在别处，请加 -InstallDir <路径>。"
}

$localVersion = Get-LocalVersion -Dir $InstallDir
Write-Info ("当前本地版本: {0}" -f $(if ($localVersion) { $localVersion } else { 'unknown' }))
Write-Info ("安装目录: {0}" -f $InstallDir)

if ($Version) {
    Write-Info "已指定目标版本: $Version"
} else {
    Write-Info '正在查询 GitHub 最新发布版本...'
}
$release = $null
try {
    $release = Get-ReleaseInfo -Tag $Version
} catch {
    $reason = $_.Exception.Message.TrimEnd('。', '.', ' ')
    Fail "无法获取 Release 信息：$reason。可用 -Version vX.Y.Z 指定版本，或先检查网络/代理。"
}
$targetTag = $release.Tag
$targetVersion = $release.Version
Write-Info ("目标版本: {0}" -f $targetTag)

$localVer = ConvertTo-Version $localVersion
$targetVer = ConvertTo-Version $targetVersion
if ($localVer -and $targetVer -and $localVer -eq $targetVer -and -not $Force) {
    Write-Ok ("当前版本已是最新（{0}），无需更新。如需强制重装请加 -Force。" -f $localVersion)
    exit 0
}

$cacheDir = Join-Path $CacheRoot $targetTag
$installerPath = Join-Path $cacheDir $AssetName
$manifest = Get-ManifestInfo -Tag $targetTag
if ($manifest) {
    Write-Info ("清单: {0}（{1} 字节）" -f $manifest.Source, $manifest.Size)
} else {
    Write-Warn "未能读取 $targetTag 的 latest.yml，下载后将无法做 sha512 校验"
}
Write-Info ("下载地址: {0}" -f $release.AssetUrl)
Write-Info ("安装包路径: {0}" -f $installerPath)

if ($DryRun) {
    Write-Host ''
    Write-Host '--- Dry run 计划 ---' -ForegroundColor Magenta
    Write-Host ("1. 下载 {0} -> {1}" -f $release.AssetUrl, $installerPath)
    Write-Host  '2. 校验 size / sha512（对照 latest.yml）'
    if ($Apply) {
        Write-Host  '3. 关闭所有 Orca 进程并等待安装目录解锁'
        Write-Host ("4. 静默安装: `"{0}`" /S" -f $installerPath)
        Write-Host ("5. 校验新版本，重新启动 {0}" -f $AppExe)
    } else {
        Write-Host  '3. 只保留安装包，打印后续手动步骤（不关闭 Orca）'
    }
    exit 0
}

# Download, reusing a cached copy that still matches the release manifest.
$needDownload = $true
if (-not $Redownload -and (Test-InstallerMatch -Path $installerPath -Manifest $manifest)) {
    Write-Ok '复用已缓存的安装包（size / sha512 与清单一致）'
    $needDownload = $false
}

if ($needDownload) {
    New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    Write-Info ('正在下载 {0} ...' -f $AssetName)
    $expected = 0
    if ($manifest) { $expected = $manifest.Size }
    Invoke-Download -Url $release.AssetUrl -OutFile $installerPath -ExpectedSize $expected
    Write-Ok '下载完成'
}

Test-Installer -Path $installerPath -Manifest $manifest

if (-not $Apply) {
    Write-Host ''
    Write-Host '================================================================' -ForegroundColor Yellow
    Write-Host '安装包已就绪，未关闭 Orca（避免中断当前会话）。' -ForegroundColor Yellow
    Write-Host '空闲时执行以下任一种方式完成安装：' -ForegroundColor Yellow
    Write-Host ''
    Write-Host ("  update-orca -Apply      # 自动关闭 Orca、静默安装并重启") -ForegroundColor Green
    Write-Host  '  或手动操作：'
    Write-Host ("    1) 退出 Orca（托盘图标 -> Quit，或 Get-Process Orca | Stop-Process）")
    Write-Host ("    2) `"{0}`" /S" -f $installerPath)
    Write-Host ("    3) 启动 {0}" -f $AppExe)
    Write-Host '================================================================' -ForegroundColor Yellow
    exit 0
}

# Apply: close, install, verify, relaunch.
$runningFirst = (@(Get-OrcaProcesses).Count -gt 0)
if ($env:ORCA_WORKTREE_ID -or $env:ORCA_TAB_ID) {
    if (-not $Force) {
        Fail '当前终端由 Orca 托管，关闭 Orca 会连带结束本会话。确认要这样做请加 -Force。'
    }
    Write-Warn 'Orca 托管终端下强制安装：本会话将在安装后中断'
}

if ($runningFirst -and -not $Yes) {
    Write-Warn '接下来会关闭 Orca（当前所有 agent 终端会话都会中断），然后静默安装并重启。'
    $answer = Read-Host '确认继续？(y/N)'
    if ($answer -notmatch '^(y|Y|yes|YES)$') { Fail '已取消' }
}

if ($runningFirst) { Stop-OrcaApp -Dir $InstallDir }

Write-Info ('正在静默安装（{0} /S）...' -f (Split-Path $installerPath -Leaf))
$proc = Start-Process -FilePath $installerPath -ArgumentList '/S' -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    Write-Warn ("安装程序返回退出码 {0}" -f $proc.ExitCode)
}

Write-Info '校验安装结果...'
$newVersion = $null
for ($i = 0; $i -lt 20; $i++) {
    $newVersion = Get-LocalVersion -Dir $InstallDir
    if ($newVersion -and (ConvertTo-Version $newVersion) -eq $targetVer) { break }
    Start-Sleep -Milliseconds 500
}
if (-not $newVersion) { Fail '安装后无法读取版本，请检查安装日志或手动重装' }
if ((ConvertTo-Version $newVersion) -ne $targetVer) {
    Fail ("安装后版本仍为 {0}，期望 {1}。可保留安装包 {2} 手动重试。" -f $newVersion, $targetTag, $installerPath)
}
Write-Ok ("版本已更新: {0} -> {1}" -f $localVersion, $newVersion)

if ($CleanCache) {
    try {
        Remove-Item -LiteralPath $cacheDir -Recurse -Force
        Write-Info "已清理缓存: $cacheDir"
    } catch {
        Write-Warn "清理缓存失败: $($_.Exception.Message)"
    }
}

if ($NoRelaunch -or -not $runningFirst) {
    Write-Info ("已安装，未启动 Orca。手动启动: {0}" -f $AppExe)
    exit 0
}

Write-Info '正在启动新版 Orca...'
Start-Process -FilePath $AppExe
if (Get-Command orca -ErrorAction SilentlyContinue) {
    for ($i = 0; $i -lt 40; $i++) {
        $reported = & orca --version 2>$null
        if ($reported -and (ConvertTo-Version $reported) -eq $targetVer) {
            Write-Ok ("orca CLI 版本已就绪: {0}" -f $reported)
            break
        }
        Start-Sleep -Milliseconds 750
    }
}
Write-Ok ("更新完成: {0}" -f $targetTag)
Write-Info '提示：Orca 自带更新缓存位于 %LOCALAPPDATA%\orca-updater\pending，可在下次应用内更新后忽略或清理。'
