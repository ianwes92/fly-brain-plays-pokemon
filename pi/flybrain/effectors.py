"""Physical outputs driven by descending-neuron activity.

Everything here is non-blocking. The simulation loop cannot afford to sit in
time.sleep(), so the buzzer and servos are state machines ticked once per
loop against the wall clock -- unlike buzzer.py, which is free to block
because beeping is all it does.

GPIO goes through lgpio on gpiochip15: gpiozero 2.0.1's LGPIOFactory
hardcodes chip 0/4, and this kernel puts the 40-pin header on chip 15
(pinctrl-rp1). Same reasoning, and same pin, as buzzer.py.
"""
import time

import numpy as np

from .ports import MOTOR, Effector

CHIP = 15
BUZZER_PIN = 17

try:
    import lgpio
except ImportError:      # importable on a machine with no GPIO, just inert
    lgpio = None


class _Gpio:
    """One shared handle on the chip, opened lazily."""

    _handle = None
    _claimed = set()

    @classmethod
    def claim_output(cls, pin):
        if lgpio is None:
            return False
        if cls._handle is None:
            cls._handle = lgpio.gpiochip_open(CHIP)
        if pin not in cls._claimed:
            lgpio.gpio_claim_output(cls._handle, pin, 0)
            cls._claimed.add(pin)
        return True

    @classmethod
    def write(cls, pin, value):
        if cls._handle is not None:
            lgpio.gpio_write(cls._handle, pin, value)

    @classmethod
    def release(cls, pin):
        if cls._handle is not None and pin in cls._claimed:
            lgpio.gpio_write(cls._handle, pin, 0)
            lgpio.gpio_free(cls._handle, pin)
            cls._claimed.discard(pin)


class BuzzerEffector(Effector):
    """Beeps when descending drive crosses a threshold.

    The piezo is a fixed-pitch active buzzer, so loudness and tone are not
    available -- the only channel is timing. Crossing `threshold` Hz of mean
    DN firing starts a beep; `refractory_s` keeps a noisy brain from turning
    it into a solid tone.
    """

    group = "descending"

    def __init__(self, group=None, pin=BUZZER_PIN, threshold=0.5,
                 beep_s=0.06, refractory_s=0.25):
        super().__init__(group)
        self.pin = pin
        self.threshold = threshold
        self.beep_s = beep_s
        self.refractory_s = refractory_s
        self.enabled = _Gpio.claim_output(pin)
        self._until = 0.0
        self._ready_at = 0.0
        self._on = False
        self.beeps = 0

    def update(self, brain, dt_ms):
        now = time.monotonic()
        hz = self.rate(brain)

        if self._on:
            if now >= self._until:
                _Gpio.write(self.pin, 0)
                self._on = False
                self._ready_at = now + self.refractory_s
        elif hz >= self.threshold and now >= self._ready_at:
            _Gpio.write(self.pin, 1)
            self._on = True
            self._until = now + self.beep_s
            self.beeps += 1
        return hz

    def close(self):
        if self._on:
            _Gpio.write(self.pin, 0)
            self._on = False
        _Gpio.release(self.pin)


