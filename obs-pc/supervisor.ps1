# Flybrain supervisor for the OBS machine. Runs every 5 minutes from Task Scheduler
# (and once at logon). The Windows-side counterpart of the Pi's watchdog: it makes
# the stream recover from an OBS crash, hang or reboot with no human and no agent.
#
# Deliberately conservative - it only acts on SUSTAINED failure, so it can never
# fight a planned 120 s maintenance window:
#   panel not running           -> start it (immediately; it is harmless)
#   OBS not running, 2 checks   -> clear crash sentinels, launch OBS --startstreaming
#   2+ OBS instances            -> kill all, clear sentinels, launch one
#                                  (two instances is what crashed OBS on 2026-10-06)
#   OBS hung (no websocket), 2  -> kill, clear sentinels, relaunch
#   OBS up but not streaming, 2 -> StartStream via websocket
# Everything is logged to supervisor.log; every recovery also posts to #fly-log.

$ErrorActionPreference = 'Continue'
$Dir      = $PSScriptRoot
$Py       = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
$PyW      = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
$Cfg      = Get-Content (Join-Path $Dir 'config.json') -Raw | ConvertFrom-Json
$ObsExe   = 'C:\Program Files\obs-studio\bin\64bit\obs64.exe'
$ObsDir   = 'C:\Program Files\obs-studio\bin\64bit'
$Sentinel = "$env:APPDATA\obs-studio\.sentinel"
$StateF   = "$Dir\supervisor_state.json"
$LogF     = "$Dir\supervisor.log"

function Log($m) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $LogF -Value $line -Encoding utf8
    # keep the log bounded
    if ((Get-Item $LogF).Length -gt 1MB) {
        (Get-Content $LogF -Tail 3000) | Set-Content $LogF -Encoding utf8
    }
}

function Alert($m) {
    Log "ALERT $m"
    try {
        $hook = (Get-Content "$Dir\discord_ids.json" -Raw | ConvertFrom-Json).'fly-log'.webhook
        $body = @{ username = 'Supervisor'; content = $m; allowed_mentions = @{ parse = @() } } | ConvertTo-Json -Compress
        # PowerShell 5.1 sends string bodies as ISO-8859-1, which mangles emoji into
        # an invalid request (Discord answers 400). Send explicit UTF-8 bytes.
        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
        Invoke-RestMethod -Uri $hook -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -TimeoutSec 10 | Out-Null
    } catch { Log "alert post failed: $($_.Exception.Message)" }
}

$state = @{ down = 0; hung = 0; idle = 0; bytes = -1 }
if (Test-Path $StateF) {
    try { $j = Get-Content $StateF -Raw | ConvertFrom-Json
          $state.down = [int]$j.down; $state.hung = [int]$j.hung; $state.idle = [int]$j.idle
          if ($null -ne $j.bytes) { $state.bytes = [double]$j.bytes } } catch {}
}
function Save { $state | ConvertTo-Json | Set-Content $StateF -Encoding utf8 }

function Clear-Sentinels {
    $n = @(Get-ChildItem $Sentinel -File -ErrorAction SilentlyContinue).Count
    if ($n) { Get-ChildItem $Sentinel -File | Remove-Item -Force; Log "cleared $n stale OBS sentinel(s)" }
}

