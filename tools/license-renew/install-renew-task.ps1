# 注册 Trae Work 助手「授权自动续期」计划任务
#   触发：登录后 3 分钟  +  每天 10:10
#   动作：pythonw.exe renew_license.py --quiet   （无窗口，日志落盘）
#   卸载：Unregister-ScheduledTask -TaskName TraeWorkLicenseRenew -Confirm:$false

$ErrorActionPreference = 'Stop'

$base   = $PSScriptRoot
$wd     = $base
$script = Join-Path $wd 'renew_license.py'

if (-not (Test-Path $script)) {
    Write-Host "[错误] 找不到 $script" -ForegroundColor Red
    exit 1
}

$py = Join-Path $env:LOCALAPPDATA 'Python\bin\pythonw.exe'
if (-not (Test-Path $py)) { $py = 'pythonw.exe' }

try {
    $act = New-ScheduledTaskAction -Execute $py -Argument ('"' + $script + '" --quiet') -WorkingDirectory $wd
    $t1  = New-ScheduledTaskTrigger -AtLogOn
    $t1.Delay = 'PT3M'
    $t2  = New-ScheduledTaskTrigger -Daily -At '10:10'
    $set = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $set.ExecutionTimeLimit = 'PT5M'

    Register-ScheduledTask -TaskName 'TraeWorkLicenseRenew' `
        -Action $act -Trigger $t1,$t2 -Settings $set `
        -Description 'Trae Work 助手授权(license_guard)自动续期：剩余<=2天时用口令静默换新7天授权' -Force | Out-Null

    Get-ScheduledTask -TaskName 'TraeWorkLicenseRenew' | Select-Object TaskName, State | Format-List
    Get-ScheduledTaskInfo -TaskName 'TraeWorkLicenseRenew' | Select-Object NextRunTime | Format-List
    Write-Host '[完成] 已注册计划任务 TraeWorkLicenseRenew（登录后3分钟 + 每天10:10）' -ForegroundColor Green
    Write-Host '       卸载：Unregister-ScheduledTask -TaskName TraeWorkLicenseRenew -Confirm:$false'
} catch {
    Write-Host "[失败] $_" -ForegroundColor Red
    Write-Host '       可尝试以管理员身份重跑本脚本。' -ForegroundColor Yellow
    exit 1
}
