# Lets your phone reach the Flybrain panel from ANYWHERE, privately.
# Right-click -> "Run with PowerShell". It asks for admin itself.
#
# Uses Tailscale: a private network between your own devices. The panel is NOT
# put on the public internet - only devices signed into your Tailscale account
# can reach it, and the panel's own token still applies on top of that.
#
# What this script does:
#   1. Downloads and installs Tailscale from pkgs.tailscale.com (official source)
#   2. Allows the panel port (8777) from Tailscale addresses only (100.64.0.0/10)
#   3. Starts Tailscale in "unattended" mode so it stays connected after reboots
#      and when you are signed out
#   4. Opens a browser for YOU to sign in or create the Tailscale account
#   5. Prints the address to open on your phone

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $PSCommandPath)
    exit
}
$ts = 'C:\Program Files\Tailscale\tailscale.exe'

# --- 1. install ---------------------------------------------------------------
if (Test-Path $ts) {
    Write-Host "1. Tailscale already installed" -ForegroundColor Green
} else {
    Write-Host "1. Downloading Tailscale..." -ForegroundColor Cyan
    $inst = "$env:TEMP\tailscale-setup.exe"
    Invoke-WebRequest "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe" -OutFile $inst -UseBasicParsing
    Write-Host "   installing (a Tailscale window may appear briefly)..." -ForegroundColor Cyan
    Start-Process $inst -ArgumentList '/quiet' -Wait
    $n = 0; while (-not (Test-Path $ts) -and $n -lt 60) { Start-Sleep 2; $n++ }
    if (-not (Test-Path $ts)) { Write-Host "   install did not complete - run the installer by hand: $inst" -ForegroundColor Red; Read-Host "Enter to close"; exit }
    Write-Host "   installed" -ForegroundColor Green
}

# --- 2. firewall: panel reachable over Tailscale only ------------------------
$name = "Flybrain panel 8777 (Tailscale)"
if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort 8777 -RemoteAddress 100.64.0.0/10 -Profile Any | Out-Null
}
Write-Host "2. Firewall: port 8777 allowed from Tailscale addresses only" -ForegroundColor Green

# --- 3+4. connect, unattended -------------------------------------------------
Write-Host "3. Connecting. A browser will open - sign in or create your Tailscale account." -ForegroundColor Cyan
& $ts up --unattended
Start-Sleep 3

# --- 5. address -----------------------------------------------------------------
$ip  = (& $ts ip -4 2>$null | Select-Object -First 1)
$tok = (Get-Content (Join-Path $PSScriptRoot 'panel_access.txt') -ErrorAction SilentlyContinue).Trim()
Write-Host ""
if ($ip) {
    Write-Host "DONE. Now on your phone:" -ForegroundColor Green
    Write-Host "  a) Install the Tailscale app and sign in with the SAME account"
    Write-Host "  b) Open:  http://$($ip):8777/?k=$tok"
    Write-Host "     (works on wi-fi or mobile data, from anywhere)"
} else {
    Write-Host "Not connected yet - finish signing in in the browser, then run this script again." -ForegroundColor Yellow
}
Write-Host ""
Read-Host "Press Enter to close"
