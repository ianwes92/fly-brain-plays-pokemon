"""Urges, and the pleasure and pain that follow from acting on them.

Two urges that grow with time, like hunger:

  leave   time spent in the same map    (tau 60 s of game time)  -> exits pull harder
  talk    time since the last conversation (tau 90 s)             -> people pull harder

Acting on an urge is rewarded -- a burst of the fly's reward dopamine neurons
(PAM), sized by how strong the urge was. Overdoing it is punished -- a burst of
its punishment dopamine neurons (PPL1):

  leaving           pleasure, unless it goes straight back to the map it just
                    left within 20 s (dithering in a doorway): pain
  talking           pleasure, unless it is the third conversation with the same
                    person within 2 minutes: pain

Why urges steer directly, not only through pleasure and pain: in this model the
dopamine learning system forms memories in the right neurons, but in
pre-registered experiments those memories did not reliably change the
descending motor neurons (see README). So the urges scale how strongly exits
and people drive their visual channels -- a pull that is measurable -- and the
pleasure and pain are delivered to the real learning system as well, so any
effect of what it learns can be measured on top.

Everything here is plain bookkeeping on game events; no brain involved, so it
is unit-tested in tests/test_motivation.py.
"""
import math


class Motivation:
    def __init__(self, tau_leave_ms=60000.0, tau_talk_ms=90000.0,
                 back_again_ms=20000.0, talk_limit=3, talk_window_ms=120000.0,
                 same_talk_gap_ms=8000.0, burst_ms=500.0):
        self.tau_leave, self.tau_talk = tau_leave_ms, tau_talk_ms
        self.back_again_ms = back_again_ms
        self.talk_limit, self.talk_window = talk_limit, talk_window_ms
        self.same_talk_gap = same_talk_gap_ms
        self.burst_ms = burst_ms

        self.map = None
        self.map_since = 0.0
        self.prev_map, self.left_prev_at = None, -1e12
        self.last_talk = 0.0
        self.talks = {}                 # person key -> list of times
        self.pleasure_until = self.pain_until = -1.0
        self.pleasure_level = 0.0
        self.log = []                   # (t, kind, detail) for display and tests
        self.counts = {"exits": 0, "talks": 0, "pleasure": 0, "pain": 0}

    # -- urges -----------------------------------------------------------------
    def leave_urge(self, t):
        return 1.0 - math.exp(-(t - self.map_since) / self.tau_leave) if self.map is not None else 0.0

    def talk_urge(self, t):
        return 1.0 - math.exp(-(t - self.last_talk) / self.tau_talk)

    # -- events ------------------------------------------------------------------
    def _pleasure(self, t, level, detail):
        self.pleasure_until = t + self.burst_ms
        self.pleasure_level = max(0.0, min(1.0, level))
        self.counts["pleasure"] += 1
        self.log.append((t, "pleasure", detail))

    def _pain(self, t, detail):
        self.pain_until = t + self.burst_ms
        self.counts["pain"] += 1
        self.log.append((t, "pain", detail))

    def on_map(self, t, map_id):
        """Call every frame with the current (group, num); detects exits taken."""
        if map_id is None:
            return
        if self.map is None:
            self.map, self.map_since = map_id, t
            return
        if map_id == self.map:
            return
        urge = self.leave_urge(t)
        came_back = (map_id == self.prev_map and t - self.left_prev_at <= self.back_again_ms)
        self.counts["exits"] += 1
        if came_back:
            self._pain(t, f"straight back to {map_id}")
        else:
            self._pleasure(t, urge, f"left {self.map} for {map_id} (urge {urge:.2f})")
        self.prev_map, self.left_prev_at = self.map, t
        self.map, self.map_since = map_id, t

    def on_talk(self, t, person_key):
        """Call when A is pressed facing a person. Mashing through one dialogue
        is one conversation: repeats with the same person inside
        same_talk_gap_ms are ignored."""
        times = self.talks.setdefault(person_key, [])
        if times and t - times[-1] < self.same_talk_gap:
            return False
        times[:] = [x for x in times if t - x <= self.talk_window] + [t]
        urge = self.talk_urge(t)
        self.counts["talks"] += 1
        if len(times) >= self.talk_limit:
            self._pain(t, f"talked to {person_key} {len(times)} times in {self.talk_window/1000:.0f} s")
        else:
            self._pleasure(t, urge, f"talked to {person_key} (urge {urge:.2f})")
        self.last_talk = t
        return True

    def manual(self, t, kind):
        """A burst delivered by hand -- the pleasure and pain buttons. Full
        strength, and it opens the learning gate exactly as a game event does,
        so whatever the fly is perceiving at that moment is what it learns
        about."""
        self.counts.setdefault("button_" + kind, 0)
        self.counts["button_" + kind] += 1
        if kind == "pleasure":
            self._pleasure(t, 1.0, "button")
        else:
            self._pain(t, "button")

    def pleasure(self, t):
        return t < self.pleasure_until

    def pain(self, t):
        return t < self.pain_until
