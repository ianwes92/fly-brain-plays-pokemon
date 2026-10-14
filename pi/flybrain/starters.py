"""Which starter the fly picked, run by run -- a running scoreboard, just for fun
(Ian, 2026-10-08: "use it as a gimmick to see who the fly 'prefers'"). Not
science: where the player stands at the table, the A-press timing and chance
all go into a pick, and mirroring would only swap Bulbasaur and Charmander.

~/.config/flybrain/starters.json: {"<run>": {"species": "SQUIRTLE", "by": "fly|chat|?"}}.
Runs 1-2 predate attribution ("?"); run 3 (Squirtle) and run 4 (Charmander)
were picked with no human key for minutes before ("fly"), though a human had
moved the player earlier in both.
"""
import json
import os

PATH = os.path.expanduser("~/.config/flybrain/starters.json")
ORDER = ("SQUIRTLE", "CHARMANDER", "BULBASAUR")


def load(path=None):
    try:
        with open(path or PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record(run, species, by, path=None):
    """Note run's starter, once. Returns True if this was new."""
    p = path or PATH
    d = load(p)
    if str(run) in d:
        return False
    d[str(run)] = {"species": str(species).upper(), "by": by}
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, p)
    return True


def tally(d=None):
    d = load() if d is None else d
    n = {s: 0 for s in ORDER}
    for v in d.values():
        s = str(v.get("species", "")).upper()
        n[s] = n.get(s, 0) + 1
    return n


def scoreboard(d=None):
    """'Squirtle 3 · Charmander 1 · Bulbasaur 0' (biggest first, ties in ORDER)."""
    n = tally(d)
    keys = sorted(n, key=lambda s: (-n[s], ORDER.index(s) if s in ORDER else 9))
    return " · ".join(f"{s.title()} {n[s]}" for s in keys)


def health_line(d=None):
    """starters: 1=SQUIRTLE:? 2=SQUIRTLE:? 3=SQUIRTLE:fly 4=CHARMANDER:fly"""
    d = load() if d is None else d
    runs = sorted(d, key=lambda k: int(k) if str(k).isdigit() else 0)
    return "starters: " + " ".join(f"{r}={d[r].get('species')}:{d[r].get('by', '?')}" for r in runs)
