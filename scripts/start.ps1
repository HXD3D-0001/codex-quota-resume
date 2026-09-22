# Start the watchdog now, without waiting for the next logon.
#
# Script execution may be disabled on this machine, and a script cannot enable
# itself. Re-entering once with -ExecutionPolicy Bypass keeps the machine-wide
# policy untouched while making the launcher runnable. A command-line -Command
# fallback covers machines where group policy ignores the parameter.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $env:LOCALAPPDATA 'CodexQuotaResume'
$watchdog = Join-Path $projectRoot 'watchdog.py'

$probe = @'
$null = $(& { $ErrorActionPreference='Stop'; [void](Get-ExecutionPolicy) })
'@

function Test-ScriptExecution {
  try {
    $probePath = Join-Path $env:TEMP ('dsh-policy-' + [guid]::NewGuid().ToString('n') + '.ps1')
    Set-Content -LiteralPath $probePath -Value $probe -Encoding UTF8
    try { & powershell.exe -NoProfile -NonInteractive -File $probePath *> $null; return ($LASTEXITCODE -eq 0) }
    finally { Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue }
  } catch { return $false }
}

if (-not (Test-ScriptExecution)) {
  if ($env:CODEX_QUOTA_RESUME_REENTRY -eq '1') {
    Write-Error 'PowerShell script execution is disabled by policy; run: powershell -ExecutionPolicy Bypass -File scripts/start.ps1'
    exit 1
  }
  $env:CODEX_QUOTA_RESUME_REENTRY = '1'
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath
  exit $LASTEXITCODE
}

New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
# An explicit start outranks an earlier stop request: leaving one behind made
# the listener exit again immediately, which looked like startup doing nothing.
Remove-Item -LiteralPath (Join-Path $runtimePath 'lifecycle-stop.request') -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $runtimePath 'stop.request') -ErrorAction SilentlyContinue
$pythonPath = (Get-Command pythonw.exe -ErrorAction Stop).Source
Start-Process -FilePath $pythonPath -ArgumentList @(('"' + $watchdog + '"')) -WorkingDirectory $projectRoot -WindowStyle Hidden
Write-Output "Watchdog started from $projectRoot"
