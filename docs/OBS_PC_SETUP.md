# Streaming PC setup (Windows)

The PC runs OBS and a small Python control panel. It does no simulation: the Pi does all the
thinking and playing. Everything here is standard-library Python except the run reports
(matplotlib, pypdfium2 optional for previews).

## 1. Install

* **Python 3.12** (from python.org, "Add to PATH" ticked) — `python` and `pythonw` must be on PATH.
* `pip install matplotlib` (only needed for the PDF run reports).
* **OBS Studio 30+**. In *Tools → WebSocket Server Settings*, enable the server (port 4455). A
  password is fine: the panel reads it from OBS's own config file.
* Optional: StreamElements (or similar) for alerts/tipper — add it on top of the scene yourself.

## 2. Configure

Copy the `obs-pc` folder somewhere permanent (e.g. `C:\flybrain`), then:

```
copy config.example.json config.json
```

Fill in `config.json`:

| key | meaning |
|---|---|
| `pi_host` | the Pi's LAN address (give it a DHCP reservation) |
| `twitch_channel`, `twitch_broadcaster_id` | your channel name and numeric user id |
| `youtube_channel_id`, `youtube_handle`, `instagram` | optional, for panel links and report footers |
| `obs_collection`, `obs_scene` | the OBS scene collection and the program scene the supervisor relaunches |
| `maintenance` | daily stream restarts `[["HH:MM", seconds], ...]` — keep them 12 h apart so YouTube archives every broadcast (≤12 h) |
| `clips_dir` | where OBS saves replay clips |

Secrets go in their own files next to it (all git-ignored):

* `panel_token.txt` — the Pi relay's bearer token (generated on the Pi, see `pi/README.md`). Without
  it the panel shows status only.
* `panel_access.txt` — a random string; any device other than localhost must open the panel as
  `http://<pc>:8777/?k=<that string>` once (sets a cookie).
* `discord_ids.json` — Discord webhook URLs per channel (`fly-log`, `run-reports`, ...), optional.

## 3. Run the panel

```
python flypanel.py           # http://127.0.0.1:8777
```

Tabs: **Status** (every service as one strip), **Drive** (live view + pad that types in chat as the
streamer, take control, pain/pleasure), **Science** (live brain, findings, movement, learning,
thoughts, turn sides...). It also serves the OBS layers `/hud` and `/brainviz`.

To start it at logon without a window, put a copy of `start_panel.vbs` (edit its folder) in
`shell:startup`.

## 4. Build the OBS scene

With the panel running and the Pi up:

```
python setup_obs_scene.py
```

It creates the scene from `config.json` with three browser sources — **Game** (the Pi's bare GBA
frame, 1152×768), **Brainviz** (768×670 at 1152,0) and **HUD** (1920×1080). Set the canvas to
1920×1080 @ 30 fps, make the scene the program scene, add your alert widgets on top, and configure
your stream service/key as usual.

## 5. Unattended operation

* **Supervisor:** create a Task Scheduler task that runs `wscript.exe "<folder>\run_supervisor.vbs"`
  at logon and every 5 minutes (stop the task if it runs longer than 4 min; "do not start a new
  instance"). It restarts the panel if it dies, relaunches OBS if it crashes, hangs, stops streaming
  or freezes (sends no bytes), and keeps the replay buffer running.
* **Inside the panel:** a freeze guard restarts OBS if the encoder stops sending bytes for 90 s
  (e.g. after a GPU driver reset), and a game guard reloads the Game layer if it stops moving while
  the Pi keeps playing. Maintenance windows end the YouTube broadcast so each one is archived.
* `harden_windows.ps1` (run as admin once): never sleep, no automatic Windows Update reboots.
* `setup_remote_access.ps1` / `allow_panel_port.ps1`: open the panel to your LAN/Tailscale.
* Alerts (stream down, frozen picture, reports) go to the Discord `fly-log` webhook.

## 6. Run reports

`run_report.py` builds a PDF for a run from the Pi's `/panel/run_export`. The panel posts a
**weekly** report while a run is live and a **final** report when the Pi's run clock resets it, to
the Discord `run-reports` webhook. Manually: `python run_report.py --run 4 [--week 1] [--post]`.
