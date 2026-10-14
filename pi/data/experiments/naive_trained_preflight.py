"""Gate for the naive-vs-trained run (2026-10-08): before any row is logged,
the trained snapshot must be the one named in naive_trained_PREREG.md and the
mirrored world must be off. Exit 1 otherwise. Reads only; changes nothing."""
import hashlib, os, sys
import numpy as np

F = os.path.expanduser("~/flybrain/data/learning/pre-regime-20261007-164515.npz")
bad = []
m = np.load(F)["m"]
fp = hashlib.sha1(np.round(m.astype(np.float64), 4).astype(np.float32).tobytes()).hexdigest()[:12]
if fp != "29dee87f6776":
    bad.append(f"fingerprint {fp} != 29dee87f6776")
if m.shape != (54218,):
    bad.append(f"shape {m.shape}")
if abs(float((1 - m).sum()) - 3611.9) > 0.1:
    bad.append(f"depression {float((1 - m).sum()):.1f} != 3611.9")
if os.environ.get("FLYBRAIN_MIRROR"):
    bad.append("FLYBRAIN_MIRROR is set")
print("preflight:", "OK" if not bad else "; ".join(bad), f"(fingerprint {fp})")
sys.exit(1 if bad else 0)
