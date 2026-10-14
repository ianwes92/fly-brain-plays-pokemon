"""Check twitch.PLACES against the screen, using the fly as the source of truth.

101 names were taken from the pokefirered decomp and only two were confirmed
by eye. Rather than trust the rest, this watches for the map-section id to
CHANGE and saves the frame at that moment, so each name can be checked against
what was actually on screen when the fly was there.

It also checks the thing a table cannot get subtly wrong without showing it:
whether consecutive places are ADJACENT in the real game. A wrong entry makes
the fly appear to teleport (Route 3 -> Saffron City), and that is visible
without looking at a single pixel.

Writes data/experiments/place_check.jsonl, one row per transition, and a frame
per newly seen id. Run it for as long as you like; it never touches the game.
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from flybrain.twitch import PLACES                                  # noqa: E402

HEALTH = "http://127.0.0.1:8765/health"
STREAM = "http://127.0.0.1:8765/stream.mjpg"
OUT = os.path.join(HERE, "place_check.jsonl")
SHOTS = os.path.join(HERE, "place_shots")

# What can legitimately follow what, in vanilla FireRed. Only the stretch the
# fly can plausibly reach is listed; anything outside it is simply not checked,
# because an unlisted pair means "no opinion", not "wrong".
NEIGHBOURS = {
    88: {101, 89},                 # Pallet Town  <-> Route 1, Viridian
    89: {101, 102, 122},           # Viridian City
    90: {102, 103},                # Pewter City  <-> Route 2, Route 3
    101: {88, 89}, 102: {89, 90, 126}, 103: {90, 104, 127},
    104: {103, 91, 105, 99}, 126: {102},
    127: {103, 104},               # Mt. Moon between Route 3 and Route 4
    91: {104, 105, 124, 109},
}


def frame():
    r = urllib.request.urlopen(STREAM, timeout=10)
    buf = b""
    while len(buf) < 4_000_000:
        c = r.read(65536)
        if not c:
            break
        buf += c
        a = buf.find(b"\xff\xd8\xff")
        if a >= 0:
            e = buf.find(b"\xff\xd9", a)
            if e > 0:
                r.close()
                return buf[a:e + 2]
    return None


def read():
    try:
        t = urllib.request.urlopen(HEALTH, timeout=6).read().decode()
    except OSError:
        return None
    m = re.search(r"place=(-?\d+)", t)
    c = re.search(r"cb2: now=(\S+) overworld=(\S+)", t)
    if not (m and c):
        return None
    # "not in a battle" is NOT the same as "out in the world": the party
    # screen and the bag are neither, and the first run of this saved two
    # frames of menus as its evidence for two place names. A frame only
    # settles anything if the map is actually on screen.
    in_world = c.group(1) == c.group(2) and c.group(2) != "None"
    return int(m.group(1)), in_world


def main(minutes=float(sys.argv[1]) if len(sys.argv) > 1 else 90.0):
    os.makedirs(SHOTS, exist_ok=True)
    prev, seen, odd = None, set(), 0
    end = time.time() + minutes * 60
    while time.time() < end:
        cur = read()
        if cur and cur[0] >= 0 and cur[1]:
            place = cur[0]
            if place != prev:
                name = PLACES.get(place)
                row = {"t": time.time(), "from": prev, "to": place,
                       "name": name, "named": name is not None}
                # the check that needs no eyes: did the fly teleport?
                if prev is not None and prev in NEIGHBOURS:
                    row["adjacent"] = place in NEIGHBOURS[prev]
                    if row["adjacent"] is False:
                        odd += 1
                        print(f"  SUSPECT: {PLACES.get(prev, prev)} -> {name or place} "
                              f"is not a real connection", flush=True)
                with open(OUT, "a") as f:
                    f.write(json.dumps(row) + "\n")
                if place not in seen:
                    seen.add(place)
                    jpg = frame()
                    if jpg:
                        with open(os.path.join(SHOTS, f"place_{place}.jpg"), "wb") as f:
                            f.write(jpg)
                    print(f"  {prev} -> {place}  {name or '(UNNAMED)'}  "
                          f"frame saved", flush=True)
                prev = place
        time.sleep(2)
    print(f"\n{len(seen)} distinct places seen, {odd} suspect transitions")
    print(f"frames in {SHOTS} -- each one shows where the fly was when the id said so")


if __name__ == "__main__":
    main()
