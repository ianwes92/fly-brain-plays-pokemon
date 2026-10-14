"""Leaky integrate-and-fire dynamics over the whole-brain connectome.

One neuron model, 139k copies of it, wired by the FlyWire graph. Each neuron
holds a membrane voltage that leaks toward rest, plus a synaptic charge that
decays and bleeds into the voltage:

    v   += (v_rest - v)*(1 - e^(-dt/tau_m)) + g*(1 - e^(-dt/tau_syn))
    g   *= e^(-dt/tau_syn)

Cross threshold -> emit a spike, clamp to reset, sit out the refractory
period, and add `mv_per_synapse * signed_synapse_count` to g of every
postsynaptic partner.

The whole thing is event driven. Only neurons that actually spiked have their
outgoing edges touched, and fly firing rates are low, so a step costs far less
than the 2.6M-edge graph suggests: the dense per-neuron voltage update over
139k float32s ends up dominating, not the synaptic scatter.

Parameter defaults follow the published whole-brain LIF treatment of this
connectome (Shiu et al., Nature 2024) and are all tunable -- they are a
starting point, not ground truth.
"""
import ctypes
import dataclasses

import numpy as np

from . import _ext


@dataclasses.dataclass
class Params:
    dt: float = 1.0             # ms per step
    v_rest: float = -52.0       # mV
    v_reset: float = -52.0      # mV
    v_threshold: float = -45.0  # mV
    tau_m: float = 20.0         # ms, membrane
    tau_syn: float = 5.0        # ms, synaptic current
    refractory: float = 2.2     # ms
    mv_per_synapse: float = 0.15
    # Transmitter predictions are noisy and fly GABA-A conductances are large,
    # so the raw sign assignment leaves the network unstable: a single kick to
    # 200 photoreceptors is otherwise enough to make it fire forever. Scaling
    # inhibition is the standard E/I balance knob. At 1.0 this network is
    # epileptiform; 2.0 gives activity that decays without input and still
    # carries an eye stimulus through to the descending neurons.
    inh_gain: float = 1.0
    # No single connection should fire a neuron by itself. Synapse counts run
    # from 1 to 2405 on this graph, so a linear mV-per-synapse rule hands the
    # tail connections a 360 mV kick when threshold is 7 mV away. Real
    # synapses saturate; this caps one connection's contribution.
    max_psp_mv: float = 5.0
    # Spike-frequency adaptation. Without it the network is bistable: any gain
    # high enough to carry a stimulus to the descending neurons is also high
    # enough to latch into self-sustaining activity that never stops. An
    # after-hyperpolarisation that accumulates with firing separates the two
    # -- a transient signal passes before adaptation builds, sustained
    # self-excitation shuts itself down.
    adapt_mv: float = 30.0
    tau_adapt: float = 150.0    # ms
    # Stand-in for drive the connectome cannot supply: ascending input from
    # the ventral nerve cord, neuromodulatory tone, channel noise. Zero gives
    # a brain that is exactly silent until something is fed to it.
    noise_mv: float = 0.0
    # Strength of APL's inhibition onto the Kenyon cells, as a multiple of what
    # its synapse counts give. The real APL is non-spiking: a graded, sustained
    # brake that keeps only ~5-10% of Kenyon cells answering any one odour, and
    # blocking it makes responses denser and odours harder to tell apart (Lin
    # et al. 2014). Modelled as one spiking LIF neuron with the uniform
    # mV-per-synapse rule it under-delivers: a scent here recruits ~20% of
    # Kenyon cells, and two scents share 85% of them. 1.0 = the uniform rule,
    # which every experiment before scent training used. The play window uses
    # the value calibrated to the physiological sparseness target
    # (data/experiments/kc_sparseness.log), chosen on sparseness alone, not on
    # how well training then works.
    apl_gain: float = 1.0
    # Antennal-lobe local neurons as inhibitory (Dale's principle + biology).
    # Transmitters here are machine predictions made synapse by synapse, which
    # left 152 of the 427 AL local neurons part-excitatory, part-inhibitory
    # (one neuron releases one fast transmitter) and 52 fully excitatory --
    # mostly neurons with no neuron-level call at all (150) or a weak one. Only
    # 2 are confidently cholinergic. Real adult AL local neurons are mostly
    # GABAergic or glutamatergic, and their lateral inhibition is what keeps
    # odours apart. As modelled, driving two glomeruli set off a dozen others
    # through these neurons, and that spill-over reached the Kenyon cells with
    # 2.3x the drive of the scent itself -- which is why two scents' Kenyon
    # responses correlated at 0.85 although their direct inputs barely overlap
    # (cosine 0.16). False keeps every earlier experiment exactly as it was.
    al_ln_inhibitory: bool = False
    tau_rate: float = 100.0     # ms, smoothing for the reported firing rates
    rate_every: float = 10.0    # ms between firing-rate refreshes
    use_c: bool = True          # fall back to numpy if False or no compiler


