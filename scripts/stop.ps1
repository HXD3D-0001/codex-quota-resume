$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $env:LOCALAPPDATA 'CodexQuotaResume'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $runtimePath 'lifecycle-stop.request') -Force | Out-Null
python (Join-Path $projectRoot 'control.py') stop
