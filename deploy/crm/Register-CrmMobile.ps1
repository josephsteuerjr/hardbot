param([Parameter(Mandatory=$true)][string]$Config,[Parameter(Mandatory=$true)][string]$User)
$ErrorActionPreference = 'Stop'
$configPath = (Resolve-Path -LiteralPath $Config).Path
$runner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'Run-CrmMobile.ps1')).Path
if ($configPath.Contains('"') -or $runner.Contains('"')) { throw 'Invalid configuration path' }
$taskName = 'Rimborso CRM Mobile'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -File "'+$runner+'" -Config "'+$configPath+'"'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and ($existing.Actions.Arguments -ne $arguments -or $existing.Principal.UserId -ne $User)) { throw 'A different CRM task already exists; inspect it before replacement' }
$action = New-ScheduledTaskAction -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $arguments
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$options = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Trigger $trigger -Settings $options -Force | Out-Null
Write-Output 'Registered. Excel runs in the dedicated Windows user session; no developer PC is used.'
