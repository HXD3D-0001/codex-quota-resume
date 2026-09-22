$ErrorActionPreference = 'Stop'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Quota Resume.lnk'
Remove-Item -LiteralPath $shortcutPath -ErrorAction SilentlyContinue
& (Join-Path $PSScriptRoot 'stop.ps1')
Write-Output 'Startup removed; retained local settings and delivery history.'
