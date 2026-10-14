"""A schematic map of the fly brain.

FlyWire's public release does not ship neuron coordinates, but every synapse
carries the neuropil it sits in, so each neuron has a home region (computed at
build time). This places those regions on a frontal schematic of the brain:
optic lobes out at the sides, mushroom bodies and superior neuropils dorsal,
central complex on the midline, antennal lobes anterior, gnathal ganglion
ventral where the mouthparts are.

It is a schematic, NOT a projection of real anatomy. Relative arrangement is
right -- what is lateral is lateral, what is dorsal is dorsal, what is on the
midline is on the midline -- but the shapes are discs and the distances are
chosen to be legible rather than measured. Good for watching signals move
through the brain; not a reference for where anything actually is.

Coordinates are (x, y) with x from -1 (fly's left) to +1 (right) and y from
-1 (ventral) to +1 (dorsal). Radius is in the same units.
"""

# (x, y, radius) for one hemisphere; _L/_R pairs are mirrored automatically.
PAIRED = {
    # optic lobe, outermost first
    "LA":      (-0.90,  0.16, 0.070),   # lamina
    "ME":      (-0.755, 0.10, 0.115),   # medulla
    "AME":     (-0.62,  0.36, 0.030),   # accessory medulla
    "LO":      (-0.60, -0.07, 0.085),   # lobula
    "LOP":     (-0.585, 0.20, 0.060),   # lobula plate
    # mushroom body
    "MB_CA":   (-0.35,  0.60, 0.055),   # calyx
    "MB_PED":  (-0.27,  0.46, 0.035),   # peduncle
    "MB_VL":   (-0.19,  0.56, 0.035),   # vertical lobe
    "MB_ML":   (-0.30,  0.33, 0.040),   # medial lobe
    # superior / dorsal
    "SLP":     (-0.42,  0.78, 0.055),
    "SIP":     (-0.25,  0.70, 0.040),
    "SMP":     (-0.11,  0.80, 0.055),
    "LH":      (-0.50,  0.63, 0.050),
    # intermediate
    "AOTU":    (-0.36,  0.30, 0.035),
    "SCL":     (-0.35,  0.45, 0.040),
    "ICL":     (-0.24,  0.37, 0.035),
    "IB":      (-0.12,  0.50, 0.040),
    "ATL":     (-0.09,  0.39, 0.028),
    "CRE":     (-0.17,  0.26, 0.040),
    "BU":      (-0.23,  0.11, 0.022),
    "GA":      (-0.19,  0.03, 0.022),
    "LAL":     (-0.24, -0.06, 0.042),
    # posterior slope
    "SPS":     (-0.46,  0.42, 0.048),
    "IPS":     (-0.40,  0.52, 0.045),
    "PLP":     (-0.54,  0.30, 0.055),
    "PVLP":    (-0.56,  0.03, 0.055),
    "AVLP":    (-0.49, -0.12, 0.062),
    "WED":     (-0.44, -0.20, 0.045),
    # ventral
    "VES":     (-0.21, -0.21, 0.040),
    "EPA":     (-0.30, -0.19, 0.035),
    "GOR":     (-0.17, -0.29, 0.032),
    "ROB":     (-0.13, -0.16, 0.020),
    "RUB":     (-0.10, -0.10, 0.020),
    "AL":      (-0.29, -0.35, 0.060),   # antennal lobe
    "AMMC":    (-0.27, -0.47, 0.045),   # antennal mechanosensory centre
    "CAN":     (-0.37, -0.56, 0.030),
    "FLA":     (-0.11, -0.53, 0.028),
}

# midline structures, no left/right
UNPAIRED = {
    "PB":      ( 0.00,  0.43, 0.055),   # protocerebral bridge
    "FB":      ( 0.00,  0.19, 0.075),   # fan-shaped body
    "EB":      ( 0.00,  0.02, 0.055),   # ellipsoid body
    "NO":      ( 0.00, -0.11, 0.030),   # noduli
    "AB":      ( 0.00,  0.10, 0.022),   # asymmetrical body
    "SAD":     ( 0.00, -0.45, 0.050),
    "GNG":     ( 0.00, -0.64, 0.095),   # gnathal ganglion
    "PRW":     ( 0.00, -0.80, 0.040),
    "OCG":     ( 0.00,  0.92, 0.025),   # ocellar ganglion
}

# Functional grouping, used for colour.
GROUPS = {
    "optic":   ("LA", "ME", "AME", "LO", "LOP"),
    "mushroom": ("MB_CA", "MB_PED", "MB_VL", "MB_ML"),
    "central_complex": ("PB", "FB", "EB", "NO", "AB"),
    "olfactory": ("AL",),
    "mechanosensory": ("AMMC", "SAD", "GNG", "PRW", "CAN", "FLA"),
    "superior": ("SLP", "SIP", "SMP", "LH"),
}


def build():
    """{neuropil_name: (x, y, radius)} for every region in the dataset."""
    out = {}
    for base, (x, y, r) in PAIRED.items():
        out[f"{base}_L"] = (x, y, r)
        out[f"{base}_R"] = (-x, y, r)
    out.update(UNPAIRED)
    # Anything unplaced parks off to the side rather than piling up at centre.
    out["UNASGD"] = (0.86, -0.86, 0.05)
    out[""] = (0.86, -0.86, 0.05)
    return out


def group_of(neuropil):
    base = neuropil[:-2] if neuropil.endswith(("_L", "_R")) else neuropil
    for name, members in GROUPS.items():
        if base in members:
            return name
    return "other"
