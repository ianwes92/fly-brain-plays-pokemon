"""Dopamine-gated learning in the mushroom body.

The fly's associative memory, implemented the way the fly is thought to do it.
Kenyon cells (KCs) represent what the fly is currently sensing, sparsely.
They synapse onto mushroom body output neurons (MBONs), which bias behaviour
toward approach or avoidance. Dopaminergic neurons (DANs) carry reward (PAM
cluster) and punishment (PPL1 cluster) and innervate the MBONs compartment by
compartment. When a compartment's dopamine arrives shortly after KCs fired,
those KCs' synapses onto that compartment's MBON are depressed. So the
situation that was paired with punishment stops driving the MBONs that
punishment dopamine targets, and behaviour shifts.

Only KC->MBON synapses are plastic. Nothing else in the connectome changes.

Where the numbers come from:

  - Which dopamine neurons teach which MBON: their actual DAN->MBON dopamine
    synapses in FlyWire (learning_raw.npz, from `flybrain learn-extract`).
    That map recovers the known compartments without being told them -- the
    strongest punishment connection is PPL101 -> MBON11, which is PPL1-g1pedc
    onto MBON-g1pedc>a/b, the canonical aversive learning pair.
  - Depression needs coincidence: a KC eligibility trace (tau ~1 s) times
    dopamine above its own slow baseline. Using the excess over baseline
    matters here: PPL1 fires tonically at ~4.5 Hz just from visual input, so
    raw dopamine would teach "everything is bad" all the time.
  - Learning rate, floor and recovery are modelling choices, not measurements;
    they are set so a handful of pairings produces a clear change, and they
    are all parameters.
"""
import dataclasses
import hashlib
import json
import os

import numpy as np

from .connectome import DATA, LEARNING_RAW


@dataclasses.dataclass
class LearningParams:
    eta: float = 0.35             # per second, at full eligibility and dopamine
    tau_elig: float = 1000.0      # ms, how long a KC stays eligible after firing
    tau_da: float = 300.0         # ms, dopamine signal smoothing
    tau_base: float = 20000.0     # ms, slow dopamine baseline (tonic level)
    da_scale: float = 30.0        # Hz of phasic dopamine that counts as "full"
    da_margin: float = 1.0        # Hz above baseline before anything is learned
    floor: float = 0.05           # synapses depress to at most this fraction
    tau_recover: float = 600000.0 # ms, slow drift back toward full strength
    every: int = 20               # steps between weight updates