# The only antennal-lobe local neurons with a confident (>= 0.6) cholinergic
# prediction; they keep their excitatory sign under al_ln_inhibitory.
AL_LN_EXCITATORY = {
    720575940617441752,   # v2LN4 right, ACH 0.72
    720575940618021755,   # v2LN4 right, ACH 0.66
}


def al_ln_edges(cx):
    """Indices of every outgoing edge of an antennal-lobe local neuron, bar
    the two confidently excitatory ones."""
    cl = np.asarray(cx.labels["class"]).astype(str)
    ln = [i for i in np.flatnonzero(cl == "ALLN") if int(cx.root_id[i]) not in AL_LN_EXCITATORY]
    out = [np.arange(cx.indptr[i], cx.indptr[i + 1]) for i in ln]
    return np.concatenate(out) if out else np.array([], dtype=np.int64)


def apl_kc_edges(cx):
    """Indices of every APL -> Kenyon cell edge in the CSR arrays."""
    L = cx.labels
    name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"]).astype(str)
    apl = np.flatnonzero(np.char.startswith(name, "APL"))
    is_kc = np.asarray(L["class"]).astype(str) == "Kenyon_Cell"
    out = []
    for i in apl:
        k = np.arange(cx.indptr[i], cx.indptr[i + 1])
        out.append(k[is_kc[cx.indices[k]]])
    return np.concatenate(out) if out else np.array([], dtype=np.int64)


