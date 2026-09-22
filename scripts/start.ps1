$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $env:LOCALAPPDATA 'CodexQuotaResume'
New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
Remove-Item -LiteralPath (Join-Path $runtimePath 'lifecycle-stop.request') -ErrorAction SilentlyContinue
$pythonPath = (Get-Command pythonw.exe -ErrorAction Stop).Source
Start-Process -FilePath $pythonPath -ArgumentList @(('"' + (Join-Path $projectRoot 'lifecycle.py') + '"')) -WorkingDirectory $projectRoot -WindowStyle Hidden