class MushroomBodyLearning:
    def __init__(self, brain, params=None, raw=LEARNING_RAW):
        if not os.path.exists(raw):
            raise SystemExit(f"missing {raw}; run `flybrain learn-extract`")
        self.brain = brain
        self.p = params or LearningParams()
        cx = brain.cx
        cl = cx.labels["class"]

        self.kc = np.flatnonzero(cl == "Kenyon_Cell")
        self.mbon = np.flatnonzero(cl == "MBON")
        self.dan = np.flatnonzero(cl == "DAN")
        self.kc_pos = np.full(cx.n, -1, dtype=np.int32)
        self.kc_pos[self.kc] = np.arange(len(self.kc))
        self.mbon_pos = np.full(cx.n, -1, dtype=np.int32)
        self.mbon_pos[self.mbon] = np.arange(len(self.mbon))
        self.dan_pos = np.full(cx.n, -1, dtype=np.int32)
        self.dan_pos[self.dan] = np.arange(len(self.dan))

        # Plastic edges: every KC -> MBON edge in the running graph.
        parts = []
        for i in self.kc:
            a, b = cx.indptr[i], cx.indptr[i + 1]
            if b > a:
                k = np.arange(a, b)
                parts.append(k[self.mbon_pos[cx.indices[k]] >= 0])
        self.edges = np.concatenate(parts).astype(np.int64)
        self.edge_kc = self.kc_pos[np.repeat(
            np.arange(cx.n), np.diff(cx.indptr))[self.edges]]
        self.edge_mbon = self.mbon_pos[cx.indices[self.edges]]
        self.w0 = brain.w_mv[self.edges].copy()
        self.m = np.ones(len(self.edges), dtype=np.float32)

        # Teaching map: dopamine synapses from each DAN onto each MBON,
        # normalised per MBON so its dopamine level is a weighted mean DAN
        # rate in Hz.
        z = np.load(raw)
        D = np.zeros((len(self.mbon), len(self.dan)), dtype=np.float32)
        to_mbon = self.mbon_pos[z["da_post"]] >= 0
        np.add.at(D, (self.mbon_pos[z["da_post"][to_mbon]],
                      self.dan_pos[z["da_pre"][to_mbon]]),
                  z["da_syn"][to_mbon].astype(np.float32))
        row = D.sum(axis=1, keepdims=True)
        self.D = D / np.maximum(row, 1.0)
        self.da_synapses = row.ravel()

        self.elig = np.zeros(len(self.kc), dtype=np.float32)
        self.dan_trace = np.zeros(len(self.dan), dtype=np.float32)
        self.da = np.zeros(len(self.mbon), dtype=np.float32)
        self.base = None
        self.teach = np.zeros(len(self.mbon), dtype=np.float32)
        self._kc_cnt = np.zeros(len(self.kc), dtype=np.float32)
        self._dan_cnt = np.zeros(len(self.dan), dtype=np.float32)
        self._steps = 0
        self.version = 0        # bumped whenever the trace or the weights move
        self.frozen = False
        # Optional gate: a callable returning True when teaching is allowed.
        # With None (the default) any dopamine above baseline teaches, which is
        # how the conditioning experiments ran. In play that let ordinary game
        # visuals teach: 30 s without a single pleasure or pain event weakened
        # 5,953 synapses by up to 41%. The game sets a gate so only delivered
        # pleasure and pain do.
        self.gate = None
        # WHO TAUGHT IT (obsfly/Ian, 2026-10-07: audience training must not
        # pass as innate behaviour). `credit` is set by the game to the source
        # of the burst being delivered ("human", "game", "rule"); every weight
        # change in a teaching window is booked to it. "outstanding" is how
        # much of the CURRENT depression each source accounts for: recovery
        # shrinks every source's share in proportion, since forgetting does
        # not know who taught what. Depression from before this ledger existed
        # is "before_ledger". Units: summed lost fraction of synaptic strength
        # (sum of 1 - m over plastic edges).
        self.credit = "unknown"
        # Continuous forgetting (Ian, 2026-10-07): with the gate closed the rule
        # used to do nothing at all, recovery included, so learned depression
        # was effectively permanent in play. With recover_always the synapses
        # also drift back toward full strength between bursts, at
        # p.tau_recover. Off by default: experiments run exactly as before.
        self.recover_always = False
        self._idle_ms = 0.0
        self.ledger = {"outstanding": {}, "depressed": {}, "recovered": 0.0}

    def attach(self):
        self.brain.plasticity = self
        return self

    def detach(self):
        if self.brain.plasticity is self:
            self.brain.plasticity = None

    # -- per step --------------------------------------------------------
    def observe(self, spiked):
        """Called from Brain.step with that step's spikes (consumed now:
        the C backend reuses the buffer)."""
        if len(spiked):
            k = self.kc_pos[spiked]
            self._kc_cnt[k[k >= 0]] += 1.0
            d = self.dan_pos[spiked]
            self._dan_cnt[d[d >= 0]] += 1.0
        self._steps += 1
        if self._steps % self.p.every == 0:
            self._update()

    def _update(self):
        # Anything derived from the trace or the weights only changes here, so
        # readers can cache on `version` instead of recomputing every frame.
        self.version += 1
        p, dt = self.p, self.p.every * self.brain.p.dt
        self.elig *= np.exp(-dt / p.tau_elig)
        self.elig += self._kc_cnt
        self.dan_trace *= np.exp(-dt / p.tau_da)
        self.dan_trace += self._dan_cnt
        self._kc_cnt[:] = 0.0
        self._dan_cnt[:] = 0.0

        # Weighted DAN rate per MBON compartment, in Hz.
        self.da = self.D @ (self.dan_trace / (p.tau_da / 1000.0))
        if self.base is None:
            self.base = self.da.copy()
        self.base += (self.da - self.base) * (dt / p.tau_base)
        self.teach = np.clip((self.da - self.base - p.da_margin) / p.da_scale, 0.0, 1.0)

        if self.frozen:
            return
        if self.gate is not None and not self.gate():
            if self.recover_always:
                self._recover_idle(dt)
            return
        e = np.tanh(self.elig / 2.0)
        g = e[self.edge_kc] * self.teach[self.edge_mbon]
        m = self.m
        before = float(m.sum())
        m -= p.eta * g * m * (dt / 1000.0)
        mid = float(m.sum())
        m += (1.0 - m) * (dt / p.tau_recover)
        np.clip(m, p.floor, 1.0, out=m)
        self._book(max(0.0, before - mid), max(0.0, float(m.sum()) - mid))
        # In place: the C kernel holds a pointer to w_mv, so the array object
        # must not be replaced.
        self.brain.w_mv[self.edges] = self.w0 * m

    def _recover_idle(self, dt):
        """Recovery outside teaching windows, applied in closed form once a
        brain-second (exact for pure exponential recovery, and ~1000x
        cheaper than touching every synapse every update)."""
        self._idle_ms += dt
        if self._idle_ms < 1000.0:
            return
        m = self.m
        before = float(m.sum())
        m += (1.0 - m) * float(-np.expm1(-self._idle_ms / self.p.tau_recover))
        np.clip(m, self.p.floor, 1.0, out=m)
        self._idle_ms = 0.0
        self.brain.w_mv[self.edges] = self.w0 * m
        self.version += 1
        self._book(0.0, max(0.0, float(m.sum()) - before))

    def note_regime(self, change, when):
        """Record a change of learning rules in the ledger itself, so the
        learned state always carries the history of how it was made."""
        reg = self.ledger.setdefault("regimes", [])
        if not reg or reg[-1].get("change") != change:
            reg.append({"change": change, "t": when})
            return True
        return False

    def _book(self, dep, rec):
        """Book one update's depression to the current credit, and spread its
        recovery over all sources in proportion to what each still holds.
        (Clipping at the floor is folded into recovery; it is tiny.)"""
        led, src = self.ledger, self.credit or "unknown"
        if dep:
            led["depressed"][src] = led["depressed"].get(src, 0.0) + dep
            led["outstanding"][src] = led["outstanding"].get(src, 0.0) + dep
        if rec:
            led["recovered"] += rec
            out = led["outstanding"]
            tot = sum(v for v in out.values())
            if tot > 0:
                keep = max(0.0, 1.0 - rec / tot)
                for k in out:
                    out[k] *= keep

    def depressed_now(self):
        return float((1.0 - self.m).sum())

    def fingerprint(self):
        """Short hash of the learned state (to 1e-4): equal fingerprints mean
        the same trained fly, so analyses can group by it."""
        q = np.round(self.m.astype(np.float64), 4).astype(np.float32)
        return hashlib.sha1(q.tobytes()).hexdigest()[:12]

    def _ledger_json(self):
        return np.frombuffer(json.dumps(self.ledger).encode(), dtype=np.uint8)

    def _ledger_from(self, z):
        if "ledger" in getattr(z, "files", ()):
            try:
                self.ledger = json.loads(bytes(z["ledger"]).decode())
                return
            except ValueError:
                pass
        d = self.depressed_now()
        self.ledger = {"outstanding": {"before_ledger": d} if d > 0 else {},
                       "depressed": {}, "recovered": 0.0}

    # -- inspection and persistence ------------------------------------------
    def mbon_strength(self):
        """Mean remaining strength of KC input, per MBON (1 = untouched)."""
        s = np.zeros(len(self.mbon)); c = np.zeros(len(self.mbon))
        np.add.at(s, self.edge_mbon, self.m)
        np.add.at(c, self.edge_mbon, 1.0)
        return s / np.maximum(c, 1.0)

    def reset(self):
        self.m[:] = 1.0
        self.brain.w_mv[self.edges] = self.w0
        self.elig[:] = 0.0
        self.dan_trace[:] = 0.0
        self.base = None
        self.ledger = {"outstanding": {}, "depressed": {}, "recovered": 0.0}

    def save(self, path):
        np.savez_compressed(path, m=self.m, edges=self.edges,
                            n_edges=np.int64(self.brain.cx.n_edges), ledger=self._ledger_json())

    def load(self, path):
        z = np.load(path)
        if int(z["n_edges"]) != self.brain.cx.n_edges or not np.array_equal(z["edges"], self.edges):
            raise ValueError(f"{path} was saved against a different connectome build")
        self.m[:] = z["m"]
        self.brain.w_mv[self.edges] = self.w0 * self.m
        self._ledger_from(z)


