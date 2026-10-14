"""What has happened to this fly, in order.

A viewer arriving mid-stream sees a sprite in a field and has no idea whether
this is minute three or hour forty. The fly has a history -- it caught things,
it earned a badge, chat taught it to avoid people -- and none of it was
anywhere on screen. This keeps that history, across restarts, so the stream
has a past.

Entries are short enough to read at a glance and are written in the order they
happened. The file is small and rewritten whole: at a handful of entries an
hour there is nothing to gain from anything cleverer.
"""
import json
import os
import stat
import time

DIARY = os.path.expanduser("~/.config/flybrain/diary.json")
KEEP = 60                   # entries kept; older ones fall off the end
POST_EVERY_S = 900.0        # how often the fly may tell chat what it has been up to


class Diary:
    """The fly's own history: what happened, when, and how to say it briefly."""

    def __init__(self, path=DIARY, clock=time.time):
        self.path = path
        self.clock = clock
        self.entries = []          # [(when, kind, text)] oldest first
        self.started = self.clock()
        self._posted = 0.0
        self.load()

    # -- writing it down ---------------------------------------------------
    def add(self, kind, text):
        """Record one thing. Returns False if it was a repeat of the last."""
        text = " ".join(str(text).split())[:120]
        if not text:
            return False
        if self.entries and self.entries[-1][1:] == (kind, text):
            return False           # the same news twice is not news
        self.entries.append((self.clock(), kind, text))
        del self.entries[:-KEEP]
        try:
            self.save()
        except OSError:
            pass                   # a diary is not worth stopping the stream for
        return True

    def recent(self, n=3):
        """The last few, newest first, with how long ago in words."""
        now = self.clock()
        out = []
        for when, kind, text in reversed(self.entries[-n:]):
            out.append((self.ago(now - when), kind, text))
        return out

    @staticmethod
    def ago(secs):
        if secs < 90:
            return "just now"
        if secs < 5400:
            return f"{int(secs / 60)}m ago"
        if secs < 172800:
            return f"{int(secs / 3600)}h ago"
        return f"{int(secs / 86400)}d ago"

    def summary(self):
        """One line to catch somebody up, or None if nothing has happened."""
        if not self.entries:
            return None
        counts = {}
        for _, kind, _ in self.entries:
            counts[kind] = counts.get(kind, 0) + 1
        bits = []
        for kind, word in (("caught", "caught"), ("badge", "badge"),
                           ("level", "level-up"), ("faint", "knocked out"),
                           ("learned", "lesson from chat")):
            n = counts.get(kind, 0)
            if n:
                bits.append(f"{n} {word}{'s' if n > 1 and not word.endswith('s') else ''}")
        span = self.ago(self.clock() - self.entries[0][0]).replace(" ago", "")
        head = ", ".join(bits) if bits else "nothing much"
        return f"the fly's last {span}: {head}. latest -- {self.entries[-1][2]}"

    def due_to_post(self):
        """True at most once every POST_EVERY_S, and only with news to tell."""
        now = self.clock()
        if not self.entries or now - self._posted < POST_EVERY_S:
            return False
        if self.entries[-1][0] <= self._posted:
            return False           # nothing new since last time
        self._posted = now
        return True

    # -- keeping it ---------------------------------------------------------
    def save(self):
        """Atomic, like the game's own snapshot: a power cut leaves the old
        file whole rather than half a new one."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as f:
            json.dump({"entries": self.entries[-KEEP:]}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def load(self):
        """Pick the history back up. A missing or damaged file just means the
        fly has no past yet, which is not worth an error."""
        try:
            with open(self.path) as f:
                data = json.load(f)
            out = []
            for row in data.get("entries", []):
                if isinstance(row, list) and len(row) == 3:
                    when, kind, text = row
                    if isinstance(when, (int, float)) and isinstance(kind, str):
                        out.append((float(when), kind, str(text)))
            self.entries = out[-KEEP:]
            return True
        except (OSError, ValueError, AttributeError, TypeError):
            self.entries = []
            return False
