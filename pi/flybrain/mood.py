"""Mood: how the fly feels about where it is, read from its own brain.

Two buttons deliver pleasure (a burst in the reward dopamine neurons, PAM) or
pain (the punishment ones, PPL1). What they are for, in the user's words:
pain marks *whatever causes it distress*, and pleasure *promotes seeking the
same behaviour*. That is what a fly's mushroom body and its way of navigating
already do, so both are built from those rather than from a score.

HOW IT FEELS -- valence = tanh(acute + learned), -1 distressed .. +1 content.

  acute    Phasic dopamine: reward (PAM) bursts minus punishment (PPL1) bursts,
           through a leaky integrator with a 20 s time constant -- flies hold a
           persistent internal state after a threat rather than snapping back
           when it stops (Gibson et al. 2015). Phasic, not tonic, because tonic
           PPL1 swings 0-19 Hz on visual input alone (mean 3.3, sd 3.5): a
           first version tracked raw rates against a baseline and read that
           flicker as joy, pinned at maximum before any button was pressed.
           Bursts are unmistakable by comparison -- measured peaks PAM 71.7 Hz
           against a tonic max of 0.83, PPL1 69.9 Hz against 18.9 -- so only
           firing above a threshold well clear of the tonic range counts.

  learned  How THIS situation has been treated before. Kenyon cells encode what
           the fly is perceiving; their recent activity (the learning rule's
           eligibility trace) is the current context. Pain depresses those
           cells' synapses onto the MBONs that punishment teaches, which in the
           fly promote approach; pleasure depresses the ones reward teaches,
           which promote avoidance. So for the Kenyon cells active right now,
           the fraction of approach drive lost minus the fraction of avoidance
           drive lost is the learned valence of the present situation -- not a
           global average. Pain in front of one NPC should sour that NPC, not
           everything. (Whether Kenyon cell patterns actually separate game
           situations well enough for that is an empirical question, and is
           measured, not assumed.) Fades as the synapses recover, tau 10 min.

WHAT A FEELING DOES is a design choice, and has to be: dopamine is modulatory,
so in this model the 320 dopamine neurons have 24 fast outgoing edges between
them and a burst reaches nothing directly; and in pre-registered tests the
mushroom body's memory did not reliably reach the motor neurons. So the brain
decides the feeling; this module decides what the feeling does, using the rule
flies and larvae actually navigate by (klinokinesis): when things are good or
improving, keep going; when they are bad or getting worse, turn and try
something else.

  content      Persist. Spontaneous turns need a bigger swing, and the walking
               bout's hysteresis widens -- keep walking if walking, keep still
               if still.
  distressed   Change course and get away. Spontaneous turns come easily,
               walking starts on less drive, exits pull more.
  worsening    A falling valence adds turning on top, whatever its level.

AND SPECIFICALLY, per kind of thing (opinions()): the mushroom body is asked
what it makes of each scent's Kenyon cell signature, relative to the others.
A feared kind is turned AWAY from when seen, a liked one approached, and a
strong opinion either way reacts on a weaker signal. Scent says what is near;
vision says which way -- this is where the two meet. Mood alone could only
make the fly jumpier everywhere, which is why training showed no visible
effect before this existed.

The coupling acts only while a Mood is attached to a Game, which only the play
window does. Experiments never attach one, so nothing they measure changes
because this exists.
"""
import math
import os

import numpy as np

from .connectome import LEARNING_RAW


