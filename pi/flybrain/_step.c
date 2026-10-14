/* Fused LIF step.
 *
 * The numpy implementation is correct but makes about ten separate passes
 * over the 139k-element state arrays per step -- v, g, adapt, refrac, the
 * threshold mask, the bincount scratch. Each pass streams ~0.5 MB in and out
 * of a 2 MB L3, so the step is bound by memory traffic rather than by
 * arithmetic, and the arithmetic is trivial.
 *
 * This does the whole neuron update in one pass: read each neuron's state
 * once, do every update on it in registers, write it back once. Same maths,
 * same results, a quarter of the traffic.
 *
 * The neuron loop is then spread over the cores. One core cannot saturate
 * this machine's memory bandwidth, and the loop is pure streaming, so it
 * scales. Each thread owns a contiguous slice of neurons and writes its
 * spikes into its own slice of the output buffer, which are compacted after
 * the join -- no atomics, no shared counter.
 *
 * The synaptic scatter stays serial: it has write conflicts that would need
 * atomics, and it touches only the edges of neurons that actually fired,
 * which is a few thousand updates against 139k neurons of streaming.
 *
 * Build: see _ext.py (compiled on first import, cached next to this file).
 */
#include <stdint.h>
#include <stddef.h>
#ifdef _OPENMP
#include <omp.h>
#endif

int fly_step(
    float   *v,            /* membrane potential, mV            */
    float   *g,            /* synaptic charge, mV               */
    float   *adapt,        /* after-hyperpolarisation, mV       */
    int8_t  *refrac,       /* refractory countdown, steps       */
    uint8_t *flag,         /* scratch, all zero on entry+exit   */
    int64_t *spikes_total,
    int32_t *delta,        /* spikes since the last rate refresh */
    int32_t *spikes_out,   /* caller-allocated, length n        */
    int      n,
    float decay_m, float rest_term,
    float deliver_s, float decay_s,
    float deliver_a, float decay_a, float adapt_mv,
    float v_reset, float v_threshold, int8_t ref_steps,
    const int64_t *indptr,       /* CSR over presynaptic neuron */
    const int32_t *indices,
    const float   *w_mv,
    const int64_t *forced, int n_forced)
{
    int ns = 0;

#ifdef _OPENMP
    int nthreads = omp_get_max_threads();
    if (nthreads > 8) nthreads = 8;
#else
    int nthreads = 1;
#endif
    int chunk = (n + nthreads - 1) / nthreads;
    int counts[8] = {0};

#ifdef _OPENMP
#pragma omp parallel num_threads(nthreads)
#endif
    {
#ifdef _OPENMP
        int t = omp_get_thread_num();
#else
        int t = 0;
#endif
        int lo = t * chunk;
        int hi = lo + chunk;
        if (hi > n) hi = n;
        int32_t *out = spikes_out + lo;   /* at most (hi-lo) spikes fit */
        int c = 0;

        for (int i = lo; i < hi; i++) {
            float   vi = v[i];
            float   gi = g[i];
            float   ai = adapt[i];
            int8_t  r  = refrac[i];

            if (r > 0) r--;

            vi = vi * decay_m + rest_term + gi * deliver_s - ai * deliver_a;
            gi *= decay_s;
            ai *= decay_a;

            /* Refractory neurons are pinned at reset rather than integrating,
             * so they cannot reach threshold below. */
            if (r > 0) vi = v_reset;

            if (vi >= v_threshold) {
                vi  = v_reset;
                r   = ref_steps;
                ai += adapt_mv;
                spikes_total[i]++;
                delta[i]++;
                out[c++] = i;
                flag[i] = 1;
            }

            v[i]      = vi;
            g[i]      = gi;
            adapt[i]  = ai;
            refrac[i] = r;
        }
        counts[t] = c;
    }

    /* Compact the per-thread regions into one dense list. */
    for (int t = 0; t < nthreads; t++) {
        int lo = t * chunk;
        if (lo >= n) break;
        if (ns != lo)
            for (int j = 0; j < counts[t]; j++)
                spikes_out[ns + j] = spikes_out[lo + j];
        ns += counts[t];
    }

    /* Forced spikes: sensory afferents fire because the world made them, not
     * because their membrane got there. Still bound by the refractory period,
     * and deduplicated against neurons that already spiked this step. */
    for (int j = 0; j < n_forced; j++) {
        int i = (int)forced[j];
        if (flag[i] || refrac[i] > 0) continue;
        v[i]      = v_reset;
        refrac[i] = ref_steps;
        adapt[i] += adapt_mv;
        spikes_total[i]++;
        delta[i]++;
        spikes_out[ns++] = i;
        flag[i] = 1;
    }

    /* Scatter this step's spikes into postsynaptic charge. Event driven:
     * only edges leaving a neuron that actually fired are touched. */
    for (int s = 0; s < ns; s++) {
        int i = spikes_out[s];
        int64_t a = indptr[i], b = indptr[i + 1];
        for (int64_t k = a; k < b; k++)
            g[indices[k]] += w_mv[k];
    }

    for (int s = 0; s < ns; s++) flag[spikes_out[s]] = 0;
    return ns;
}

