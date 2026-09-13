<#
.SYNOPSIS
    Launches a built Hextra executable headlessly and reports whether it exits cleanly.

.DESCRIPTION
    Hextra ships a --smoke-test mode that forces QT_QPA_PLATFORM=offscreen, builds the
    whole shell, and quits after ~800 ms. That makes it possible to prove a packaged exe
    really works (Qt plugins resolve, every page constructs) on a machine with no display,
    which is exactly what a CI runner is.

.PARAMETER Exe
    Path to the built executable.

.PARAMETER TimeoutSeconds
    How long to wait before killing the process and failing.

.EXAMPLE
    pwsh -File packaging/smoke_test.ps1 -Exe dist/Hextra.exe
#>
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'

function Show-CrashLog {
    $log = Join-Path $env:TEMP 'hextra_crash.log'
    if (Test-Path $log) {
        Write-Host "---- $log ----"
        Get-Content $log -Tail 60 | ForEach-Object { Write-Host $_ }
        Write-Host '---- end crash log ----'
    }
}

if (-not (Test-Path $Exe)) {
    Write-Host "::error::Smoke test target not found: $Exe"
    exit 2
}

$item = Get-Item $Exe
Write-Host ("Smoke testing {0} ({1:N1} MB)" -f $item.FullName, ($item.Length / 1MB))

$env:QT_QPA_PLATFORM = 'offscreen'
$watch = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $item.FullName -ArgumentList '--smoke-test' -PassThru -WindowStyle Hidden

if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
    try { $proc.Kill($true) } catch { $proc.Kill() }
    $watch.Stop()
    Write-Host "::error::Smoke test timed out after $TimeoutSeconds s (killed)"
    Show-CrashLog
    exit 3
}

$watch.Stop()
$code = $proc.ExitCode
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue

Write-Host ("Smoke test finished in {0:N1}s with exit code {1}" -f $watch.Elapsed.TotalSeconds, $code)
if ($code -ne 0) {
    Write-Host "::error::Smoke test failed with exit code $code"
    Show-CrashLog
    exit $code
}

Write-Host 'Smoke test passed: the packaged exe builds the full UI headlessly.'
exit 0
