# Naive vs trained fly: pre-registration

Written 2026-10-07, BEFORE any data. Approved by Ian (relayed by obsfly, and the
learning-fade decision it depends on was confirmed by Ian in the flybrain
session). Nothing below may change after the first row is logged; any deviation
is reported as a deviation.

## Question
Does the fly's accumulated training (game rewards + chat + run-1 carry,
inseparable) change its behaviour, compared with the same brain untrained?
(Amended before data, see below. The snapshot cannot say what CHAT taught it.)

## The trained state
`data/learning/pre-regime-20261007-164515.npz`: the mushroom body's KC->MBON
weights saved by the game immediately before continuous forgetting was switched
on (2026-10-07 16:45). 3611.9 units of depression over 8,695 of 54,218 plastic
synapses, ledger "before_ledger" (fingerprint at save: 29dee87f6776). It is
loaded as float32 exactly as saved.

## Arms
- NAIVE: all plastic weights at full strength (`learning.reset()`).
- TRAINED: weights loaded from the file above.
Applied after calibration (`Game.calibrate` runs first, identically in both
arms, with learning frozen), before play. With no pain or pleasure delivered,
the learning gate never opens, and the harness never reads the live
forgetting setting, so the weights stay fixed for the whole run.

Everything else is ethogram_now.run() unchanged: the same start state, the same
calibration, 240 s of brain time, no chat, no rewards, with mood, scents and
opinions attached as in play.

## Design
12 seeds (0-11) x 2 arms = 24 runs, paired by seed. Same reproducibility
settings as the mirror runs: OMP_NUM_THREADS=1, nice 19, alongside the live
stream, overnight.

## Manipulation check (precondition)
At the start of play, the trained arm's learned opinions (mood.opinions(),
people and exits) must differ from the naive arm's by more than 0.05 on at
least one kind, averaged over seeds. If not, the result is reported as "the
snapshot carries no behaviourally relevant training" and nothing else is
claimed.

## Measures
PRIMARY ("attraction reactivity"): attraction turns per run: toward_exit + toward_person +
away_from_exit + away_from_person (Hands.stats). Learned opinions act on
behaviour ONLY through these turns (Hands._attraction: thresholds scaled by
opinion, avoidance below -0.5) and through mood, so this is the measure that
training can most directly move.

SECONDARY (each reported, with Holm correction across the eight):
1. mean mood valence over sampled overworld frames
2. moves (tiles changed)
3. bumps per tile
4. walking fraction
5. left share of turns
6. attraction turns per opportunity (attraction turns / opportunity frames, below)
7. net approach to people: (toward_person - away_from_person) / (their sum)
8. net approach to exits: (toward_exit - away_from_exit) / (their sum)
For 7 and 8 a run with no such turns has no value; that seed's pair is dropped
from that measure only.

EXPOSURE: opportunity frames per run = overworld game frames in which a person
or an exit is in smelling range (nose level >= 0.5, the on-screen "in range"
criterion), also counted per kind. If the arms differ in exposure (paired t on
opportunity frames, |t| > 2.201), the report says so next to the primary.

## Statistics and decision rules
Per seed, d = trained - naive. One-sample two-sided t over the 12 seeds;
t(11) critical = 2.201; 95% CI reported.
- PRIMARY significant: "the accumulated training changes how often the fly
  turns toward or away from people and doors", with direction and size.
- PRIMARY not significant, 95% CI within +/-20% of the naive mean: reported as
  a finding: "no detectable effect of the training on attraction turns".
- Otherwise: INCONCLUSIVE, not "no effect".
Secondary results are descriptive unless they survive Holm at 0.05.
The score output also lists, for the primary, the per-arm means and every
seed's paired d.

## Amendments (2026-10-07, after obsfly's review, BEFORE any data)
1. Question reworded: the trained state is "before_ledger" (run-1 carry + game
   rewards + chat, inseparable). Part of it accrued during the 2026-10-05..07
   reversed-steering period, when bursts were paired with mis-applied actions. It
   is a real weight state, not a clean measure of what chat taught.
2. Exposure: opportunity frames are logged per run and per kind, and
   "attraction turns per opportunity" added as a secondary. The literal "signal
   above the _attraction gate" definition was not used: _attraction turns every
   time its gate is passed, so it would equal the turn count. Scent in range is
   the gate-independent measure of opportunity.
3. Direction: net approach for people and exits added as secondaries; the
   primary is renamed "attraction reactivity". Holm now runs across eight.
4. Output: rows carry the explicit counters obsfly's blind recompute needs, and
   the scorer prints per-arm primary means and the paired d per seed.

## Reporting
Whatever the result (positive, null, inconclusive, or precondition failed), it
goes to Ian and obsfly in the mirror-test structure. Scored by
naive_trained_score.py, written before the data and unchanged after. Raw rows go
to data/experiments/naive_trained.jsonl for obsfly's blind recompute.

Review: obsfly approved the amended prereg 2026-10-07 (before any data). Schedule held until the mirror-rerun report.

## RESULT (run 2026-10-09 00:30-03:14; blind recompute by obsfly from the raw file matched)
Preflight OK (fingerprint 29dee87f6776); 24/24 runs. naive_trained.jsonl sha256 e14e4b05...10b1.
- PRECONDITION met: trained opinions people -0.094, exits -0.410 vs naive 0.
- PRIMARY attraction reactivity: trained-naive +4.42 (SE 5.82), t=+0.76, 95% CI [-8.40, +17.23],
  inside +/-20% of the naive mean (100.4) -> "no detectable effect of the training on attraction turns".
- EXPOSURE: +494 frames, t=+1.41, no flag.
- SECONDARY (Holm, 8): mean valence -0.463 (survives), moves +63.9 (survives), walking fraction
  +0.024 (survives); bumps per tile -0.024, left share +0.023, attraction per opportunity ~0
  descriptive only. "bumps per tile" was computed from the row field wall_bumps/steps_moved.
- LIMITATION: net approach to people/exits (7, 8) is +1.0 in every run: no away turns are
  possible when no learned opinion reaches AVOID_OPINION (-0.5) and learning is frozen. Untestable
  with this snapshot; not substituted.