class Mood:
    TAU_ACUTE_MS = 20_000.0      # how long a fright or a treat lingers
    TAU_TREND_MS = 1_500.0       # smoothing for 'getting better or worse'
    # Phasic thresholds, from 30 s of tonic play (tonic max PAM 0.83 Hz, PPL1
    # 18.9 Hz) and single bursts (peaks 71.7 and 69.9 Hz).
    PAM_PHASIC_HZ = 10.0
    PPL1_PHASIC_HZ = 30.0
    # Excess above threshold in one burst, integrated (Hz x s), measured:
    # PAM ~(70-10) Hz for ~0.6 s, PPL1 ~(65-30) Hz for ~0.45 s. Each is scaled
    # so a single burst of either moves the acute state by BURST_STEP -- equal
    # weight to a unit of pleasure and a unit of pain.
    PAM_BURST_HZS = 36.0
    PPL1_BURST_HZS = 16.0
    BURST_STEP = 0.4
    LEARNED_EVERY_MS = 250.0     # how often the learned part of mood is recomputed
    LEARNED_SCALE = 0.25         # fraction of drive lost that counts as a full unit
    TREND_GAIN = 2.0             # turning added per unit/s of falling valence

    def __init__(self, cx, raw=LEARNING_RAW, signatures=None):
        L = cx.labels
        name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
        cl = L["class"]
        dan = cl == "DAN"
        self.pam = np.flatnonzero(dan & np.char.startswith(name, "PAM"))
        self.ppl1 = np.flatnonzero(dan & np.char.startswith(name, "PPL1"))

        # Sort MBONs by which dopamine cluster teaches them, from the actual
        # DAN -> MBON dopamine synapses. A clear majority is required; MBONs
        # taught about equally by both belong to neither side.
        mbon = np.flatnonzero(cl == "MBON")
        self.approach = self.avoid = np.array([], dtype=int)
        if os.path.exists(raw):
            z = np.load(raw)
            n = len(cl)
            is_pam = np.zeros(n, bool); is_pam[self.pam] = True
            is_ppl = np.zeros(n, bool); is_ppl[self.ppl1] = True
            pam_syn = np.zeros(n); ppl_syn = np.zeros(n)
            pre, post, syn = z["da_pre"], z["da_post"], z["da_syn"].astype(float)
            np.add.at(pam_syn, post[is_pam[pre]], syn[is_pam[pre]])
            np.add.at(ppl_syn, post[is_ppl[pre]], syn[is_ppl[pre]])
            tot = pam_syn[mbon] + ppl_syn[mbon]
            frac_ppl = np.where(tot > 0, ppl_syn[mbon] / np.maximum(tot, 1), 0.5)
            self.approach = mbon[(tot > 0) & (frac_ppl >= 0.7)]   # punishment-taught
            self.avoid = mbon[(tot > 0) & (frac_ppl <= 0.3)]      # reward-taught
        self._edge_side = None     # per plastic edge: +1 approach, -1 avoid, 0 neither

        self.acute = 0.0
        self.learned = 0.0
        self.valence = 0.0
        self.trend = 0.0           # smoothed d(valence)/dt, per second
        self._smooth = 0.0
        self._last_t = None
        self._base = None          # thresholds before mood touched them
        self.opinion = {}
        self.opinion_raw = {}
        # Kenyon cell signature of each scent (nose.kc_signatures); with them,
        # the fly holds an opinion of each kind of thing and steers by it.
        self.signatures = signatures
        self._opinion_t = -1e12
        self._learned_version = -1      # see update(): the cache key for `learned`
        self._learned_t = -1e12

    # -- reading the brain -------------------------------------------------------
    def _context_valence(self, learning, kc_activity=None):
        """Learned valence of what a pattern of Kenyon cell activity encodes --
        by default the cells active right now (the eligibility trace)."""
        if self._edge_side is None:
            side = np.zeros(len(learning.mbon), dtype=np.int8)
            side[learning.mbon_pos[self.approach]] = 1
            side[learning.mbon_pos[self.avoid]] = -1
            self._edge_side = side[learning.edge_mbon]
        act = learning.elig if kc_activity is None else kc_activity
        e = np.tanh(act / 2.0)[learning.edge_kc]              # as the learning rule weighs it
        drive = e * learning.w0
        lost = drive * (1.0 - learning.m)
        app, avd = self._edge_side == 1, self._edge_side == -1
        pot_app, pot_avd = drive[app].sum(), drive[avd].sum()
        if pot_app <= 0 or pot_avd <= 0:
            return 0.0
        return float(lost[avd].sum() / pot_avd - lost[app].sum() / pot_app)

    def update(self, brain, t_ms, learning=None):
        r = brain.rate
        pam, ppl = float(r[self.pam].mean()), float(r[self.ppl1].mean())
        if self._last_t is None:
            self._last_t = t_ms
        dt = max(t_ms - self._last_t, 0.0)
        self._last_t = t_ms

        # Only phasic firing counts: excess above thresholds clear of the tonic
        # range, each normalised to one burst's worth.
        joy = max(0.0, pam - self.PAM_PHASIC_HZ) / self.PAM_BURST_HZS
        hurt = max(0.0, ppl - self.PPL1_PHASIC_HZ) / self.PPL1_BURST_HZS
        # Leaky integrator: d(acute)/dt = step * (joy - hurt) - acute / tau.
        self.acute += (self.BURST_STEP * (joy - hurt) * dt / 1000.0
                       - self.acute * dt / self.TAU_ACUTE_MS)
        self.acute = max(-3.0, min(3.0, self.acute))

        if learning is not None and len(self.approach) and len(self.avoid):
            # `learned` reads the eligibility trace and the weights, and BOTH
            # only move when the learning rule updates them (every
            # Learning.p.every steps). Recomputing it per frame walked ~100k
            # synapses three times for an answer that could not have changed:
            # it was 15% of the whole game's running time. Caching on the
            # version is exact, not an approximation.
            # ...and only a few times a second. The learning rule bumps its
            # version every 20 ms, so the version check alone still walked
            # ~100k synapses nearly every frame (2 ms, 10% of a frame's whole
            # budget). Mood's own constants are seconds long -- the acute
            # dopamine response above is what has to be quick, and it is.
            if (learning.version != self._learned_version
                    and t_ms - self._learned_t >= self.LEARNED_EVERY_MS):
                self.learned = self._context_valence(learning) / self.LEARNED_SCALE
                self._learned_version = learning.version
                self._learned_t = t_ms
            # Opinions move only when learning does; twice a second is plenty
            # and keeps three passes over ~100k synapses off the hot path.
            if self.signatures is not None and t_ms - self._opinion_t >= 500.0:
                self.opinions(learning, self.signatures)
                self._opinion_t = t_ms

        v = math.tanh(self.acute + self.learned)
        if dt > 0:
            prev = self._smooth
            self._smooth += (v - self._smooth) * min(1.0, dt / self.TAU_TREND_MS)
            self.trend = (self._smooth - prev) / (dt / 1000.0)
        self.valence = v
        return v

    @property
    def label(self):
        v = self.valence
        if v <= -0.5: return "scared"
        if v <= -0.15: return "uneasy"
        if v >= 0.5: return "happy"
        if v >= 0.15: return "content"
        return "calm"

    # -- what a feeling does (the design choice) ------------------------------------
    def apply(self, game):
        h, e = game.hands, game.eyes
        if self._base is None:
            self._base = {"start": h.start_thresh, "stop": h.stop_thresh}
        m = self.valence
        fear, joy = max(0.0, -m), max(0.0, m)
        worse = max(0.0, -self.trend)

        # Walking: distress lowers both ends of the band (bouts start easily --
        # get away); contentment widens it (whatever it is doing, it keeps doing).
        h.start_thresh = self._base["start"] * (1.0 - 0.5 * fear) * (1.0 + 0.3 * joy)
        h.stop_thresh = self._base["stop"] * (1.0 - 0.5 * fear) * (1.0 - 0.3 * joy)
        # Turning: spontaneous turns come easily when distressed or when things
        # are getting worse, and need a bigger swing when content -- stay the
        # course while it is going well.
        h.turn_scale = max(0.3, min(2.0, 1.0 + 0.6 * joy - 0.6 * fear - self.TREND_GAIN * worse))
        # A frightened fly looks for a way out.
        e.gain_exits *= 1.0 + fear
        # What it has learned about each kind of thing steers it toward or away
        # from that kind when seen (Hands._attraction) -- specific, where the
        # general mood above is not.
        if self.opinion:
            h.opinion = {"people": self.opinion.get("people", 0.0),
                         "exits": self.opinion.get("exits", 0.0)}

    # -- opinions: how it feels about each KIND of thing ---------------------------
    # Relative valence -> opinion. Set from the pre-registered scent experiment
    # (thing_learning_scent_alln.jsonl): a scent punished six times reaches
    # about -0.55, so that maps to a strong -0.9 and weaker associations stay
    # visibly weaker. An earlier 0.04, set when differences were ~0.07,
    # saturated everything at -1.0 and hid which thing was feared more.
    OPINION_SCALE = 0.35

    def opinions(self, learning, signatures):
        """-1 (fears) .. +1 (likes), per kind of thing, from the mushroom body.

        Asks the learned synapses what they make of each scent's Kenyon cell
        signature, whether or not that thing is near -- judged against what
        it makes of smelling NOTHING (the background Kenyon cell activity).
        Most of what a pain press teaches is a general souring (only ~1/9 was
        scent-specific in thing_learning_scent.jsonl); it hits the background
        too, so it cancels, and what is left says "people, specifically".
        (A first version judged each kind against the mean of the kinds,
        which coupled them: souring one made the others look liked.)
        """
        bg = signatures["background"]
        ref = self._context_valence(learning, bg)
        raw = {k: self._context_valence(learning, bg + sig) - ref
               for k, sig in signatures.items() if k != "background"}
        self.opinion = {k: math.tanh(v / self.OPINION_SCALE) for k, v in raw.items()}
        self.opinion_raw = raw
        return self.opinion

    def rebase(self):
        """Forget the captured thresholds -- call after a recalibration."""
        self._base = None
