"""Where the simulated brain meets the Pi.

Two halves:

POPULATIONS  names the cell groups that matter for input and output, in
             FlyWire's own annotation vocabulary. This is the part worth
             getting right -- everything else is plumbing.

Sensor/Effector  are the contracts. A Sensor turns a physical reading into
             spikes in an afferent population; an Effector turns the firing
             rate of an efferent population into something that moves. Real
             hardware drops in by subclassing, with no change to the engine.

A note on the output side: this connectome is the *brain*. Leg motor neurons
live in the ventral nerve cord, which is not in this dataset. The brain's
actual motor output is the ~1300 descending neurons that project down the
neck connective, so those are what a servo layer should listen to -- the DNs
carry steering and gait *commands*, and the pattern generation that turns a
command into joint angles belongs downstream, in your own code.
"""
import math

import numpy as np

# --- afferent populations ------------------------------------------------
# Selectors are passed straight to Connectome.select().
SENSORY = {
    # Retina. R1-6 are the motion/luminance channel, R7/R8 colour and
    # polarised light. ~11k photoreceptors per brain.
    "eye":     {"cell_type": ["R1-6", "R7", "R8"]},
    # Ocelli: three simple eyes, fast luminance change, good for flicker.
    "ocelli":  {"cell_type": "ocellar_retinula_cell"},
    # Johnston's organ, the antennal ear. The A/B subtypes are the phasic
    # ones tuned to the vibration of near-field sound.
    "ear":     {"sub_class": "auditory"},
    # The remaining JO subtypes are tonic: steady antennal deflection, i.e.
    # wind and gravity rather than sound.
    "wind":    {"cell_type": ["JO-C*", "JO-D*", "JO-E*", "JO-F*"]},
    # Mechanosensory bristles -- the closest thing to a touch sensor.
    "touch":   {"sub_class": ["eye_bristle", "head_bristle"]},
    "taste":   {"class": "gustatory"},
    "smell":   {"class": "olfactory"},
    "heat":    {"class": "thermosensory"},
    "humid":   {"class": "hygrosensory"},
}

# --- efferent populations -------------------------------------------------
MOTOR = {
    "descending":  {"super_class": "descending"},
    "dn_left":     {"super_class": "descending", "side": "left"},
    "dn_right":    {"super_class": "descending", "side": "right"},
    # Named DNs with known behavioural roles. These live under the hemibrain
    # naming, not the systematic v783 cell_type.
    "steer_left":  {"super_class": "descending",
                    "hemibrain_type": ["DNa01*", "DNa02*"], "side": "left"},
    "steer_right": {"super_class": "descending",
                    "hemibrain_type": ["DNa01*", "DNa02*"], "side": "right"},
    "backward":    {"super_class": "descending", "hemibrain_type": "MDN*"},
    "stop":        {"super_class": "descending", "hemibrain_type": "DNp09*"},
}

# --- interesting internals to watch --------------------------------------
CENTRAL = {
    "central_complex": {"class": "CX"},     # heading, navigation
    "mushroom_body":   {"class": "Kenyon_Cell"},   # learning and memory
    "mbon":            {"class": "MBON"},          # its readout
    "optic_lobe":      {"super_class": "optic"},
    "antennal_lobe":   {"class": "ALPN"},
}

ALL_GROUPS = {**SENSORY, **MOTOR, **CENTRAL}


def resolve(cx, groups=None):
    """{name: index array} for the named groups (default: all of them)."""
    groups = groups or ALL_GROUPS
    return {name: cx.select(**sel) for name, sel in groups.items()}


# --- contracts ------------------------------------------------------------
class Sensor:
    """Turns a physical reading into afferent spikes.

    Subclass and implement read(). Return a stimulus in 0..1; the base class
    maps that onto a Poisson firing rate between `rest_hz` and `max_hz` and
    drives the population. Override apply() if you need per-neuron structure
    (a real eye should, since 11k photoreceptors are not one pixel).
    """

    #: key into SENSORY
    role = None

    def __init__(self, role=None, max_hz=150.0, rest_hz=5.0):
        if role:
            self.role = role
        self.max_hz = max_hz
        self.rest_hz = rest_hz
        self.idx = np.empty(0, dtype=np.int64)
        self.value = 0.0

    def attach(self, cx):
        self.idx = cx.select(**SENSORY[self.role])
        return self

    def read(self, t_ms):
        """Stimulus in 0..1. Override with a real hardware read."""
        raise NotImplementedError

    def apply(self, brain):
        self.value = min(max(self.read(brain.t_ms), 0.0), 1.0)
        hz = self.rest_hz + self.value * (self.max_hz - self.rest_hz)
        brain.drive(self.idx, hz)

    def close(self):
        pass

    def __len__(self):
        return len(self.idx)


class Effector:
    """Turns the firing rate of an efferent population into an action."""

    group = "descending"

    def __init__(self, group=None):
        if group:
            self.group = group
        self.idx = np.empty(0, dtype=np.int64)

    def attach(self, cx):
        self.idx = cx.select(**MOTOR[self.group])
        return self

    def rate(self, brain):
        return brain.population_rate(self.idx)

    def update(self, brain, dt_ms):
        raise NotImplementedError

    def close(self):
        pass

    def __len__(self):
        return len(self.idx)


# --- stand-in sensors until the hardware is wired -------------------------
class SyntheticSensor(Sensor):
    """A placeholder stimulus so the brain has something to chew on.

    `pattern` is one of:
      pulse  -- on for `duty` of every `period` ms (a light being switched)
      ramp   -- sawtooth
      sine   -- smooth oscillation
      off    -- silence
    """

    def __init__(self, role, pattern="pulse", period=2000.0, duty=0.25,
                 amplitude=1.0, phase=0.0, **kw):
        super().__init__(role, **kw)
        self.pattern = pattern
        self.period = period
        self.duty = duty
        self.amplitude = amplitude
        self.phase = phase

    def read(self, t_ms):
        u = ((t_ms / self.period) + self.phase) % 1.0
        if self.pattern == "pulse":
            v = 1.0 if u < self.duty else 0.0
        elif self.pattern == "ramp":
            v = u
        elif self.pattern == "sine":
            v = 0.5 * (1.0 - math.cos(2.0 * math.pi * u))
        else:
            v = 0.0
        return v * self.amplitude


class ManualSensor(Sensor):
    """Stimulus you set from outside -- handy for poking the brain by hand."""

    def __init__(self, role, **kw):
        super().__init__(role, **kw)
        self.level = 0.0

    def read(self, t_ms):
        return self.level