class ServoBank(Effector):
    """Scaffold for six legs. Inert until real servos are wired.

    The brain does not produce joint angles. Its descending neurons issue
    commands -- go, turn, stop -- and the ventral nerve cord, which this
    connectome does not contain, turns those into stepping. So this stands in
    for the VNC: a tripod-gait oscillator whose frequency comes from overall
    DN drive and whose left/right asymmetry comes from the difference between
    the left and right DN populations.

    With dry_run=True (the default) it computes leg phases and writes nothing,
    so you can watch the gait in the dashboard before trusting it with
    hardware. Give it `pins` and dry_run=False once the servos exist; driving
    hobby servos properly wants hardware PWM or a PCA9685 rather than
    bit-banged GPIO, which is why the write path is deliberately left open.
    """

    group = "descending"

    # Tripod gait: legs L1,R2,L3 swing together, then R1,L2,R3.
    TRIPOD = np.array([0.0, 0.5, 0.0, 0.5, 0.0, 0.5])
    LEGS = ("L1", "L2", "L3", "R1", "R2", "R3")

    def __init__(self, pins=None, dry_run=True, max_hz=8.0, threshold=0.3):
        super().__init__("descending")
        self.pins = pins or []
        self.dry_run = dry_run or not self.pins
        self.max_hz = max_hz
        self.threshold = threshold
        self.phase = 0.0
        self.angles = np.zeros(6)
        self.speed = 0.0
        self.turn = 0.0
        self._left = self._right = None

    def attach(self, cx):
        super().attach(cx)
        self._left = cx.select(**MOTOR["dn_left"])
        self._right = cx.select(**MOTOR["dn_right"])
        return self

    def update(self, brain, dt_ms):
        drive = self.rate(brain)
        left = brain.population_rate(self._left)
        right = brain.population_rate(self._right)

        # Command signals: how fast, and which way.
        self.speed = max(drive - self.threshold, 0.0) / max(self.max_hz, 1e-6)
        self.speed = float(np.clip(self.speed, 0.0, 1.0))
        total = left + right
        self.turn = float((right - left) / total) if total > 1e-6 else 0.0

        # Step the gait oscillator.
        self.phase = (self.phase + self.speed * self.max_hz * dt_ms / 1000.0) % 1.0
        swing = np.sin(2 * np.pi * (self.phase + self.TRIPOD))

        # Outer legs of a turn take longer strides.
        gain = np.array([1 - self.turn] * 3 + [1 + self.turn] * 3)
        self.angles = 90.0 + 30.0 * swing * self.speed * gain

        if not self.dry_run:
            self.write(self.angles)
        return drive

    def write(self, angles):
        """Override with your PWM driver (PCA9685, pigpio hardware PWM, ...)."""
        raise NotImplementedError("wire up a real servo driver here")

    def close(self):
        pass


class Chirper:
    """The piezo as a voice for the moments that matter in training.

    An active buzzer has one pitch, so the only vocabulary is rhythm. Three
    words, each for an event a watcher cares about, and nothing else -- a
    buzzer that beeps on raw motor drive (BuzzerEffector) says nothing anyone
    can use:

      pain      one long harsh buzz            ______
      pleasure  two quick chirps               _ _
      learned   one tiny tick: the fly just turned away from something it
                fears, or toward something it likes, BECAUSE of what it was
                taught -- the sound of the training working.
      milestone three rising chirps: the game itself moved on -- a Pokemon
                caught, a level gained, a badge won.

    Non-blocking like the rest of this module: patterns are queued as
    (on_s, off_s) segments and tick() advances them against the wall clock,
    once per window frame. A new pain or pleasure pattern replaces whatever is
    playing; a learned tick waits its turn and is dropped if the queue is busy,
    so a run of avoidance turns cannot drown out the stimulus sounds.
    """

    PATTERNS = {
        "pain": [(0.40, 0.10)],
        "pleasure": [(0.05, 0.07), (0.05, 0.10)],
        "learned": [(0.012, 0.15)],
        # Something happened in the game worth looking up for: a catch, a
        # level, a badge. Three rising chirps, distinct from the two of
        # pleasure so they are not confused.
        "milestone": [(0.05, 0.05), (0.05, 0.05), (0.14, 0.10)],
    }

    def __init__(self, pin=BUZZER_PIN):
        self.pin = pin
        self.enabled = _Gpio.claim_output(pin)
        self._queue = []
        self._on = False
        self._next = 0.0
        self.counts = {k: 0 for k in self.PATTERNS}

    def say(self, word):
        if word == "learned":
            if self._queue or self._on:
                return
        else:
            self._queue.clear()
            if self._on:
                _Gpio.write(self.pin, 0)
                self._on = False
            self._next = 0.0
        self._queue.extend(self.PATTERNS[word])
        self.counts[word] += 1

    def tick(self):
        now = time.monotonic()
        if now < self._next:
            return
        if self._on:
            _Gpio.write(self.pin, 0)
            self._on = False
            off = self._queue.pop(0)[1] if self._queue else 0.0
            self._next = now + off
            return
        if self._queue:
            _Gpio.write(self.pin, 1)
            self._on = True
            self._next = now + self._queue[0][0]

    def close(self):
        if self._on:
            _Gpio.write(self.pin, 0)
            self._on = False
        self._queue.clear()
        _Gpio.release(self.pin)