def extract(out=LEARNING_RAW, log=print):
    """Pull the learning circuit out of the raw FlyWire connections.

    Two things the main build leaves out or thins: every Kenyon cell -> MBON
    connection regardless of size, and every dopamine synapse from a DAN onto
    an MBON or KC (dopamine is not fast excitation or inhibition, so it is
    not in the voltage graph at all). Scans all 16.8M rows once, ~1 minute.
    Rebuild the connectome afterwards so the small KC -> MBON edges are in.
    """
    import gzip
    from .connectome import Connectome
    cx = Connectome()
    cl = cx.labels["class"]
    kc, mbon, dan = cl == "Kenyon_Cell", cl == "MBON", cl == "DAN"
    idx_of = {int(r): i for i, r in enumerate(cx.root_id)}
    kcm = ([], [], [], [])
    da = ([], [], [])
    log("scanning connections for the mushroom body learning circuit ...")
    with gzip.open(os.path.join(DATA, "connections_no_threshold.csv.gz"), "rt") as f:
        next(f)
        for line in f:
            a, b, _npil, syn, nt = line.rstrip("\r\n").split(",")
            ia = idx_of.get(int(a))
            if ia is None:
                continue
            if kc[ia]:
                ib = idx_of.get(int(b))
                if ib is not None and mbon[ib]:
                    kcm[0].append(ia); kcm[1].append(ib); kcm[2].append(int(syn)); kcm[3].append(nt)
            elif dan[ia] and nt == "DA":
                ib = idx_of.get(int(b))
                if ib is not None and (mbon[ib] or kc[ib]):
                    da[0].append(ia); da[1].append(ib); da[2].append(int(syn))
    np.savez_compressed(
        out, kcm_pre=np.array(kcm[0], np.int32), kcm_post=np.array(kcm[1], np.int32),
        kcm_syn=np.array(kcm[2], np.int32), kcm_nt=np.array(kcm[3]),
        da_pre=np.array(da[0], np.int32), da_post=np.array(da[1], np.int32),
        da_syn=np.array(da[2], np.int32))
    log(f"  {len(kcm[0]):,} KC->MBON connections, {len(da[0]):,} dopamine connections -> {out}")
    log("now rebuild: flybrain build")
