# Operations runbook

The system is built to run for weeks with nobody watching. This page lists what runs by itself,
what alerts you (Discord `fly-log`), and the few things that need a human.

## Runs by itself

| what | when |
|---|---|
| Stream restart + YouTube broadcast rollover (each broadcast ≤12 h, so it is archived) | daily maintenance windows (default 07:40 and 19:40) |
| New Lab Run: archive, fresh game, untrained brain, announcements | every 14 days (Pi run clock, 13:00; warning the day before) |
| Weekly run report (PDF to Discord `run-reports`) | day 7 of each run |
| Final run report | when the run resets |
| Panel restart if it dies; OBS relaunch if it crashes, hangs, stops streaming or freezes | supervisor, every 5 min |
| OBS restart if the encoder stops sending bytes (GPU driver reset) | panel freeze guard, 90 s |
| Game layer reload if the picture freezes while the Pi plays | panel game guard, 30 s |
| Game restart if the emulator stalls | Pi watchdog |
| Token renewals (Twitch bridge, Threads) | Pi, automatic |
| Nightly backup of code, saves and learned weights | Pi, 04:30 |

## Alerts and what to do

| alert in `fly-log` | do this |
|---|---|
| "OBS PC may have rebooted and needs its PIN" | Sign in to the PC. Everything else starts by itself (panel at logon, supervisor launches OBS streaming). |
| "Twitch login failed / token revoked" | On the Pi run the device login printed in the alert and approve the code at twitch.tv/activate while signed in to the channel's account. |
| "Stream may be stalled / frozen encoder" (repeats) | Usually a GPU driver reset; the guards recover it. If it keeps happening, update the graphics driver. |
| "YouTube still not live after maintenance" | Check YouTube Studio; the Pi keeper normally recovers within 5 min. Twitch is unaffected. |
| "Instagram token expires in N days" | Follow the steps in the alert (renew the long-lived token). |
| Weekly/final report failed | It retries every 10 min. If it keeps failing, `python run_report.py --run N --post` on the PC shows the error. |

## Rolling back the stream layout

OBS keeps the previous scene. Switching the program scene back is instant; the supervisor's relaunch
scene is set by `obs_scene` in `config.json`.
