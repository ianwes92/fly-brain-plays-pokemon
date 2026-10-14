"""The fly's news, numbered, for anything outside the game that repeats it.

The Discord poster (and anything after it) needs to answer one question after
every restart: what has happened that I have not told people about yet? That
needs an id per event which only ever goes up and survives the game stopping,
so a poster can ask for everything after the last one it handled and get each
piece of news exactly once -- not again after a deploy, and not never.

It is deliberately NOT its own detector. Everything here comes from
`game.milestones`, the same call that rings the buzzer, posts in Twitch chat,
writes the diary and rewards the fly. Two detectors drift apart and then the
channels disagree with each other, which looks broken to everyone watching.

Small enough to rewrite whole on every change: a few events an hour.
"""
import json
import os
import stat

EVENTS = os.path.expanduser("~/.config/flybrain/events.json")
KEEP = 200               # more than any poller should ever be behind

# The game's own words for these on the left; what anything outside is told on
# the right. Only "caught" differs, and it differs because the outside name was
# agreed first -- renaming the game's half would touch the diary, the rewards
# table and the tests for a cosmetic gain.
KIND_NAMES = {"caught": "catch"}


class EventLog:
    """Numbered news, kept across restarts."""

    def __init__(self, path=EVENTS, keep=KEEP):
        self.path = path
        self.keep = keep
        self.events = []       # [{"id", "kind", "text"}] oldest first
        self.next_id = 1
        self.load()

    def add(self, kind, text, short=None, credit=None):
        """Record one piece of news. Returns its id, or None if it was empty.

        The id comes from a counter that is saved with the events, never from
        the length of the list -- the list is trimmed, so its length goes down
        and an id taken from it would repeat.
        """
        text = " ".join(str(text).split())[:200]
        if not text:
            return None
        ev = {"id": self.next_id, "kind": KIND_NAMES.get(kind, kind), "text": text}
        if short:
            # The vertical Short being cut for this moment. Its presence tells
            # the Discord poster to hold this news and send it WITH the video
            # as one message; its absence (a level, or a moment rate-limited
            # out of a Short) means post the text straight away.
            ev["short"] = str(short)
        if credit:
            # Who made it happen (attrib.Attribution.credit): "fly" when no
            # human key had reached the game for CLEAN_MS, else "assisted".
            ev["by"] = credit.get("by")
            ev["credit"] = credit
        self.next_id += 1
        self.events.append(ev)
        del self.events[:-self.keep]
        try:
            self.save()
        except OSError:
            pass               # news is not worth stopping the stream for
        return ev["id"]

    def since(self, after=0, limit=50):
        """Everything newer than `after`, oldest first.

        A caller that has been away longer than KEEP cannot be given what was
        trimmed; it gets what is left rather than nothing, because a poster
        silently posting nothing for ever is the worse failure.
        """
        try:
            after = int(after)
        except (TypeError, ValueError):
            after = 0
        return [e for e in self.events if e["id"] > after][:limit]

    # -- keeping it ---------------------------------------------------------
    def save(self):
        """Atomic: a power cut leaves the old file whole rather than half a
        new one. Half a file would reset the counter and re-announce
        everything, which is exactly what the ids exist to prevent."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as f:
            json.dump({"next_id": self.next_id, "events": self.events[-self.keep:]}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def load(self):
        """Pick the numbering back up.

        A damaged or missing file must not restart the count at 1: a poster
        that has already seen id 40 would then be sent 1..40 again as news. So
        the counter starts past the highest id that can still be read, and if
        nothing can be read at all the log starts empty -- which produces no
        news rather than old news.
        """
        try:
            with open(self.path) as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            return False
        out = []
        for row in data.get("events", []) or []:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                ev = {"id": row["id"], "kind": str(row.get("kind", "")),
                      "text": str(row.get("text", ""))}
                if row.get("short"):
                    # Kept across a restart: a game restarting while a Short is
                    # still rendering must not quietly turn a video post into text.
                    ev["short"] = str(row["short"])
                out.append(ev)
        out.sort(key=lambda e: e["id"])
        self.events = out[-self.keep:]
        highest = self.events[-1]["id"] + 1 if self.events else 1
        saved = data.get("next_id")
        self.next_id = max(highest, saved if isinstance(saved, int) else 1)
        return True
