# Register (or refresh) the logon task that starts the Codex lifecycle listener.
# The task is the only place that guarantees "follows Codex after logon", so it
# is registered with restart-on-failure as a second layer above the listener's
# own self-healing loop.
param(
  [string]$TaskName = 'CodexQuotaResumeListener',
  [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$watchdog = Join-Path $projectRoot 'watchdog.py'
$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $watchdog)) { throw "watchdog.py not found at $watchdog" }

# A stop request left over from an earlier session would make the fresh logon
# launch exit at once, so the registration clears it like start.ps1 does.
$runtimePath = Join-Path $env:LOCALAPPDATA 'CodexQuotaResume'
foreach ($name in 'lifecycle-stop.request', 'stop.request') {
  Remove-Item -LiteralPath (Join-Path $runtimePath $name) -ErrorAction SilentlyContinue
}

$user = "$env:USERDOMAIN\$env:USERNAME"
# The action is the watchdog, not the listener: the watchdog restarts a killed
# listener within seconds, which Task Scheduler's own restart policy cannot do
# (a process killed from outside leaves the task "Ready", never "failed").
$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $watchdog + '"') -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -MultipleInstances IgnoreNew

# Kept as a backstop for the case where the watchdog itself is killed.
$settings.RestartCount = 999
$settings.RestartInterval = 'PT1M'
$settings.ExecutionTimeLimit = 'PT0S'

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null

# The startup shortcut predates the task and would start a second listener.
$shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Quota Resume.lnk'
$removed = $false
if (Test-Path -LiteralPath $shortcut) {
  Copy-Item -LiteralPath $shortcut -Destination ($shortcut + '.removed') -Force
  Remove-Item -LiteralPath $shortcut -Force
  $removed = $true
}

if (-not $NoStart) { Start-ScheduledTask -TaskName $TaskName }

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
$summary = [pscustomobject]@{
  task_name    = $TaskName
  state        = (Get-ScheduledTask -TaskName $TaskName).State
  last_run     = $info.LastRunTime
  last_result  = $info.LastTaskResult
  restart      = "$($settings.RestartCount) x $($settings.RestartInterval)"
  removed_shortcut = $removed
}
$summary | ConvertTo-Json -Compress
