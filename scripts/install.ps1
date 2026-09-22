# Install the UI runtime, initialize state, and register logon startup.
param([Parameter(Mandatory=$true)][string]$OwnerThreadId)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$uiRuntime = Join-Path $env:LOCALAPPDATA 'CodexQuotaResume/ui-runtime'
if (-not (Test-Path -LiteralPath (Join-Path $uiRuntime 'PySide6/QtWidgets.pyd'))) {
  python -m pip install --disable-pip-version-check --target $uiRuntime -r (Join-Path $projectRoot 'requirements-ui.txt')
  if ($LASTEXITCODE -ne 0) { throw 'UI runtime installation failed' }
}
python (Join-Path $projectRoot 'control.py') init --owner $OwnerThreadId
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed' }
# Registers the logon task and removes the pre-0.3 startup shortcut.
& (Join-Path $PSScriptRoot 'register-task.ps1')
Write-Output 'Installed. Run "python control.py doctor" to verify startup sync.'
