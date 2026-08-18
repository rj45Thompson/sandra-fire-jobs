# Register Muster to start automatically and stay running.
#
#   powershell -ExecutionPolicy Bypass -File install_service.ps1
#
# Two layers, neither needing administrator rights:
#   1. A scheduled task that fires at logon and restarts on failure
#   2. A Startup-folder shortcut as a belt-and-braces fallback
#
# The watchdog then health-checks the engine every 10 seconds and restarts it
# if it stops, so the only way it stays down is the deliberate stop command.
#
# Remove with:  powershell -File install_service.ps1 -Uninstall

param([switch]$Uninstall)

$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$task  = "MusterEngine"
$startup = [Environment]::GetFolderPath("Startup")
$lnk   = Join-Path $startup "Muster.lnk"
$vbs   = Join-Path $root "run_hidden.vbs"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $lnk -ErrorAction SilentlyContinue
    Write-Host "Auto-start removed."
    Write-Host "The engine may still be running. Stop it with:  py backend\watchdog.py --stop"
    exit 0
}

$py = (Get-Command pyw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "No Python found on PATH." }

# a tiny launcher so nothing flashes a console window on login
@"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$root"
sh.Run "$py backend\watchdog.py", 0, False
"@ | Set-Content $vbs -Encoding ASCII

# ---- layer 1: scheduled task, logon trigger (no admin needed) ----
$taskOk = $false
try {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue

    $action  = New-ScheduledTaskAction -Execute "wscript.exe" `
                 -Argument "`"$vbs`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $set     = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries -StartWhenAvailable `
                 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                 -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
        -Settings $set -Description "Keeps the Muster job engine running." `
        -ErrorAction Stop | Out-Null
    $taskOk = $true
    Write-Host "  [ok] scheduled task registered (runs at logon)" -ForegroundColor Green
} catch {
    Write-Host "  [--] scheduled task unavailable: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ---- layer 2: Startup folder shortcut ----
try {
    $sh = New-Object -ComObject WScript.Shell
    $s  = $sh.CreateShortcut($lnk)
    $s.TargetPath       = "wscript.exe"
    $s.Arguments        = "`"$vbs`""
    $s.WorkingDirectory = $root
    $s.Description      = "Muster job engine"
    $s.Save()
    Write-Host "  [ok] Startup-folder shortcut created" -ForegroundColor Green
} catch {
    Write-Host "  [--] could not create Startup shortcut: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ---- start it now ----
if ($taskOk) { Start-ScheduledTask -TaskName $task } else { & wscript.exe $vbs }
Start-Sleep -Seconds 10

Write-Host ""
Write-Host "  Muster runs in the background now." -ForegroundColor Green
Write-Host ""
Write-Host "  Open        http://127.0.0.1:8770"
Write-Host "  Starts      automatically at logon"
Write-Host "  Recovers    within ~10s if the engine stops"
Write-Host ""
Write-Host "  Stop it:    py backend\watchdog.py --stop"
Write-Host "  Undo all:   powershell -File install_service.ps1 -Uninstall"
Write-Host ""

try {
    $h = (Invoke-WebRequest "http://127.0.0.1:8770/health" -TimeoutSec 12).Content
    Write-Host "  health: $h" -ForegroundColor Green
} catch {
    Write-Host "  engine still coming up - check again in a moment" -ForegroundColor Yellow
}