/* Smoothed firing rates from spike-count deltas.
 *
 * The numpy version made five passes over the 139k state arrays -- subtract,
 * copy, scale, divide, add -- every ten steps. At a game frame's worth of
 * thinking that was 5 ms of the 16.7 ms a frame is allowed, a quarter of the
 * budget, to do one multiply-add per neuron. One fused pass instead, reading
 * a per-neuron int32 counter the step already keeps rather than differencing
 * two int64 arrays -- half the memory traffic -- in numpy's own order.
 */
void fly_rates(int32_t *delta, float *rate, int n, double decay, double window)
{
    const double one_minus = 1.0 - decay;
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
    for (int i = 0; i < n; i++) {
        int32_t d = delta[i];
        if (d == 0 && rate[i] == 0.0f) continue;     /* silent, and already zero */
        delta[i] = 0;
        /* numpy's own order: scale in float32, then add in double. */
        /* numpy does `rate *= decay` in float32 (in-place on a float32
         * array), then `rate += (1-decay) * (delta/window)` in float64.
         * Matching that exactly means the C and numpy paths cannot drift. */
        rate[i] = (float)((double)(rate[i] * (float)decay)
                          + one_minus * ((double)d / window));
    }
}

/* ---------------------------------------------------------------------
 * Visualisation helpers.
 *
 * These were numpy one-liners and were the most expensive part of drawing a
 * frame -- not because the arithmetic is hard but because each built several
 * megabytes of temporaries per frame (a fancy-index, a broadcast add, a
 * float64 bincount over 456k bins) and held the interpreter lock while doing
 * it, which stalled the simulation thread. In C they allocate nothing and
 * release the GIL, so drawing genuinely overlaps with simulating.
 * ------------------------------------------------------------------- */

/* Add each spiking neuron's colour into the phosphor buffer. */
void fly_scatter(float *phos, const int32_t *pix3, const float *color3,
                 const int32_t *spikes, int nspikes, float gain)
{
    for (int i = 0; i < nspikes; i++) {
        int s = spikes[i];
        int base = pix3[s];
        const float *c = color3 + (size_t)s * 3;
        phos[base]     += c[0] * gain;
        phos[base + 1] += c[1] * gain;
        phos[base + 2] += c[2] * gain;
    }
}

/* Fade the phosphor and tone-map it into the 8-bit frame, in one pass.
 * sqrt compresses the bright end so busy regions keep their hue instead of
 * clipping to white. */
void fly_fade_tone(float *phos, uint8_t *out, int n, float fade, float scale)
{
    for (int i = 0; i < n; i++) {
        float p = phos[i] * fade;
        phos[i] = p;
        float t = __builtin_sqrtf(p) * scale;
        out[i] = t >= 255.0f ? 255 : (uint8_t)t;
    }
}

/* Cap the OpenMP team size. The viewer leaves a core free for drawing:
 * with all four cores inside the kernel, the render thread has to preempt
 * one of them and both sides get slower. */
void fly_set_threads(int n)
{
#ifdef _OPENMP
    if (n > 0) omp_set_num_threads(n);
#else
    (void)n;
#endif
}
