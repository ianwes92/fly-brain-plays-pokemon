# Science

## Attribution: is it really the fly?

Every frame, step, bump, milestone and lesson is tagged **fly alone** or **human-assisted**. Something
counts as the fly's own only if no human key (chat, paid steering, `!menu`, the streamer's panel)
reached the game in the previous **5 seconds of brain time**. Chat presses are *added* to the fly's
own motor output for ~0.7 s; paid steering replaces it while it lasts. Science numbers use the fly's
own moves only, so viewers cannot skew them.

## Learning

`!pain` / `!pleasure` (and game events like catches) release dopamine onto the mushroom body's
KC→MBON synapses, changing the fly's learned opinion of whatever it is smelling (people, doors).
Opinions below −0.5 make it avoid. Lessons fade with a 2-hour time constant (brain time). Every
weight change is booked to its source (chat, the game, the stillness rule) in a ledger.

## Runs

Each **Lab Run lasts 14 days** and starts from a brand-new game and an untrained brain. Behaviour
settings only change *between* runs, so every run is a comparable dataset. A PDF report is posted
**weekly** and at the end of each run: progress, who helped, what it learned, the fly's thoughts and
a "what held it back" page with measured failure modes and candidate fixes for the next run.
(Runs 1–3 were test runs while the measurement was being built.)

## Experiments

Preregistered: the method, measures and decision rules are written before any data, results are
reported whatever they say. Raw rows are recomputed independently before a verdict is published.

| experiment | result |
|---|---|
| **Mirror world** (12 seeds, normal vs left-right mirrored world) | The fly turns left only 44.6% of the time (t = −4.37). The lean survives mirroring (intrinsic −4.5 pp, t = −6.87); the map's effect was not significant (CI −3.0 to +1.1 pp). The lean is intrinsic to the simulated fly — connectome plus our sensory/motor readout code, not yet separated. |
| **Untrained vs trained** (12 seeds, same brain with and without its saved training) | No detectable effect on attraction turns (100.4 vs 104.8, t = 0.76, 95% CI −8 to +17, inside the preregistered ±20% band). Surviving Holm correction: worse mood (−0.46) and more walking (+64 tiles, +2.4 pp walking time). Avoidance could not be tested: no saved opinion reached the −0.5 threshold. |

Recompute scripts are in [`../science/`](../science/); the Pi side holds the preregistrations,
harnesses and frozen scorers.

## Known limitations

* "Intrinsic" means the whole simulated fly: the connectome and our sensory/motor code are not yet
  separated.
* The trained state in the second experiment mixed early test-run training, game rewards and chat.
* Positions in the brain visual are each neuron's synapse centroid (FlyWire v783), not its soma.