class Brain:
    def __init__(self, connectome, params=None, seed=0):
        self.cx = connectome
        self.p = params or Params()
        self.rng = np.random.default_rng(seed)
        n = self.cx.n

        # Synaptic counts -> millivolts, done once. float64 because that is
        # what np.bincount produces for weights anyway.
        self.w_mv = (self.cx.weights * self.p.mv_per_synapse).astype(np.float32)
        if self.p.inh_gain != 1.0:
            self.w_mv[self.w_mv < 0] *= self.p.inh_gain
        if self.p.max_psp_mv:
            cap = self.p.max_psp_mv
            np.clip(self.w_mv, -cap * self.p.inh_gain, cap, out=self.w_mv)
        if self.p.al_ln_inhibitory:
            k = al_ln_edges(self.cx)
            mag = np.abs(self.cx.weights[k]) * self.p.mv_per_synapse * self.p.inh_gain
            if self.p.max_psp_mv:
                mag = np.minimum(mag, self.p.max_psp_mv * self.p.inh_gain)
            self.w_mv[k] = -mag.astype(np.float32)
        if self.p.apl_gain != 1.0:
            # After the cap on purpose: the cap models one synapse saturating,
            # and APL's many small graded synapses onto each Kenyon cell do not.
            self.w_mv[apl_kc_edges(self.cx)] *= self.p.apl_gain

        self.v = np.full(n, self.p.v_rest, dtype=np.float32)
        self.g = np.zeros(n, dtype=np.float32)
        self.refrac = np.zeros(n, dtype=np.int8)
        self.rate = np.zeros(n, dtype=np.float32)   # Hz, smoothed
        self.spikes_total = np.zeros(n, dtype=np.int64)
        self.adapt = np.zeros(n, dtype=np.float32)  # after-hyperpolarisation
        self._tmp = np.empty(n, dtype=np.float32)   # scratch, avoids per-step allocs

        # Firing rates are a readout, not part of the dynamics, so they are
        # refreshed on a slower clock from spikes_total deltas. Keeping them
        # out of the step removes a whole pass over 139k floats.
        self._snap = np.zeros(n, dtype=np.int64)

        # Buffers the C kernel writes into.
        self._flag = np.zeros(n, dtype=np.uint8)
        self._spikes_out = np.empty(n, dtype=np.int32)
        loaded = _ext.load() if self.p.use_c else None
        self._fly_step, self._fly_rates = loaded if loaded else (None, None)
        self._empty_forced = np.empty(0, dtype=np.int64)

        self.steps = 0
        self.t_ms = 0.0
        self._forced = []
        # Optional learning rule, given each step's spikes (see learning.py).
        self.plasticity = None
        self._recompute()
        if self._fly_step is not None:
            self._bind_ptrs()

    def _bind_ptrs(self):
        """Cache the ctypes pointers handed to the C kernel.

        These were rebuilt every step, which is ~29 us of pure Python per
        step -- 6% of the budget, and worse than that under threading since
        it is all done holding the GIL. The arrays are only ever mutated in
        place, never reallocated, so their buffers do not move and the
        pointers stay valid for the life of the Brain.
        """
        self._p_v = _ext.ptr(self.v, ctypes.c_float)
        self._p_g = _ext.ptr(self.g, ctypes.c_float)
        self._p_adapt = _ext.ptr(self.adapt, ctypes.c_float)
        self._p_refrac = _ext.ptr(self.refrac, ctypes.c_int8)
        self._p_flag = _ext.ptr(self._flag, ctypes.c_uint8)
        self._p_total = _ext.ptr(self.spikes_total, ctypes.c_int64)
        self._p_out = _ext.ptr(self._spikes_out, ctypes.c_int32)
        self._rate_delta = np.zeros(self.cx.n, dtype=np.int32)  # spikes since the last refresh
        self._p_delta = _ext.ptr(self._rate_delta, ctypes.c_int32)
        self._p_rate = _ext.ptr(self.rate, ctypes.c_float)
        self._p_indptr = _ext.ptr(self.cx.indptr, ctypes.c_int64)
        self._p_indices = _ext.ptr(self.cx.indices, ctypes.c_int32)
        self._p_w = _ext.ptr(self.w_mv, ctypes.c_float)

    def _recompute(self):
        p = self.p
        self._decay_m = float(np.exp(-p.dt / p.tau_m))
        self._rest_term = p.v_rest * (1.0 - self._decay_m)
        self._decay_s = float(np.exp(-p.dt / p.tau_syn))
        self._deliver = 1.0 - self._decay_s
        self._ref_steps = max(int(round(p.refractory / p.dt)), 1)
        # refrac is int8 to keep the per-step memory traffic down; that caps
        # the refractory period at 127 steps (dt >= ~0.017 ms at 2.2 ms).
        if self._ref_steps > 127:
            raise ValueError(
                f"dt={p.dt} ms makes the refractory period {self._ref_steps} "
                f"steps, over the 127 the int8 counter holds; raise dt")
        self._decay_a = float(np.exp(-p.dt / p.tau_adapt))
        self._deliver_a = 1.0 - self._decay_a
        self._rate_steps = max(int(round(p.rate_every / p.dt)), 1)
        self._rate_window = self._rate_steps * p.dt / 1000.0
        self._rate_decay = float(np.exp(-self._rate_window * 1000.0 / p.tau_rate))

    # -- input -------------------------------------------------------------
    def force(self, idx):
        """Make these neurons spike on the next step, whatever their voltage.

        This is how sensory afferents get driven: a photoreceptor's job is to
        spike at a rate set by the light hitting it, not to be talked into it
        by upstream synapses it does not have.
        """
        # Appended to a list and joined once per step. Concatenating here
        # instead re-copied the whole array for every sensor, every step.
        # Duplicates are harmless -- the C kernel dedupes against its spike
        # flags, and the numpy path's mask update is idempotent.
        self._forced.append(np.asarray(idx, dtype=np.int64))

    def drive(self, idx, hz):
        """Poisson-drive a population at `hz`, applied to the next step.

        Sampling a uniform per neuron would mean 11k draws every step just for
        the retina. Per-step spike probability is tiny, so draw the *number*
        of spikers from a binomial and then pick only that many indices --
        O(spikes) instead of O(population). Duplicate picks are harmless;
        force() folds them together.
        """
        n = len(idx)
        if hz <= 0 or n == 0:
            return
        idx = np.asarray(idx, dtype=np.int64)
        p = hz * self.p.dt / 1000.0
        if p >= 1.0:
            self.force(idx)
            return
        k = int(self.rng.binomial(n, p))
        if k:
            self.force(idx[self.rng.integers(0, n, k)])

    def inject(self, idx, mv):
        """Add synaptic charge directly -- a nudge rather than a spike."""
        self.g[np.asarray(idx, dtype=np.int64)] += mv

    # -- the step ----------------------------------------------------------
    def _out_edges(self, idx):
        """Flat offsets into indices/weights for every edge leaving `idx`."""
        indptr = self.cx.indptr
        starts = indptr[idx]
        counts = indptr[idx + 1] - starts
        total = int(counts.sum())
        if total == 0:
            return None
        ends = np.cumsum(counts)
        out = np.arange(total, dtype=np.int64)
        out -= np.repeat(ends - counts, counts)
        out += np.repeat(starts, counts)
        return out

    @property
    def backend(self):
        return "C" if self._fly_step is not None else "numpy"

    def _refresh_rates(self):
        """Recompute smoothed firing rates from spike-count deltas.

        Five passes over 139k neurons for one multiply-add each: worth a
        millisecond of a game frame in numpy, so the C build fuses it (same
        arithmetic, same order). See fly_rates in _step.c.
        """
        if self._fly_rates is not None:
            self._fly_rates(self._p_delta, self._p_rate, self.cx.n,
                            self._rate_decay, self._rate_window)
            return
        delta = self.spikes_total - self._snap
        np.copyto(self._snap, self.spikes_total)
        self.rate *= self._rate_decay
        self.rate += (1.0 - self._rate_decay) * (delta / self._rate_window)

    def step(self):
        """Advance one dt. Returns the indices that spiked."""
        spiked = (self._step_c() if self._fly_step is not None
                  else self._step_np())
        if self.plasticity is not None:
            self.plasticity.observe(spiked)
        self.steps += 1
        self.t_ms += self.p.dt
        if self.steps % self._rate_steps == 0:
            self._refresh_rates()
        return spiked

    def _step_c(self):
        p = self.p
        f = self._forced
        if not f:
            forced = self._empty_forced
        elif len(f) == 1:
            forced = f[0]
        else:
            forced = np.concatenate(f)
        self._forced = []
        n = self._fly_step(
            self._p_v, self._p_g, self._p_adapt, self._p_refrac, self._p_flag,
            self._p_total, self._p_delta, self._p_out, self.cx.n,
            self._decay_m, self._rest_term,
            self._deliver, self._decay_s,
            self._deliver_a, self._decay_a, p.adapt_mv,
            p.v_reset, p.v_threshold, self._ref_steps,
            self._p_indptr, self._p_indices, self._p_w,
            _ext.ptr(forced, ctypes.c_int64), len(forced),
        )
        return self._spikes_out[:n]

    def _step_np(self):
        p = self.p
        v, g = self.v, self.g

        np.subtract(self.refrac, 1, out=self.refrac, where=self.refrac > 0)

        v *= self._decay_m
        v += self._rest_term
        np.multiply(g, self._deliver, out=self._tmp)
        v += self._tmp
        g *= self._decay_s

        if p.adapt_mv:
            np.multiply(self.adapt, self._deliver_a, out=self._tmp)
            v -= self._tmp
            self.adapt *= self._decay_a

        if p.noise_mv:
            v += self.rng.normal(0.0, p.noise_mv, size=len(v)).astype(np.float32)

        refractory = self.refrac > 0
        v[refractory] = p.v_reset

        fired = v >= p.v_threshold
        if self._forced:
            f = (self._forced[0] if len(self._forced) == 1
                 else np.concatenate(self._forced))
            fired[f] |= ~refractory[f]
            self._forced = []
        spiked = np.flatnonzero(fired)

        if spiked.size:
            v[spiked] = p.v_reset
            self.refrac[spiked] = self._ref_steps
            self.spikes_total[spiked] += 1
            if p.adapt_mv:
                self.adapt[spiked] += p.adapt_mv

            k = self._out_edges(spiked)
            if k is not None:
                np.add(g,
                       np.bincount(self.cx.indices[k], weights=self.w_mv[k],
                                   minlength=self.cx.n),
                       out=g, casting="unsafe")
        return spiked

    def run(self, ms):
        """Advance `ms` of simulated time, returning total spike count."""
        total = 0
        for _ in range(int(round(ms / self.p.dt))):
            total += len(self.step())
        return total

    # -- readout -----------------------------------------------------------
    def population_rate(self, idx):
        """Mean smoothed firing rate over a population, in Hz."""
        return float(self.rate[idx].mean()) if len(idx) else 0.0

    def reset(self):
        self.v[:] = self.p.v_rest
        self.g[:] = 0.0
        self.refrac[:] = 0
        self.adapt[:] = 0.0
        self.rate[:] = 0.0
        self.spikes_total[:] = 0
        self._snap[:] = 0
        self._flag[:] = 0
        self._forced = []
        self.steps = 0
        self.t_ms = 0.0
