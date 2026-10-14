# Lets your phone reach the Flybrain panel on TCP 8777.
#
# HOW TO RUN: right-click this file -> "Run with PowerShell".
# It re-launches itself as administrator, so just approve the prompt.
#
# Scope is LocalSubnet on purpose: only devices on your own network can connect.
# If this laptop joins a cafe or hotel wi-fi, the port stays shut there.

# --- self-elevate ------------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Requesting administrator rights..." -ForegroundColor Yellow
    Start-Process powershell.exe -Verb RunAs `
        -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File', ('"{0}"' -f $PSCommandPath)
    exit
}

# --- add the rule ------------------------------------------------------------
$name = "Flybrain panel 8777"
if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
    Write-Host "Rule already exists - nothing to do." -ForegroundColor Yellow
} else {
    New-NetFirewallRule -DisplayName $name `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8777 `
        -RemoteAddress LocalSubnet -Profile Any | Out-Null
    Write-Host "Added: $name  (TCP 8777, local subnet only)" -ForegroundColor Green
}

Get-NetFirewallRule -DisplayName $name |
    Select-Object DisplayName, Enabled, Direction, Action | Format-Table -AutoSize

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -like '192.168.*' } | Select-Object -First 1).IPAddress
$tok = (Get-Content (Join-Path $PSScriptRoot 'panel_access.txt') -ErrorAction SilentlyContinue).Trim()

Write-Host ""
Write-Host "Open this on your phone (same wi-fi):" -ForegroundColor Cyan
Write-Host ("  http://{0}:8777/?k={1}" -f $ip, $tok)
Write-Host ""
Write-Host "It sets a cookie that lasts a year, so afterwards just {0}:8777 works." -f $ip
Write-Host ""
Read-Host "Press Enter to close"
