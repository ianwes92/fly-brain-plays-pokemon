"""Phase 1b: training diagnostics, then ceiling vs matched control.

Every log line is fsync'd, and a power monitor records the supply voltage and
throttle flags, because two earlier runs died in unexplained reboots.
"""
import os, sys, subprocess, threading, time
sys.path.insert(0, "~/flybrain")
HERE = os.path.dirname(os.path.abspath(__file__))

def opener(name):
    f = open(os.path.join(HERE, name), "w")
    def log(*a):
        f.write(" ".join(str(x) for x in a) + "\n"); f.flush(); os.fsync(f.fileno())
        print(*a, flush=True)
    return log

def power_monitor(stop):
    log = opener("power.log")
    while not stop.is_set():
        try:
            thr = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True).stdout.strip()
            v = subprocess.run(["vcgencmd", "pmic_read_adc", "EXT5V_V"], capture_output=True, text=True).stdout.split("=")[-1].strip()
            t = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True).stdout.strip()
            log(time.strftime("%H:%M:%S"), thr, "EXT5V", v, t, "load", os.getloadavg()[0])
        except Exception as e:
            log("monitor error", e)
        stop.wait(5)

stop = threading.Event()
threading.Thread(target=power_monitor, args=(stop,), daemon=True).start()
from flybrain.connectome import Connectome
from flybrain import condition
cx = Connectome()
condition.training_diagnostics(cx, seed=0, log=opener("training_diagnostics.log"))
condition.ceiling_experiment(cx, seeds=range(5), log=opener("ceiling_control.log"))
stop.set()