function Start-Obs {
    Clear-Sentinels   # otherwise OBS stops on an "unclean shutdown" dialog nobody can click
    Start-Process -FilePath $ObsExe -WorkingDirectory $ObsDir `
        -ArgumentList '--collection',$Cfg.obs_collection,'--scene',$Cfg.obs_scene,'--disable-updater','--startstreaming','--startreplaybuffer'
    Log 'launched OBS with --startstreaming --startreplaybuffer'
}

function Ws-Status {
    # returns 'streaming' | 'idle' | 'dead'
    $code = @"
import sys
sys.path.insert(0, r'$Dir')
try:
    import obsws
    st = obsws.connect().call('GetStreamStatus')
    print(('streaming:%d' % st['outputBytes']) if st['outputActive'] else 'idle')
except Exception:
    print('dead')
"@
    $r = & $Py -c $code 2>$null
    if ($r) { return ($r | Select-Object -Last 1).Trim() } else { return 'dead' }
}

# ---- 1. panel ---------------------------------------------------------------
$panel = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match 'flypanel\.py' }
if (-not $panel) {
    Start-Process -FilePath $PyW -WorkingDirectory $Dir -ArgumentList 'flypanel.py' -WindowStyle Hidden
    Alert '🟠 Supervisor: the Flybrain panel was not running — restarted it.'
}

# ---- 2. OBS ------------------------------------------------------------------
$obs = @(Get-Process obs64 -ErrorAction SilentlyContinue)

if ($obs.Count -gt 1) {
    Alert "🔴 Supervisor: found $($obs.Count) OBS instances — killing all and starting one."
    $obs | Stop-Process -Force
    Start-Sleep -Seconds 5
    Start-Obs
    $state.down = 0; $state.hung = 0; $state.idle = 0; Save; exit
}

if ($obs.Count -eq 0) {
    $state.down++
    Log "OBS not running (check $($state.down))"
    # Straight after a reboot/logon there is nothing to wait for: OBS is simply not
    # started yet, so launch on the first check instead of losing 5-10 minutes.
    $fresh = ((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalMinutes -lt 20
    if ($state.down -ge 2 -or $fresh) {
        Start-Obs
        if ($fresh) { Alert '🟠 Supervisor: PC restarted — launched OBS with streaming on.' }
        else { Alert '🔴 Supervisor: OBS was not running for 5+ min — relaunched it with streaming on.' }
        $state.down = 0
    }
    $state.hung = 0; $state.idle = 0; Save; exit
}
$state.down = 0

$s = Ws-Status
# Backstop for a frozen encoder (GPU driver reset, 2026-10-08): OBS reports "streaming" but
# its byte counter has not moved since the last check 5 min ago. The panel's freeze guard
# normally catches this within 90 s; this covers the panel being down.
if ($s -like 'streaming:*') {
    $b = [double]($s.Split(':')[1])
    if ($state.bytes -ge 0 -and $b -eq $state.bytes) {
        Log "OBS streaming but no bytes sent since last check (frozen encoder)"
        $obs | Stop-Process -Force
        Start-Sleep -Seconds 5
        Start-Obs
        Alert '🔴 Supervisor: OBS said it was streaming but sent nothing for 5+ min (frozen encoder) — restarted OBS.'
        $state.bytes = -1; Save; exit
    }
    $state.bytes = $b
    $s = 'streaming'
} else { $state.bytes = -1 }
if ($s -eq 'dead') {
    $state.hung++
    Log "OBS running but websocket dead (check $($state.hung))"
    if ($state.hung -ge 2) {
        $obs | Stop-Process -Force
        Start-Sleep -Seconds 5
        Start-Obs
        Alert '🔴 Supervisor: OBS was hung (no response for 5+ min) — killed and relaunched it.'
        $state.hung = 0
    }
    $state.idle = 0; Save; exit
}
$state.hung = 0

if ($s -eq 'idle') {
    $state.idle++
    Log "OBS up but not streaming (check $($state.idle))"
    if ($state.idle -ge 2) {
        & $Py -c "import sys; sys.path.insert(0, r'$Dir'); import obsws; obsws.connect().call('StartStream')" 2>$null
        Alert '🟠 Supervisor: OBS was not streaming for 5+ min — started the stream.'
        $state.idle = 0
    }
    Save; exit
}

$state.idle = 0

# ---- 3. replay buffer -------------------------------------------------------
# The panel's "Save last 60s clip" needs it, and OBS does not restart it on its
# own after a crash or a manual relaunch. Cheap to check, harmless to start.
$rb = & $Py -c "import sys; sys.path.insert(0, r'$Dir'); import obsws; o = obsws.connect(); print('on' if o.call('GetReplayBufferStatus')['outputActive'] else (o.call('StartReplayBuffer') or 'started'))" 2>$null
if ($rb -and ($rb | Select-Object -Last 1).Trim() -eq 'started') { Log 'replay buffer was off - started it' }
Save
