# Fruit Fly Plays Pokémon — the Raspberry Pi side

A whole-brain simulation of an adult fruit fly (FlyWire FAFB v783: 139,255
neurons, ~2.6 M connections, leaky integrate-and-fire) plays Pokémon FireRed on
a Raspberry Pi 5, live, 24/7. Twitch chat drives alongside it, and `!pain` /
`!pleasure` train its mushroom body. This folder is everything that runs on the
Pi: the brain, the emulator bridge, the stream relay, the chat bot, the science
logging, the run clock and the posters. The OBS / Windows side is in `../obs-pc/`.

Nothing here contains credentials, saves, data or the game ROM.

## Hardware

- Raspberry Pi 5, 16 GB RAM (8 GB works without the brain layer exports).
- NVMe SSD (the snapshots and logs are written continuously; SD cards wear out).
- The official 27 W USB-C supply. Under-voltage makes the fly sluggish and is
  reported to Discord.
- Active cooling. The simulation keeps all four cores busy.
- Wired network. The stream is encoded by OBS on another PC on the LAN,
  which reads the Pi's relay on port 8765.

## OS and packages

Raspberry Pi OS (64-bit, Debian 12 "bookworm"), desktop edition: the game runs
in a pygame window.

```sh
sudo apt install python3-numpy python3-pygame python3-pil libmgba-dev gcc git
```

The two small C extensions (the spike-step kernel and the libmgba shim) compile
themselves on first run with gcc.

## 1. Get the code

```sh
git clone <this repo> ~/flybrain-repo
ln -s ~/flybrain-repo/pi ~/flybrain      # the code expects to live at ~/flybrain
```

## 2. The connectome (FlyWire v783)

```sh
cd ~/flybrain
./fetch_data.py         # ~215 MB from Zenodo record 17030629, resumable, checksum-verified
./flybrain.py build     # one-time conversion to data/connectome.npz
```

`fetch_data.py` downloads into `data/`: `neurons.csv.gz`, `classification.csv.gz`,
`connections_no_threshold.csv.gz`, `cell_stats.csv.gz`, `visual_neuron_types.csv.gz`.

The data are CC-BY 4.0. If you use them, cite:
Dorkenwald et al., "Neuronal wiring diagram of an adult brain", Nature 634, 124–138 (2024);
Schlegel et al., "Whole-brain annotation and multi-connectome cell typing of
Drosophila", Nature 634, 139–152 (2024).

Optional, for the OBS brain layer's real neuron positions: `play/brain_layout.py build`,
then `play/brain_positions.py` on `flywire_synapses_783.feather` (Zenodo
10676866, 9.5 GB, needs `pyarrow` in a venv). See the docstrings.

## 3. The game ROM: bring your own

The ROM is not distributed and never will be. Use a legally obtained copy of
**Pokémon FireRed (USA) 1.0** (`BPRE`, rev 0) and put it at:

```
~/flybrain/play/firered.gba
```

The battery save `firered.sav` and the resume snapshots are created next to it.

## 4. Configuration

See [config/README.md](config/README.md). In short:

```sh
mkdir -p ~/.config/flybrain
cp config/site.example.json ~/.config/flybrain/site.json   # then edit: your channel, ids
```

Every secret lives in its own file under `~/.config/flybrain/`, mode 0600, never in git.

## 5. Services

```sh
mkdir -p ~/.config/systemd/user ~/.config/autostart
cp systemd/flybrain-*.service systemd/flybrain-*.timer ~/.config/systemd/user/
cp autostart/flybrain-game.desktop ~/.config/autostart/
systemctl --user daemon-reload
systemctl --user enable --now flybrain-relay.service flybrain-watchdog.service \
    flybrain-discord.service flybrain-youtube.service flybrain-ext.service \
    flybrain-run-clock.timer flybrain-threads-sample.timer flybrain-twitch-relogin.timer \
    flybrain-battery-plan.timer flybrain-battery-notice.timer flybrain-battery-run.timer
sudo loginctl enable-linger $USER         # keep the user services running without a login
```

Enable only what you use. Without its credentials, each poster logs that and
waits. `flybrain-threads`, `flybrain-igstory` and `flybrain-backup` (a push to
your own private git repo) are optional. The game itself starts at desktop login
from the autostart entry. `play/launch.sh` restarts it if it exits, and the
watchdog restarts it if the emulator stalls.

Deploying a code change: `bash play/restart.sh` (the game saves, exits and comes
back in ~20 s; it never loses learned state).

## 6. Is it running?

```sh
curl -s localhost:8766/health | head -20        # the game: phase=playing, team, run, brain
curl -s localhost:8765/health | head -3         # the relay that OBS reads
systemctl --user list-timers 'flybrain*'
tail play/launcher.log
python3 -m pytest -q tests 2>/dev/null || for t in tests/test_*.py; do python3 $t >/dev/null || echo FAIL $t; done
```

OBS on the other PC uses `http://<pi>:8765/game` (the bare game),
`/panel/live`, `/panel/overlay` and `/panel/brain_layout` (bearer token, see
config/README.md). The old composed view is at `http://<pi>:8765/`.

## Science

Every claim on stream comes from code in this folder. `data/experiments/`
holds the pre-registrations (`*_PREREG.md`), harnesses and frozen scorers.
`STANDING_BATTERY_PREREG.md` is the experiment that runs automatically after
every lab run. Raw data is not in the repo.
