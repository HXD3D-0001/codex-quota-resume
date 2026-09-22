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
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Quota Resume.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = '-NoProfile -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'start.ps1') + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Codex quota monitor and automatic task continuation'
$shortcut.Save()
& (Join-Path $PSScriptRoot 'start.ps1')
Write-Output 'Installed startup shortcut and launched monitor.'
