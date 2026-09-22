$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
python (Join-Path $projectRoot 'control.py') stop
