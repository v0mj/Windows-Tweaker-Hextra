<#
.SYNOPSIS
    Verifies and stages a built Hextra exe for publishing: confirms it is a real
    Windows x64 GUI executable, checks the embedded version resource, renames it
    to the release name and records its SHA-256.

.EXAMPLE
    pwsh -File packaging/stage_artifact.ps1 -Exe dist/Hextra.exe -OutDir artifact -Backend pyinstaller
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [ValidateSet('pyinstaller', 'nuitka')][string]$Backend = 'pyinstaller'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Exe)) {
    Write-Host "::error::Expected build output not found: $Exe"
    exit 2
}

function Test-PeExecutable {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {
        return @{ ok = $false; reason = 'missing MZ header' }
    }
    $peOffset = [BitConverter]::ToInt32($bytes, 60)
    if ($peOffset -le 0 -or ($peOffset + 24 + 68 + 2) -gt $bytes.Length) {
        return @{ ok = $false; reason = 'missing PE header' }
    }
    if ($bytes[$peOffset] -ne 0x50 -or $bytes[$peOffset + 1] -ne 0x45) {
        return @{ ok = $false; reason = 'bad PE signature' }
    }
    $machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4)
    # Optional header starts at peOffset+24; Subsystem is at offset 68 inside it.
    $subsystem = [BitConverter]::ToUInt16($bytes, $peOffset + 24 + 68)
    $machineName = switch ($machine) { 0x8664 { 'x64' } 0xAA64 { 'arm64' } 0x14c { 'x86' } default { ('0x{0:X}' -f $machine) } }
    $subsystemName = switch ($subsystem) { 2 { 'GUI' } 3 { 'console' } default { $subsystem } }
    return @{
        ok          = ($machine -eq 0x8664)
        machine     = $machineName
        subsystem   = $subsystemName
        reason      = if ($machine -ne 0x8664) { "unexpected machine $machineName" } else { '' }
    }
}

$repo = Split-Path -Parent $PSScriptRoot
$version = (& python (Join-Path $repo 'packaging/build_info.py') --field version).Trim()
$stem = "Hextra-$version-windows-x64"
if ($Backend -eq 'nuitka') { $stem = "$stem-nuitka" }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$target = Join-Path $OutDir "$stem.exe"
Copy-Item -Path $Exe -Destination $target -Force

$pe = Test-PeExecutable -Path $target
$vi = (Get-Item $target).VersionInfo
$hash = (Get-FileHash -Path $target -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $target).Length

Write-Host ''
Write-Host "Backend        : $Backend"
Write-Host "File           : $(Split-Path $target -Leaf)"
Write-Host "Size           : $([math]::Round($size / 1MB, 2)) MB ($size bytes)"
Write-Host "SHA-256        : $hash"
Write-Host "PE machine     : $($pe.machine)"
Write-Host "PE subsystem   : $($pe.subsystem)  (expected: GUI - no console window)"
Write-Host "Product name   : $($vi.ProductName)"
Write-Host "File description: $($vi.FileDescription)"
Write-Host "File version   : $($vi.FileVersion)"
Write-Host "Product version: $($vi.ProductVersion)"
Write-Host ''

$problems = @()
if (-not $pe.ok) { $problems += "PE check failed: $($pe.reason)" }
if ($pe.subsystem -ne 'GUI') { $problems += "expected a GUI (windowed) executable, got '$($pe.subsystem)'" }
if ($vi.ProductName -and $vi.ProductName -ne 'Hextra') { $problems += "unexpected ProductName '$($vi.ProductName)'" }

foreach ($problem in $problems) {
    Write-Host "::warning::$problem"
}

$commit = if ($env:GITHUB_SHA) { $env:GITHUB_SHA } else { (& git -C $repo rev-parse HEAD).Trim() }
[ordered]@{
    product        = 'Hextra'
    version        = $version
    backend        = $Backend
    file           = (Split-Path $target -Leaf)
    bytes          = $size
    sha256         = $hash
    peMachine      = $pe.machine
    peSubsystem    = $pe.subsystem
    fileVersion    = $vi.FileVersion
    productName    = $vi.ProductName
    commit         = $commit
    builtUtc       = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    python         = (& python -c 'import sys;print(sys.version.split()[0])').Trim()
} | ConvertTo-Json | Set-Content -Path (Join-Path $OutDir 'build-info.json') -Encoding utf8

"$hash  $(Split-Path $target -Leaf)" | Set-Content -Path (Join-Path $OutDir 'SHA256SUMS.txt') -Encoding utf8 -NoNewline

if ($env:GITHUB_STEP_SUMMARY) {
    @"

### Hextra $Backend build

| | |
|---|---|
| File | ``$(Split-Path $target -Leaf)`` |
| Size | $([math]::Round($size / 1MB, 2)) MB |
| SHA-256 | ``$hash`` |
| Target | Windows $($pe.machine), $($pe.subsystem) subsystem |
| Version | $($vi.FileVersion) |
| Commit | ``$($commit.Substring(0, [Math]::Min(7, $commit.Length)))`` |

"@ | Add-Content -Path $env:GITHUB_STEP_SUMMARY -Encoding utf8
}

if ($problems.Count -gt 0) {
    Write-Host '::error::Staged exe failed verification.'
    exit 1
}

Write-Host "Staged $target"
exit 0
