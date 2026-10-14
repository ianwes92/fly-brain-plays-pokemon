# Makes this machine safe to run the stream unattended.
# Right-click -> "Run with PowerShell". It asks for admin itself.
#
# What it changes, and why:
#   1. Never sleep / hibernate on mains power. If OBS ever dies, Windows would
#      otherwise sleep after 15 min and take the panel and supervisor with it.
#   2. Windows Update may not auto-restart while you are signed in. Right now it
#      is allowed to reboot between midnight and 6am, which would leave the
#      stream dead at the login screen until morning. Updates still install;
#      they just wait for a restart you choose.
#
# It does NOT set up auto-login - that needs your password and is a security
# choice you should make deliberately. See the note printed at the end.

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $PSCommandPath)
    exit
}

Write-Host "1. Power: never sleep or hibernate on mains power" -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
Write-Host "   done" -ForegroundColor Green

Write-Host "2. Windows Update: no automatic restart while signed in" -ForegroundColor Cyan
$k = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
New-Item -Path $k -Force | Out-Null
Set-ItemProperty -Path $k -Name NoAutoRebootWithLoggedOnUsers -Value 1 -Type DWord
Write-Host "   done" -ForegroundColor Green

Write-Host ""
Write-Host "Verify:" -ForegroundColor Cyan
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE | Select-String "Current AC"
(Get-ItemProperty $k).NoAutoRebootWithLoggedOnUsers | ForEach-Object { "   NoAutoRebootWithLoggedOnUsers = $_" }

Write-Host ""
Write-Host "STILL YOUR CALL - auto-login after a power cut:" -ForegroundColor Yellow
Write-Host "  Without it, a reboot leaves the PC at the login screen and nothing starts."
Write-Host "  Microsoft's tool stores the password encrypted rather than in plain text:"
Write-Host "    https://learn.microsoft.com/sysinternals/downloads/autologon"
Write-Host "  Trade-off: anyone with physical access to this PC gets into your account."
Write-Host ""
Read-Host "Press Enter to close"
