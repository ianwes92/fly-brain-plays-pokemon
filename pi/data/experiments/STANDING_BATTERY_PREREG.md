# Standing per-run battery: untrained vs end-of-run trained (pre-registration)

Written 2026-10-13, BEFORE any battery data. Ian: the project must keep
collecting data and running experiments with nobody watching.

## What runs, and when
After every LAB run ends (the 14-day run clock, or a manual reset), the night
after at 00:30 ET: the naive-vs-trained experiment of naive_trained_PREREG.md
(including its amendments), UNCHANGED in design, measures, statistics and
decision rules, with one substitution:
- TRAINED arm = the run's end-of-run learned KC->MBON weights, from its archive
  (runs/<run>/firered.resume.ss.mb.npz, array "m"), loaded as float32 as saved.
Harness naive_trained.py (NT_TRAINED / NT_OUT only redirect paths) and scorer
naive_trained_score.py are used as frozen on 2026-10-09; no edits for the battery.

## Preflight
The trained file's fingerprint (sha1 of m rounded to 1e-4) is recorded when the
battery is planned and must match at 00:30, or the run is cancelled and that is
reported.

## Reporting
Fully automatic. The public result line is derived ONLY from the scorer's own
verdict lines (PRECONDITION / SIGNIFICANT / NULL FINDING / INCONCLUSIVE), and
always says "scored by the pre-registered script, not independently checked".
There is no blind recompute in this battery. Raw rows and score.txt are kept in
data/experiments/battery/run<N>/ and served by the relay for anyone to recheck.

## Not part of the battery
No new hand-designed experiments run without a person; the mirror test is not
repeated automatically.
