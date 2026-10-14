"""Discovery/validation search for the descending neurons that carry an aversive memory."""
import os, sys, subprocess, threading, time
sys.path.insert(0, "~/flybrain")
HERE = os.path.dirname(os.path.abspath(__file__))

def opener(name, mode="a"):
    f = open(os.path.join(HERE, name), mode)
    def log(*a):
        f.write(" ".join(str(x) for x in a) + "\n"); f.flush(); os.fsync(f.fileno())
        print(*a, flush=True)
    return log

def power_monitor(stop):
    log = opener("power.log")
    log("--- dn_channel run", time.strftime("%Y-%m-%d %H:%M:%S"))
    while not stop.is_set():
        try:
            r = lambda *c: subprocess.run(["vcgencmd", *c], capture_output=True, text=True).stdout.strip()
            log(time.strftime("%H:%M:%S"), r("get_throttled"), "EXT5V",
                r("pmic_read_adc", "EXT5V_V").split("=")[-1], r("measure_temp"),
                "load", round(os.getloadavg()[0], 2))
        except Exception as e:
            log("monitor error", e)
        stop.wait(5)

stop = threading.Event()
threading.Thread(target=power_monitor, args=(stop,), daemon=True).start()
from flybrain.connectome import Connectome
from flybrain import condition
condition.dn_channel(Connectome(), log=opener("dn_channel.log"))
stop.set()
