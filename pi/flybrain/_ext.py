"""Loads the fused C step kernel, compiling it on first use.

Falls back to None if there is no compiler or the build fails; engine.py then
uses the pure-numpy path, which is slower but produces the same results.
"""
import ctypes
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "_step.c")
_SO = os.path.join(_HERE, "_step.so")

FLAGS = ["-O3", "-march=native", "-ffast-math", "-fopenmp", "-shared", "-fPIC"]


def _build(verbose=False):
    if not os.path.exists(_SRC):
        return False
    if os.path.exists(_SO) and os.path.getmtime(_SO) >= os.path.getmtime(_SRC):
        return True
    try:
        subprocess.run(["gcc", *FLAGS, "-o", _SO, _SRC],
                       check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError) as exc:
        if verbose:
            err = getattr(exc, "stderr", b"")
            print(f"flybrain: C kernel build failed ({exc}); "
                  f"{err.decode(errors='replace')[:400]}", file=sys.stderr)
        return False


def load(verbose=False):
    """Return the ctypes-bound fly_step, or None to use the numpy path."""
    if not _build(verbose):
        return None
    try:
        lib = ctypes.CDLL(_SO)
    except OSError:
        return None

    f32 = ctypes.POINTER(ctypes.c_float)
    i8 = ctypes.POINTER(ctypes.c_int8)
    u8 = ctypes.POINTER(ctypes.c_uint8)
    i32 = ctypes.POINTER(ctypes.c_int32)
    i64 = ctypes.POINTER(ctypes.c_int64)
    fl = ctypes.c_float

    if hasattr(lib, "fly_scatter"):
        lib.fly_scatter.restype = None
        lib.fly_scatter.argtypes = [f32, i32, f32, i32, ctypes.c_int, fl]
        lib.fly_fade_tone.restype = None
        lib.fly_fade_tone.argtypes = [f32, u8, ctypes.c_int, fl, fl]

    if hasattr(lib, "fly_rates"):
        lib.fly_rates.restype = None
        lib.fly_rates.argtypes = [i32, f32, ctypes.c_int, ctypes.c_double, ctypes.c_double]

    lib.fly_step.restype = ctypes.c_int
    lib.fly_step.argtypes = [
        f32, f32, f32, i8, u8, i64, i32, i32, ctypes.c_int,
        fl, fl, fl, fl, fl, fl, fl, fl, fl, ctypes.c_int8,
        i64, i32, f32, i64, ctypes.c_int,
    ]
    return lib.fly_step, (lib.fly_rates if hasattr(lib, "fly_rates") else None)


def load_lib():
    """The whole shared library, for the visualisation helpers."""
    if not _build():
        return None
    try:
        lib = ctypes.CDLL(_SO)
    except OSError:
        return None
    f32 = ctypes.POINTER(ctypes.c_float)
    u8 = ctypes.POINTER(ctypes.c_uint8)
    i32 = ctypes.POINTER(ctypes.c_int32)
    if not hasattr(lib, "fly_scatter"):
        return None
    lib.fly_scatter.restype = None
    lib.fly_scatter.argtypes = [f32, i32, f32, i32, ctypes.c_int, ctypes.c_float]
    lib.fly_fade_tone.restype = None
    lib.fly_fade_tone.argtypes = [f32, u8, ctypes.c_int, ctypes.c_float,
                                  ctypes.c_float]
    if hasattr(lib, "fly_set_threads"):
        lib.fly_set_threads.restype = None
        lib.fly_set_threads.argtypes = [ctypes.c_int]
    return lib


def ptr(arr, ctype):
    return arr.ctypes.data_as(ctypes.POINTER(ctype))


# --- embedded Game Boy Advance -------------------------------------------
_GBA_SRC = os.path.join(_HERE, "_gba.c")
_GBA_SO = os.path.join(_HERE, "_gba.so")


def load_gba(verbose=True):
    """ctypes handle on the libmgba shim, building it if needed.

    Needs libmgba and its headers (Debian: libmgba-dev). Returns None, with
    a reason printed, if either is missing.
    """
    stale = (not os.path.exists(_GBA_SO)
             or os.path.getmtime(_GBA_SO) < os.path.getmtime(_GBA_SRC))
    if stale:
        try:
            subprocess.run(["gcc", "-O2", "-shared", "-fPIC", "-o", _GBA_SO,
                            _GBA_SRC, "-lmgba", "-lm"],
                           check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            if verbose:
                err = getattr(exc, "stderr", b"") or b""
                print("flybrain: could not build the GBA shim -- is libmgba-dev "
                      f"installed?\n{err.decode(errors='replace')[:400]}",
                      file=sys.stderr)
            return None
    lib = ctypes.CDLL(_GBA_SO)
    c = ctypes
    lib.gba_open.argtypes = [c.c_char_p, c.c_char_p, c.c_char_p]
    lib.gba_open.restype = c.c_int
    lib.gba_frame.argtypes = [c.c_uint32]
    lib.gba_frame.restype = c.c_void_p
    lib.gba_audio_rms.restype = c.c_double
    if hasattr(lib, "gba_audio_samples"):
        lib.gba_audio_samples.argtypes = [c.c_void_p, c.c_int]
        lib.gba_audio_samples.restype = c.c_int
    for name, rt in (("gba_read8", c.c_uint8), ("gba_read16", c.c_uint16),
                     ("gba_read32", c.c_uint32)):
        getattr(lib, name).argtypes = [c.c_uint32]
        getattr(lib, name).restype = rt
    lib.gba_close.restype = None
    for name in ("gba_save_state", "gba_load_state"):
        getattr(lib, name).argtypes = [c.c_char_p]
        getattr(lib, name).restype = c.c_int
    return lib
