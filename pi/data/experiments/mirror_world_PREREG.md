# Mirrored-world test: pre-registration

Written 2026-10-05, BEFORE any data. Approved by Ian (relayed by obsfly, confirmed in
the flybrain session). Nothing below may change after the first row is logged; any
deviation is reported as a deviation.

## Question
The fly turns left only ~43-45% of the time (43.2% live; 44.7% in the 2026-10-03
pre-registered ethogram). Is that lean intrinsic to the simulated brain (FlyWire
connectivity), or produced by the world it sees and our readouts?

## Manipulation
Mirror the WORLD, not the brain. Every lateral sensory channel's drive is swapped
between its left and right neurons (slip, obstacle/LPLC1, people/LC10, exits/LPLC4),
and the turn the fly chooses is applied to the game reversed. The fly therefore lives
in a consistent mirror image of the same game. Nothing else changes: same save, same
maps, same calibration procedure (run inside each arm's own world), same parameters.
The nose and touch channels are not lateralized and are untouched.

## Measures (all in the FLY'S OWN frame: what the brain decided)
- PRIMARY: left share L = left turns / (left + right turns), per run.
- SECONDARY: the brain-level turn signal (left DN pair minus right DN pair), as the
  fraction of sampled frames where it is below zero. It is independent of every
  turn-decision rule.

## Decomposition (stated before data)
For each seed, with normal-world L_n and mirrored-world L_m (percent):
- intrinsic component  I = (L_n + L_m) / 2 - 50
  (a lean that survives mirroring belongs to the brain)
- environmental component E = (L_n - L_m) / 2
  (a lean that flips with the world belongs to the world or the readouts)
Pure intrinsic: L_m = L_n (E = 0). Pure environmental: L_m = 100 - L_n (I = 0).
Both can be present. The same decomposition is applied to the secondary measure.

## Design
12 seeds (0-11) x 2 arms (normal, mirror) = 24 runs, each 240 s of brain time from
the same ethogram start state as ethogram_now.py, no chat, no game rewards. Paired
by seed.

## Sample size
Per-seed left share in the 10-03 ethogram: 47.1, 37.6, 44.6, 52.3, 43.5, 44.7
(sd ~4.8 pp, ~260 turns per run). With 12 paired seeds, the SE of I or E is ~1.5-2 pp,
so a 5 pp component is detectable (t ~2.5-3.3) and the ~5-6 pp total lean can be
split between the two.

## Statistics and decision rules
One-sample t over the 12 seeds for I and for E; two-sided; t(11) critical = 2.201.
- "INTRINSIC component exists": I significantly below 0 (left share under 50 in both worlds).
- "ENVIRONMENTAL component exists": E significantly non-zero.
- Both may be reported. The size of each comes with a 95% CI.
- PRECONDITION: if the normal arm does not itself lean right (L_n not significantly
  below 50), there is nothing to decompose. That will be reported as "the bias did
  not reproduce in the harness".
- INCONCLUSIVE: if neither I nor E is significant AND either 95% CI is wider than
  +/-5 pp, the test could not resolve a component of the size in question.
  It will be reported as inconclusive, not as "no bias".
- A clean null (both CIs inside +/-2.5 pp) is reported as a finding.

## The known wall-branch rule (game.py, wall-ahead turn: `turn > 0`)
It is not a confound to correct for. It is one of the channels the test measures.
It converts the brain's turn-signal asymmetry into a turn side. If that asymmetry
is intrinsic it survives the mirror (counts toward I). If it is driven by what the
fly sees, it flips (counts toward E). The obstacle-avoidance turns use a median
split and are balanced by construction in both arms. They dilute both components
equally and so cannot create either. They are reported separately (stats "avoid",
"wall") so the dilution is visible. The secondary measure (turn-signal sign)
bypasses every decision rule.

## Run conditions
A separate simulated fly on save copies; the live stream is not touched. Cost: a
third of stream speed if run while live, or a stream-off window. Timing is Ian's
decision.

## Reporting
The result goes to Ian and obsfly whatever it is: positive, null, inconclusive, or
precondition failed. Scored by mirror_world_score.py, written before the data and
unchanged after.

## DEVIATION (found 2026-10-07, after scoring)
The FLYBRAIN_MIRROR switch was stored in `Hands.mirror`, a name already used by
the DNp35/DNp18 side readout that `read()` rewrites every frame. The heading
update tested that name, so in BOTH arms nearly every applied turn was reversed
(the readout is almost never exactly 0), and the mirror arm's intended turn
reversal never happened as designed. The only manipulation that differed between
arms was the Eyes lateral swap; neither arm was the world described above.
The primary result is void. The secondary (brain turn-signal sign) measures the
brain directly and is less affected, but was not collected under the registered
conditions. Fixed in game.py (`mirror_world`), with tests/test_turn_direction.py.

## RESULT (rerun 2026-10-08 00:30-03:15; blind recompute by obsfly matched)
Preflight 12/12 OK; 24/24 runs. Rows: mirror_world.jsonl (sha256 2fb93afc...528e).
- PRECONDITION met: normal arm 44.6% left (t=-4.37).
- PRIMARY: SHOWN, an INTRINSIC component. I = -4.45 pp (t=-6.87, 95% CI -5.88..-3.02).
  E = -0.95 pp (t=-1.04, CI -2.97..+1.07): not shown (not a clean null either).
- SECONDARY: I = -21.96 (t=-20.09); E = -1.88 (t=-2.15, just under 2.201).
Wording agreed with obsfly: the lean is intrinsic to the simulated fly (connectome +
our sensory/motor readouts), about -4.5 pp; connectome vs readout code not separated.
Note on this prereg: the Question groups "our readouts" with the world, but a readout
asymmetry fixed in the fly's own frame does not flip with the world, so it counts in
I, not E. Known code-level fly-frame asymmetry: wall-ahead turns choose a side by
the sign of the turn signal (FLYBRAIN_WALL_SIDE=zero), so they lean right (24 turns
of ~6,000). Attraction turns' side readouts were not logged per side.
