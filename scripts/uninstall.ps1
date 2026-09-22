# Remove the logon task and stop the listener. Delivery history is kept so a
# reinstall never resumes a turn that was already dispatched.
param([string]$TaskName = 'CodexQuotaResumeListener')
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
& (Join-Path $PSScriptRoot 'stop.ps1')
Write-Output "Startup task removed; retained local settings and delivery history."
