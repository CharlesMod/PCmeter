#!/usr/bin/env python3
"""PCmeter backend: samples CPU and GPU telemetry and streams it to the web UI.

Run:  python3 backend/pcmeter.py --open
Then the UI lives at http://127.0.0.1:8765 and the raw stream at /stream (Server-Sent Events).
With --app (what the desktop shortcut uses) it shows the CPU and GPU cards as desktop gadgets:
one frameless, translucent Qt Quick window per card, with no outer window (see desktop.py and qml/);
the telemetry goes straight into the windows, with no server. With --app --browser it opens
a Chromium app window instead, reuses a copy that is already running, and quits once its last
window has been closed for a while.

Sources
  CPU    psutil (all platforms); RAPL package power on Linux when readable.
  NVIDIA NVML via the `nvidia-ml-py` package (Linux and Windows).
  AMD    amdgpu sysfs + /proc/<pid>/fdinfo on Linux.
"""
import argparse
from collections import deque
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import urllib.request
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

try:
    import pynvml
except ImportError:
    pynvml = None

__version__ = "1.2.0"
# A PyInstaller build unpacks the web UI next to the code in sys._MEIPASS.
WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "web"
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _read(path, cast=str):
    try:
        return cast(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def _proc_name(pid):
    try:
        return psutil.Process(pid).name()
    except (psutil.Error, OSError):
        return f"pid {pid}"


# ---------------------------------------------------------------- CPU

def cpu_model():
    system = platform.system()
    if system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif system == "Darwin":
        try:
            return subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return platform.processor() or "CPU"


# Preferred sensor chips and labels, most accurate first.
TEMP_CHIPS = ("k10temp", "zenpower", "coretemp", "cpu_thermal", "soc_thermal", "acpitz")
TEMP_LABELS = ("Tdie", "Tctl", "Package id 0")


def find_cpu_temp_file():
    """The hwmon input for the best CPU sensor, found once so each sample is a single file read."""
    chips = {}
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        name = _read(hw / "name")
        if name:
            chips.setdefault(name, hw)
    for chip in TEMP_CHIPS:
        hw = chips.get(chip)
        if not hw:
            continue
        labels = {_read(f): f for f in hw.glob("temp*_label")}
        for label in TEMP_LABELS:
            if label in labels:
                return Path(str(labels[label]).replace("_label", "_input"))
        if (hw / "temp1_input").exists():
            return hw / "temp1_input"
    return None


def cpu_temp():
    if not hasattr(psutil, "sensors_temperatures"):
        return None
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    for chip in TEMP_CHIPS:
        entries = temps.get(chip)
        if not entries:
            continue
        for label in TEMP_LABELS:
            for e in entries:
                if e.label == label:
                    return e.current
        return entries[0].current
    return None


# ---------------------------------------------------------------- coolant (radiator / liquid) temperature

COOLANT_RE = re.compile(r"coolant|liquid|water", re.I)
# hwmon chips of AIO coolers and pump controllers whose temp1 is the liquid temperature.
COOLANT_CHIPS = ("kraken2", "x53", "z53", "kraken2023", "kraken2023elite", "d5next", "aquaero",
                 "octo", "quadro", "highflownext", "leakshield", "rog_ryujin", "waterforce")
COOLANT_RANGE = (5.0, 90.0)   # anything outside this is a dead or bogus sensor
HWMON_ROOT = "/sys/class/hwmon"


def _plausible(c):
    return c is not None and COOLANT_RANGE[0] <= c <= COOLANT_RANGE[1]


def find_coolant_file(root=HWMON_ROOT):
    """(input file, source name) of a hwmon coolant sensor, or None. A labelled sensor wins over a known chip's temp1."""
    chip_fallback = None
    try:
        hwmons = sorted(Path(root).glob("hwmon*"))
    except OSError:
        return None
    for hw in hwmons:
        name = _read(hw / "name") or ""
        for lf in sorted(hw.glob("temp*_label")):
            if COOLANT_RE.search(_read(lf) or ""):
                inp = Path(str(lf).replace("_label", "_input"))
                if _plausible((_read(inp, int) or 0) / 1000 if inp.exists() else None):
                    return inp, name or "hwmon"
        inp = hw / "temp1_input"
        if (chip_fallback is None and name in COOLANT_CHIPS and not (hw / "temp1_label").exists()
                and inp.exists() and _plausible((_read(inp, int) or 0) / 1000)):
            chip_fallback = (inp, name)
    return chip_fallback


def parse_liquidctl_status(text):
    """(temperature C, device description) from `liquidctl status --json` output, or None."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    for dev in data if isinstance(data, list) else []:
        for item in dev.get("status") or []:
            if "liquid temperature" in str(item.get("key", "")).lower() and item.get("unit", "°C") in ("°C", "C"):
                try:
                    value = float(item["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                return value, str(dev.get("description") or "")
    return None


class LiquidctlPoller:
    """Background reader of the liquid temperature through the liquidctl CLI.

    SAFETY: this only ever runs `liquidctl status`. It must never call initialize/set (or anything
    else that writes), because that would change pump, fan or LED settings on the user's cooler.
    """
    MAX_AGE = 20.0      # a reading older than this counts as no reading
    PERIOD = 5.0
    BACKOFF_PERIOD = 60.0
    BACKOFF_AFTER = 5   # consecutive failures before slowing down

    def __init__(self, path, clock=time.monotonic):
        self.path = path
        self.clock = clock
        self.value = None       # (temp, description, timestamp)
        self.failures = 0
        self.timeout = 5.0

    def poll_once(self):
        try:
            out = subprocess.run([self.path, "status", "--json"], capture_output=True, text=True,
                                 timeout=self.timeout, check=True).stdout
            parsed = parse_liquidctl_status(out)
            if parsed is None:
                raise ValueError("no liquid temperature in output")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            self.failures += 1
            if self.failures == 1:
                print(f"pcmeter: liquidctl status failed: {exc}", file=sys.stderr)
            return False
        self.failures = 0
        self.value = (parsed[0], parsed[1], self.clock())
        return True

    def delay(self):
        return self.BACKOFF_PERIOD if self.failures >= self.BACKOFF_AFTER else self.PERIOD

    def run(self):
        while True:
            self.poll_once()
            time.sleep(self.delay())

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="liquidctl").start()

    def latest(self):
        """(temp, source) from the last good poll, or (None, None) if there is none or it is stale."""
        v = self.value
        if v and self.clock() - v[2] <= self.MAX_AGE and _plausible(v[0]):
            return v[0], "liquidctl: " + v[1] if v[1] else "liquidctl"
        return None, None


def pick_temp_source(coolant_temp, preference="auto"):
    """Which temperature the dial should show: "coolant" or "cpu"."""
    return "coolant" if coolant_temp is not None and preference != "cpu" else "cpu"


FANCTL_PATHS = ("/run/fanctl/state.json", "/run/host/run/fanctl/state.json")


class FanctlCoolant:
    """The coolant temperature from fanctl's state file (`inputs.coolant`): fanctl, a root service, already
    polls the loop's own sensor, so this is the first source and costs one stat per sample, a parse only
    when the file changed, and no subprocess. Stale (`t` older than STALE seconds), missing or malformed
    means no value, and the caller falls back to the hwmon / liquidctl search."""
    STALE = 30.0

    def __init__(self, path="auto", paths=FANCTL_PATHS, wall=time.time):
        self.paths = [] if path == "none" else ([path] if path != "auto" else list(paths))
        self.wall = wall
        self.cache = {}            # path -> (mtime_ns, size, t, coolant)

    def present(self):
        return any(os.path.exists(p) for p in self.paths)

    def read_input(self, name):
        """`inputs.<name>` of a fresh state file as a float, else None."""
        for p in self.paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            key = (st.st_mtime_ns, st.st_size)
            hit = self.cache.get(p)
            if not hit or hit[0] != key:
                t, inputs = None, {}
                try:
                    with open(p) as f:
                        d = json.load(f)
                    t = d.get("t")
                    t = t if isinstance(t, (int, float)) and not isinstance(t, bool) else st.st_mtime
                    inputs = d.get("inputs") if isinstance(d.get("inputs"), dict) else {}
                except Exception:
                    pass
                hit = self.cache[p] = (key, t, inputs)
            _, t, inputs = hit
            v = inputs.get(name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and t is not None \
                    and 0 <= self.wall() - t <= self.STALE:
                return float(v)
        return None

    def read(self):
        c = self.read_input("coolant")
        return c if _plausible(c) else None


class CoolantSensor:
    RESCAN = 60.0

    def __init__(self, preference="auto", liquidctl="auto", hwmon_root=HWMON_ROOT,
                 system=None, clock=time.monotonic, start_thread=True, fanctl="none", wall=time.time):
        self.preference = preference
        self.fanctl = FanctlCoolant(fanctl, wall=wall) if fanctl != "none" else None
        self.hwmon_root = hwmon_root
        self.system = system or platform.system()
        self.clock = clock
        self.file = self.source = None
        self.poller = None
        self.next_scan = 0.0
        if self.system == "Linux":
            self._scan()
            path = None if liquidctl == "none" else (liquidctl if liquidctl != "auto" else shutil.which("liquidctl"))
            owned = self.fanctl is not None and self.fanctl.present()    # fanctl polls the loop: no second client
            if path and not self.file and not owned and (liquidctl != "auto" or os.path.exists(path)):
                self.poller = LiquidctlPoller(path, clock)
                if start_thread:
                    self.poller.start()

    def _scan(self):
        found = find_coolant_file(self.hwmon_root)
        self.file, self.source = found if found else (None, None)
        self.next_scan = self.clock() + self.RESCAN

    def read(self):
        """(coolant temp C or None, source name or None); never blocks on the liquidctl process."""
        if self.system != "Linux":
            return self._read_psutil()
        if self.fanctl is not None:
            c = self.fanctl.read()
            if c is not None:
                return c, "fanctl"
        if not self.file and self.clock() >= self.next_scan:
            self._scan()
        if self.file:
            c = _read(self.file, int)
            c = c / 1000 if c is not None else None
            if _plausible(c):
                return c, self.source
            self.file = self.source = None   # sensor vanished or went bogus: look again at the next rescan
        if self.poller:
            return self.poller.latest()
        return None, None

    @staticmethod
    def _read_psutil():
        if not hasattr(psutil, "sensors_temperatures"):
            return None, None
        try:
            temps = psutil.sensors_temperatures()
        except Exception:
            return None, None
        for chip, entries in temps.items():
            for e in entries:
                if COOLANT_RE.search(e.label or "") and _plausible(e.current):
                    return e.current, chip
        return None, None

    def fields(self):
        temp, source = self.read()
        return {"coolant_temp": temp, "coolant_source": source if temp is not None else None,
                "temp_source": pick_temp_source(temp, self.preference)}


class Rapl:
    """CPU package power from the Linux powercap energy counter (root may be required)."""

    def __init__(self):
        self.path = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
        self.wrap = _read("/sys/class/powercap/intel-rapl:0/max_energy_range_uj", int) or 2**32
        self.last = None

    def watts(self):
        energy = _read(self.path, int)
        if energy is None:
            return None
        now = time.monotonic()
        watts = None
        if self.last:
            de = energy - self.last[0]
            if de < 0:
                de += self.wrap
            dt = now - self.last[1]
            if dt > 0:
                watts = de / 1e6 / dt
        self.last = (energy, now)
        return watts


def group_procs(rows, limit=10):
    """Merge the processes of one program (same executable, else the same name) into a single row: the
    CPU shares and memory add up, `n` counts the processes and `pid` is the busiest member's. Sorted
    busiest first."""
    groups = {}
    for r in rows:
        key = r.get("exe") or r["name"]
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"pid": r["pid"], "name": key, "util": 0.0, "mem_mb": 0.0, "n": 0, "_top": -1.0}
        g["util"] += r["util"]
        g["mem_mb"] += r["mem_mb"]
        g["n"] += 1
        if r["util"] > g["_top"]:
            g["_top"], g["pid"] = r["util"], r["pid"]
    out = sorted(groups.values(), key=lambda g: (g["util"], g["mem_mb"]), reverse=True)[:limit]
    for g in out:
        del g["_top"]
    return out


def with_other(procs, busy_pct, floor=0.5):
    """Append the CPU the listed rows do not account for (programs that started and ended between two scans,
    interrupt time, the unlisted tail) so the gap is visible: machine busy minus the listed rows, in the same
    units (% of one core)."""
    if busy_pct is None:
        return procs
    gap = busy_pct - sum(p["util"] for p in procs)
    if gap < floor:
        return procs
    return procs + [{"pid": 0, "name": "other / short-lived", "util": gap, "mem_mb": 0.0, "n": 0, "other": True}]


def cpu_busy_jiffies():
    """Busy jiffies of the whole machine from /proc/stat (everything but idle and iowait), or None."""
    try:
        with open("/proc/stat", "rb") as f:
            v = [int(x) for x in f.readline().split()[1:9]]
    except (OSError, ValueError):
        return None
    return sum(v) - v[3] - (v[4] if len(v) > 4 else 0)


# ---------------------------------------------------------------- how close to a limit
# The CPU and GPU dials show the closest of up to three headroom terms, each 0..100 % of its limit:
#   POWER   package watts / the sustained power limit (PL1, or the board limit on a GPU)
#   THERMAL (die temperature - T_IDLE_REF) / (throttle temperature - T_IDLE_REF); 100 % is the throttle point.
#           T_IDLE_REF = 40 C: about what a die reads at idle in a normal room, so the scale starts near 0
#           when idle and a die at 70 C reads 50 %.
#   LOAD    utilisation
# A missing source drops its term; the dial never shows less than the load.
T_IDLE_REF = 40.0
TERM_ORDER = ("POWER", "THERMAL", "LOAD")      # the tie-break, too


def power_term(watts, limit_w):
    if watts is None or not limit_w or limit_w <= 0:
        return None
    return max(0.0, min(100.0, watts / limit_w * 100))


def thermal_term(temp, limit_c, ref=T_IDLE_REF):
    if temp is None or limit_c is None or limit_c <= ref:
        return None
    return max(0.0, min(100.0, (temp - ref) / (limit_c - ref) * 100))


def pick_limiter(terms):
    """(value, name) of the largest available term; ties go to POWER, then THERMAL, then LOAD."""
    best = None
    for name in TERM_ORDER:
        v = terms.get(name)
        if v is not None and (best is None or v > best[0]):
            best = (v, name)
    return best if best else (None, None)


def limit_info(util, temp, temp_limit, power_w, power_limit_w, throttled, **extra):
    terms = {"POWER": power_term(power_w, power_limit_w), "THERMAL": thermal_term(temp, temp_limit),
             "LOAD": None if util is None else max(0.0, min(100.0, float(util)))}
    value, term = pick_limiter(terms)
    return {"value": value, "term": term, "terms": terms, "throttled": bool(throttled),
            "power_limit_w": power_limit_w, "temp_limit": temp_limit, **extra}


class ThrottleWatch:
    """True while a throttle counter (milliseconds or events, only ever rising) has risen within the last
    `hold` seconds: a 0.5 s sample sees the counter move in only some of the samples, and the flag should not flicker."""

    def __init__(self, hold=3.0):
        self.hold, self.last, self.until = hold, None, 0.0

    def update(self, counter, now):
        if counter is not None:
            if self.last is not None and counter > self.last:
                self.until = now + self.hold
            self.last = counter
        return now < self.until


INTEL_THROTTLE = "/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_total_time_ms"
RAPL_DIR = "/sys/class/powercap/intel-rapl:0"


def rapl_pl1_watts(rapl_dir=RAPL_DIR):
    """The sustained (long-term, PL1) package power limit in watts, or None."""
    for i in range(3):
        if _read(f"{rapl_dir}/constraint_{i}_name", str) == "long_term":
            uw = _read(f"{rapl_dir}/constraint_{i}_power_limit_uw", int)
            return uw / 1e6 if uw else None
    return None


class CpuSampler:
    def __init__(self, proc_every=1.0, coolant=None):
        self.proc_every = proc_every
        self.coolant = coolant or CoolantSensor()
        self.name = cpu_model()
        self.cores = psutil.cpu_count(logical=False) or 0
        self.threads = psutil.cpu_count(logical=True) or 0
        self.rapl = Rapl()
        self.temp_file = find_cpu_temp_file() if platform.system() == "Linux" else None
        self.procs = []
        self.next_proc_scan = 0.0
        self.prev_ticks, self.last_scan = {}, None
        self.prev_busy, self.exes = None, {}
        self.fanctl = getattr(self.coolant, "fanctl", None)
        self.throttle = ThrottleWatch()
        self.throttle_file = INTEL_THROTTLE if os.path.exists(INTEL_THROTTLE) else None
        self.pl1, self.pl1_at = None, 0.0
        self.tjmax = None
        if self.temp_file:
            self.tjmax = (_read(str(self.temp_file).replace("_input", "_crit"), int) or 0) / 1000 or 100.0
        psutil.cpu_percent(percpu=True)  # prime the counters

    def reset(self):
        """Forget every running counter after a pause, so the first sample after it is not an average over the gap."""
        psutil.cpu_percent(percpu=True)
        self.rapl.last = None
        self.prev_ticks, self.last_scan = {}, None
        self.prev_busy = None
        self.next_proc_scan = 0.0

    def _power(self):
        """Package watts: RAPL when readable, else fanctl's published `inputs.power`."""
        w = self.rapl.watts()
        if w is None and self.fanctl is not None:
            w = self.fanctl.read_input("power")
        return w

    def _limit(self, util, temp, power_w):
        now = time.monotonic()
        if now - self.pl1_at > 30:
            self.pl1, self.pl1_at = rapl_pl1_watts(), now
        counter = _read(self.throttle_file, int) if self.throttle_file else None
        return limit_info(util, temp, self.tjmax if temp is not None else None, power_w, self.pl1,
                          self.throttle.update(counter, now))

    def _scan_procs(self):
        if platform.system() == "Linux":
            self._scan_procs_linux()
        else:
            self._scan_procs_psutil()

    def _exe(self, pid):
        """Executable name of a process (cached per pid), or None for kernel threads and the unreadable."""
        if pid in self.exes:
            return self.exes[pid]
        try:
            exe = os.path.basename(os.readlink(f"/proc/{pid}/exe")).removesuffix(" (deleted)") or None
        except OSError:
            exe = None
        self.exes[pid] = exe
        return exe

    def _scan_procs_linux(self):
        """One read of /proc/<pid>/stat per process: name, CPU ticks and RSS are all in it. Each process's CPU is
        in % of ONE core (100 = a full core, as in top); processes of one program are grouped, and a row
        for the busy time nobody listed (short-lived processes and the rest) is appended."""
        now = time.monotonic()
        dt = now - self.last_scan if self.last_scan else None
        busy_now = cpu_busy_jiffies()
        ticks_now, found = {}, []
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                with open(f"/proc/{entry.name}/stat", "rb") as f:
                    data = f.read()
            except OSError:
                continue
            close = data.rfind(b")")
            fields = data[close + 2:].split()
            pid = int(entry.name)
            ticks = int(fields[11]) + int(fields[12])   # utime + stime
            ticks_now[pid] = ticks
            if dt and pid in self.prev_ticks:
                util = (ticks - self.prev_ticks[pid]) / CLK_TCK / dt * 100
                found.append({
                    "pid": pid,
                    "name": data[data.find(b"(") + 1:close].decode(errors="replace"),
                    "exe": self._exe(pid) if util > 0 else None,
                    "util": util,
                    "mem_mb": int(fields[21]) * PAGE_SIZE / 2**20,
                })
        self.exes = {pid: e for pid, e in self.exes.items() if pid in ticks_now}
        busy = None
        if dt and busy_now is not None and self.prev_busy is not None:
            busy = (busy_now - self.prev_busy) / CLK_TCK / dt * 100
        self.prev_ticks, self.last_scan, self.prev_busy = ticks_now, now, busy_now
        for r in found:
            if r["exe"] is None and len(r["name"]) == 15:   # the kernel truncates names; take the full one from cmdline
                try:
                    with open(f"/proc/{r['pid']}/cmdline", "rb") as f:
                        argv0 = f.read().split(b"\0", 1)[0].split(b" ", 1)[0]
                    full = os.path.basename(argv0.decode(errors="replace"))
                    if full.startswith(r["name"]):   # some programs rewrite argv; keep only a real match
                        r["name"] = full
                except OSError:
                    pass
        procs = group_procs(found)
        self.procs = with_other(procs, busy) if busy is not None else procs

    def _scan_procs_psutil(self):
        # process_iter caches Process objects, so cpu_percent measures since the previous scan.
        found = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            info = p.info
            if info["cpu_percent"] is None:
                continue
            found.append({
                "pid": info["pid"],
                "name": info["name"] or f"pid {info['pid']}",
                "util": info["cpu_percent"],       # % of one core, as in top
                "mem_mb": info["memory_info"].rss / 2**20 if info["memory_info"] else 0,
            })
        procs = group_procs(found)
        self.procs = procs

    def sample(self, procs=True, full=True):
        """`procs`: some process list is on screen; `full`: the readouts beside the dial are too (not just the dial)."""
        now = time.monotonic()
        if procs and now >= self.next_proc_scan:
            self._scan_procs()
            self.next_proc_scan = now + self.proc_every
        elif not procs:
            self.last_scan = None      # the next scan after a gap must not average over it
            self.prev_ticks = {}
            self.next_proc_scan = 0.0
        per_core = psutil.cpu_percent(percpu=True)
        util = sum(per_core) / max(1, len(per_core))
        temp = _read(self.temp_file, int) / 1000 if self.temp_file else cpu_temp()
        power = self._power()
        if not full:
            return {"name": self.name, "cores": self.cores, "threads": self.threads,
                    "util": util, "per_core": per_core, "temp": temp, "limit": self._limit(util, temp, power),
                    "freq_mhz": None, "power_w": power, "load": None, "procs": []}
        freq = psutil.cpu_freq()
        try:
            load = list(psutil.getloadavg())
        except (AttributeError, OSError):
            load = None
        return {
            **self.coolant.fields(),
            "name": self.name,
            "cores": self.cores,
            "threads": self.threads,
            "util": util,
            "per_core": per_core,
            "temp": temp,
            "limit": self._limit(util, temp, power),
            "freq_mhz": freq.current if freq else None,
            "power_w": power,
            "load": load,
            "procs": self.procs,
        }


# ---------------------------------------------------------------- NVIDIA

class NvidiaGpu:
    vendor = "NVIDIA"

    def __init__(self, handle, proc_every=1.0):
        n = pynvml
        self.h = handle
        self.proc_every = proc_every
        name = n.nvmlDeviceGetName(handle)
        self.name = name.decode() if isinstance(name, bytes) else name
        self.bus_width = self._try(n.nvmlDeviceGetMemoryBusWidth)
        self.max_mem_clock = self._try(n.nvmlDeviceGetMaxClockInfo, n.NVML_CLOCK_MEM)
        self.power_limit = self._try(n.nvmlDeviceGetEnforcedPowerLimit)
        self.last_proc_ts = 0
        self.last_mem_ts = 0
        self.procs = []
        self.next_proc_scan = 0.0

    def _try(self, fn, *args):
        try:
            return fn(self.h, *args)
        except pynvml.NVMLError:
            return None

    def _bandwidth(self, mem_clock_mhz):
        # NVML reports the effective memory clock, so x2 for double data rate.
        if not (mem_clock_mhz and self.bus_width):
            return None
        return mem_clock_mhz * 1e6 * self.bus_width * 2 / 8 / 1e9

    def _mem_busy(self):
        """Memory-controller busy %, averaged over every driver sample since the last call. The driver
        samples several times a second in whole percents; the average resolves far finer steps."""
        try:
            _, samples = pynvml.nvmlDeviceGetSamples(self.h, pynvml.NVML_MEMORY_UTILIZATION_SAMPLES, self.last_mem_ts)
        except pynvml.NVMLError:
            return None
        fresh = [s for s in samples if s.timeStamp > self.last_mem_ts]
        if not fresh:
            return None
        self.last_mem_ts = max(s.timeStamp for s in fresh)
        if len(fresh) > 12:          # the first call returns the whole buffer; keep about the last second
            fresh = fresh[-12:]
        return sum(s.sampleValue.uiVal for s in fresh) / len(fresh)

    def _scan_procs(self):
        n = pynvml
        found = {}
        for fn in (n.nvmlDeviceGetComputeRunningProcesses, n.nvmlDeviceGetGraphicsRunningProcesses):
            for p in self._try(fn) or []:
                vram = (p.usedGpuMemory or 0) / 2**20
                entry = found.setdefault(p.pid, {"pid": p.pid, "util": 0.0, "vram_mb": 0.0})
                entry["vram_mb"] = max(entry["vram_mb"], vram)
        for s in self._try(n.nvmlDeviceGetProcessUtilization, self.last_proc_ts) or []:
            entry = found.setdefault(s.pid, {"pid": s.pid, "util": 0.0, "vram_mb": 0.0})
            entry["util"] = max(entry["util"], float(s.smUtil))
            self.last_proc_ts = max(self.last_proc_ts, s.timeStamp)
        for entry in found.values():
            entry["name"] = _proc_name(entry["pid"])
        self.procs = sorted(found.values(), key=lambda p: (p["util"], p["vram_mb"]), reverse=True)[:10]

    def sample(self, procs=True, full=True):
        """Only what is on screen is asked of the driver: `full` the readouts beside the dial, `procs` the process list."""
        n = pynvml
        rates = self._try(n.nvmlDeviceGetUtilizationRates)
        mem = self._try(n.nvmlDeviceGetMemoryInfo) if full else None
        mem_clock = self._try(n.nvmlDeviceGetClockInfo, n.NVML_CLOCK_MEM)
        power = self._try(n.nvmlDeviceGetPowerUsage) if full else None
        now = time.monotonic()
        if procs and now >= self.next_proc_scan:
            self._scan_procs()
            self.next_proc_scan = now + self.proc_every
        elif not procs:
            self.next_proc_scan = 0.0
        mem_util = self._mem_busy()
        if mem_util is None and rates:
            mem_util = float(rates.memory)
        bw_now = self._bandwidth(mem_clock)
        return {
            "name": self.name,
            "vendor": self.vendor,
            "util": rates.gpu if rates else None,
            "mem_util": mem_util,
            "temp": self._try(n.nvmlDeviceGetTemperature, n.NVML_TEMPERATURE_GPU),
            "clock_mhz": self._try(n.nvmlDeviceGetClockInfo, n.NVML_CLOCK_GRAPHICS) if full else None,
            "power_w": power / 1000 if power is not None else None,
            "power_limit_w": self.power_limit / 1000 if self.power_limit else None,
            "fan_pct": self._try(n.nvmlDeviceGetFanSpeed) if full else None,
            "vram_used_mb": mem.used / 2**20 if mem else None,
            "vram_total_mb": mem.total / 2**20 if mem else None,
            # Memory-controller busy time x bandwidth at the current memory clock.
            "bw_gbps": bw_now * mem_util / 100 if bw_now is not None and mem_util is not None else None,
            "bw_peak_gbps": self._bandwidth(self.max_mem_clock),
            "bw_capacity_gbps": bw_now,
            "bus_width": self.bus_width,
            "procs": self.procs,
        }


# ---------------------------------------------------------------- AMD (Linux)

class DrmClients:
    """Per-process GPU usage from DRM fdinfo (amdgpu, and the same format for i915/xe)."""

    def __init__(self, driver, pdev):
        self.driver = driver
        self.pdev = pdev
        self.prev = {}  # (pid, client_id) -> (busy_ns, wall_ns)

    def scan(self, full=True):
        wall = time.monotonic_ns()
        per_pid = {}
        seen = {}
        if full:
            pid_dirs = [d for d in Path("/proc").iterdir() if d.name.isdigit()]
        else:
            pid_dirs = [Path(f"/proc/{pid}") for pid in {pid for pid, _ in self.prev}]
        for pid_dir in pid_dirs:
            try:
                fds = list((pid_dir / "fdinfo").iterdir())
            except OSError:
                continue
            for fd in fds:
                try:
                    text = fd.read_text()
                except OSError:
                    continue
                if f"drm-driver:\t{self.driver}" not in text or self.pdev not in text:
                    continue
                fields = dict(line.split(":\t", 1) for line in text.splitlines() if ":\t" in line)
                key = (int(pid_dir.name), fields.get("drm-client-id"))
                if key in seen:
                    continue
                busy = sum(int(v.split()[0]) for k, v in fields.items() if k.startswith("drm-engine-"))
                vram = fields.get("drm-memory-vram", "0 KiB").split()
                vram_mb = int(vram[0]) / 1024 if vram[0].isdigit() else 0
                seen[key] = (busy, wall)
                util = 0.0
                if key in self.prev:
                    pb, pw = self.prev[key]
                    if wall > pw:
                        util = max(0.0, min(100.0, (busy - pb) / (wall - pw) * 100))
                entry = per_pid.setdefault(key[0], {"pid": key[0], "util": 0.0, "vram_mb": 0.0})
                entry["util"] += util
                entry["vram_mb"] += vram_mb
        self.prev = seen
        for entry in per_pid.values():
            entry["name"] = _proc_name(entry["pid"])
        return sorted(per_pid.values(), key=lambda p: (p["util"], p["vram_mb"]), reverse=True)[:10]


class AmdGpu:
    vendor = "AMD"

    def __init__(self, dev, peak_bw=None, proc_every=2.0):
        self.proc_every = proc_every
        self.dev = Path(dev)
        hwmons = sorted(self.dev.glob("hwmon/hwmon*"))
        self.hwmon = hwmons[0] if hwmons else None
        self.name = _read(self.dev / "product_name") or f"AMD Radeon ({self.dev.resolve().name})"
        self.peak_bw = peak_bw
        self.clients = DrmClients("amdgpu", self.dev.resolve().name)
        self.procs = []
        self.next_proc_scan = 0.0
        self.next_full_scan = 0.0

    def _hw(self, name, scale):
        if not self.hwmon:
            return None
        v = _read(self.hwmon / name, int)
        return v / scale if v is not None else None

    def sample(self, procs=True, full=True):
        now = time.monotonic()
        if procs and now >= self.next_proc_scan:
            walk = now >= self.next_full_scan
            self.procs = self.clients.scan(walk)
            self.next_proc_scan = now + self.proc_every
            if walk:
                self.next_full_scan = now + 5 * self.proc_every
        elif not procs:
            self.next_proc_scan = 0.0
            self.next_full_scan = 0.0
            self.clients.prev = {}
        mem_util = _read(self.dev / "mem_busy_percent", int)
        used = _read(self.dev / "mem_info_vram_used", int)
        total = _read(self.dev / "mem_info_vram_total", int)
        fan = None
        if self.hwmon:
            rpm, rpm_max = self._hw("fan1_input", 1), self._hw("fan1_max", 1)
            if rpm is not None and rpm_max:
                fan = rpm / rpm_max * 100
        return {
            "name": self.name,
            "vendor": self.vendor,
            "util": _read(self.dev / "gpu_busy_percent", int),
            "mem_util": mem_util,
            "temp": self._hw("temp1_input", 1000),
            "clock_mhz": self._hw("freq1_input", 1e6),
            "power_w": self._hw("power1_average", 1e6) or self._hw("power1_input", 1e6),
            "power_limit_w": self._hw("power1_cap", 1e6),
            "fan_pct": fan,
            "vram_used_mb": used / 2**20 if used else None,
            "vram_total_mb": total / 2**20 if total else None,
            "bw_gbps": self.peak_bw * mem_util / 100 if self.peak_bw and mem_util is not None else None,
            "bw_peak_gbps": self.peak_bw,
            "bw_capacity_gbps": self.peak_bw,
            "bus_width": None,
            "procs": self.procs,
        }


# Where a container (distrobox, toolbox, the Claude desktop app) mounts the host's root: NVML lives
# in the host's driver, so when the default library path has no libnvidia-ml, look for it there.
HOST_NVML = ("/run/host/usr/lib64/libnvidia-ml.so.1", "/run/host/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1",
             "/run/host/usr/lib/libnvidia-ml.so.1", "/run/host/usr/lib/aarch64-linux-gnu/libnvidia-ml.so.1")


def load_nvml():
    """Make pynvml usable, falling back to the host's libnvidia-ml when running inside a container."""
    if pynvml is None:
        return False
    import ctypes
    try:
        ctypes.CDLL("libnvidia-ml.so.1")
        return True
    except OSError:
        pass
    for path in HOST_NVML:
        if Path(path).exists():
            try:
                pynvml.nvmlLib = ctypes.CDLL(path)
                return True
            except OSError:
                continue
    return False


def detect_gpus(amd_peak_bw=None, proc_every=1.0):
    gpus = []
    if load_nvml():
        try:
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                gpus.append(NvidiaGpu(pynvml.nvmlDeviceGetHandleByIndex(i), proc_every))
        except pynvml.NVMLError:
            pass
    if platform.system() == "Linux":
        for card in sorted(Path("/sys/class/drm").glob("card*")):
            dev = card / "device"
            if re.fullmatch(r"card\d+", card.name) and _read(dev / "vendor") == "0x1002" \
                    and (dev / "gpu_busy_percent").exists():
                gpus.append(AmdGpu(dev, amd_peak_bw, 2 * proc_every))
    return gpus


# ---------------------------------------------------------------- server

class DisplayBaseline:
    """VRAM traffic spent refreshing the monitors (scanout), so it can be left out of the activity.

    The display engine re-reads every frame buffer on every refresh whether or not anything runs,
    which is several GB/s with a few high-resolution monitors. It does not use the 3D or compute
    engines, so the baseline is learned only from samples where those are near idle: the 90th
    percentile of the last 10 minutes of such samples, which brings an idle desktop
    to zero without its jitter showing. With no idle samples (a long game) the last value is kept.
    """

    IDLE_UTIL = 15      # GPU core % at or below which a sample counts as idle
    WINDOW = 600.0      # seconds of idle samples to learn from
    MIN_SAMPLES = 6

    def __init__(self):
        self.samples = deque()
        self.value = 0.0

    def update(self, util, traffic):
        now = time.monotonic()
        if util is not None and util <= self.IDLE_UTIL:
            self.samples.append((now, traffic))
        while self.samples and now - self.samples[0][0] > self.WINDOW:
            self.samples.popleft()
        if len(self.samples) >= self.MIN_SAMPLES:
            ordered = sorted(v for _, v in self.samples)
            self.value = ordered[len(ordered) * 9 // 10]
        return self.value


def subtract_display(g, baseline):
    """Report memory traffic net of the monitor-refresh baseline; the totals stay alongside."""
    capacity = g.pop("bw_capacity_gbps", None)
    if g.get("bw_gbps") is not None:
        total = g["bw_gbps"]
        base = baseline.update(g.get("util"), total)
        g.update(bw_total_gbps=total, bw_display_gbps=base, bw_gbps=max(0.0, total - base))
        if capacity:
            g["mem_util"] = min(100.0, g["bw_gbps"] / capacity * 100)
    elif g.get("mem_util") is not None:
        total = g["mem_util"]
        base = baseline.update(g.get("util"), total)
        g.update(mem_util_total=total, mem_util_display=base, mem_util=max(0.0, total - base))
    return g


class History:
    """Ring buffer of 5 s averages (coolant, die, gpu0 temps) plus fan override events, and the last
    RAW_SECONDS of readings exactly as sampled (every sample, not averaged) for the 1 minute view."""
    BUCKET = 5.0
    MAXLEN = 1440   # 2 h
    RAW_SECONDS = 150.0

    def __init__(self, bucket=BUCKET, maxlen=MAXLEN):
        self.bucket = bucket
        self.maxlen = maxlen
        self.samples = deque(maxlen=maxlen)
        self.events = deque(maxlen=500)
        self.lock = threading.Lock()
        self.start = None
        self.acc = [[0.0, 0], [0.0, 0], [0.0, 0]]
        self.raw = deque()

    def add(self, t, coolant, die, gpu):
        """Feed one reading (None for missing); flushes a sample once a bucket has elapsed."""
        with self.lock:
            self.raw.append([round(t, 2)] + [round(v, 2) if isinstance(v, (int, float)) else None for v in (coolant, die, gpu)])
            while self.raw and t - self.raw[0][0] > self.RAW_SECONDS:
                self.raw.popleft()
        if self.start is None:
            self.start = t
        for a, v in zip(self.acc, (coolant, die, gpu)):
            if isinstance(v, (int, float)):
                a[0] += v
                a[1] += 1
        if t - self.start >= self.bucket:
            row = [round(t, 1)] + [round(a[0] / a[1], 1) if a[1] else None for a in self.acc]
            with self.lock:
                self.samples.append(row)
            self.start = None
            self.acc = [[0.0, 0], [0.0, 0], [0.0, 0]]

    def event(self, t, on, trigger):
        with self.lock:
            self.events.append({"t": round(t, 1), "on": on, "trigger": trigger})

    def recent(self, seconds):
        """The last `seconds` of raw readings and the fan events from then on (cheap: for the 1 minute view)."""
        with self.lock:
            raw = list(self.raw)
            events = list(self.events)
        if raw:
            lo = raw[-1][0] - seconds
            raw = [r for r in raw if r[0] >= lo]
            events = [e for e in events if e["t"] >= lo]
        return {"samples": raw, "events": events}

    def payload(self):
        with self.lock:
            samples = list(self.samples)
            events = list(self.events)
            raw = list(self.raw)
        if samples:
            cutoff = samples[0][0]
            events = [e for e in events if e["t"] >= cutoff]
        return {"interval": int(self.bucket), "samples": samples, "events": events, "raw": raw}


class FanState:
    """Reads the state file written by fanctl (absent on most machines; errors are silent)."""
    EVERY = 2.0
    STALE = 15.0

    def __init__(self, path="auto", history=None, paths=FANCTL_PATHS):
        self.paths = [] if path == "none" else ([path] if path != "auto" else list(paths))
        self.history = history
        self.checked = None
        self.value = None
        self.override = None   # last seen override flag

    def read(self, now=None):
        now = time.time() if now is None else now
        if self.checked is not None and 0 <= now - self.checked < self.EVERY:
            return self.value
        self.checked = now
        self.value = self._load(now)
        return self.value

    def _load(self, now):
        for p in self.paths:
            try:
                if not os.path.exists(p):
                    continue
                with open(p) as f:
                    d = json.load(f)
                t = d.get("t")
                if not isinstance(t, (int, float)):
                    t = os.stat(p).st_mtime
                if now - t > self.STALE:
                    return None
                override = bool(d.get("override"))
                trigger = d.get("trigger")
                fans = d.get("fans")
                fan = next(iter(fans.values()), None) if isinstance(fans, dict) else None
                fan = fan if isinstance(fan, dict) else {}
                if override != self.override:
                    if (self.override is not None or override) and self.history:
                        self.history.event(now, override, trigger)
                    self.override = override
                return {"override": override, "trigger": trigger,
                        "duty": fan.get("duty"), "rpm": fan.get("rpm")}
            except Exception:
                return None
        return None


FULL = {"procs": True, "full": True}


class Telemetry:
    def __init__(self, interval, amd_peak_bw, proc_every=1.0, coolant=None, fanctl="auto"):
        self.interval = interval
        self.cpu = CpuSampler(proc_every, coolant)
        self.gpus = detect_gpus(amd_peak_bw, proc_every)
        self.baselines = [DisplayBaseline() for _ in self.gpus]
        self.history = History()
        self.fan = FanState(fanctl, self.history)
        self.host = socket.gethostname()
        self.latest = None
        self.cond = threading.Condition()
        self.clients = 0
        self.local = False                 # the desktop gadgets are on screen (see desktop.py)
        self.listeners = []                # in-process consumers: called with each snapshot dict
        self.want = {"cpu": FULL, "gpu": FULL}   # what the gadgets show (None: card hidden); a stream wants everything
        self.watched = threading.Event()   # set while anyone is looking: a stream is open or a gadget is visible
        self.paused = threading.Event()    # set while nobody is: ends the sleep between samples at once
        self.paused.set()
        self.cold = True                   # counters need priming before the next sample

    def _refresh(self):
        if self.clients > 0 or self.local:
            self.paused.clear()
            self.watched.set()
        else:
            self.watched.clear()
            self.paused.set()
            self.cold = True

    def attach(self, delta):
        with self.cond:
            self.clients += delta
            self._refresh()

    def set_local(self, on):
        """Gadgets visible and the session unlocked: sample. Otherwise wait on an event, using no CPU."""
        self.local = bool(on)
        self._refresh()

    def run(self):
        while True:
            self.watched.wait()   # no window open: sleep instead of sampling
            if self.cold:         # first sample after a pause: don't average over the gap
                self.cold = False
                self.cpu.reset()
                self.paused.wait(0.25)
                if not self.watched.is_set():
                    continue
            started = time.monotonic()
            # A stream (the browser page) shows everything; gadgets only what their views show.
            want = self.want if self.clients == 0 else {"cpu": FULL, "gpu": FULL}
            gpus = []
            gwant = want["gpu"]
            if gwant is None and want["cpu"] is not None:
                gwant = {"procs": False, "full": False}     # the Temps history wants the GPU temperature too
            for gpu, baseline in zip(self.gpus, self.baselines):
                if gwant is None:
                    break
                try:
                    gpus.append(subtract_display(gpu.sample(**gwant), baseline))
                except Exception as exc:  # one flaky device should not stop the stream
                    gpus.append({"name": gpu.name, "vendor": gpu.vendor, "error": str(exc)})
            now = time.time()
            cpu = self.cpu.sample(**want["cpu"]) if want["cpu"] is not None else None
            fan = self.fan.read(now)
            if cpu is not None or gpus:
                self.history.add(now, cpu.get("coolant_temp") if cpu else None, cpu.get("temp") if cpu else None,
                                 gpus[0].get("temp") if gpus else None)
            snap = {
                "t": now,
                "interval": self.interval,
                "host": self.host,
                "cpu": cpu,
                "gpus": gpus,
                "fan": fan,
            }
            if self.clients > 0:
                with self.cond:
                    self.latest = json.dumps(snap)
                    self.cond.notify_all()
            for fn in self.listeners:
                fn(snap)
            self.paused.wait(max(0.0, self.interval - (time.monotonic() - started)))


def make_handler(telemetry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/stream":
                self._stream()
            elif path == "/health":
                self._send(b"pcmeter", "text/plain")
            elif path == "/snapshot":
                telemetry.attach(1)
                try:
                    with telemetry.cond:
                        telemetry.cond.wait(timeout=2 * telemetry.interval)
                        data = telemetry.latest or "{}"
                finally:
                    telemetry.attach(-1)
                self._send(data.encode(), "application/json")
            elif path == "/history":
                self._send(json.dumps(telemetry.history.payload()).encode(), "application/json")
            elif path in ("/", "/index.html"):
                self._send((WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            last = None
            telemetry.attach(1)
            try:
                while True:
                    with telemetry.cond:
                        telemetry.cond.wait_for(lambda: telemetry.latest is not last, timeout=5)
                        data = telemetry.latest
                    if data is not last:
                        self.wfile.write(f"data: {data}\n\n".encode())
                    else:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    last = data
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                telemetry.attach(-1)

    return Handler


# ---------------------------------------------------------------- app mode

CHROMIUM_COMMANDS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "brave-browser", "brave", "microsoft-edge", "vivaldi")
CHROMIUM_FLATPAKS = ("com.google.Chrome", "org.chromium.Chromium", "com.brave.Browser",
                     "com.microsoft.Edge", "com.vivaldi.Vivaldi",
                     "io.github.ungoogled_software.ungoogled_chromium")


def _clean_env():
    """The environment for launching other programs, minus the libraries a PyInstaller build injects."""
    env = os.environ.copy()
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def _spawn(cmd):
    subprocess.Popen(cmd, env=_clean_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def open_window(url):
    """A chromeless app window from any Chromium-based browser (native or Flatpak), else the default browser."""
    app = [f"--app={url}", "--window-size=900,1040"]
    for cmd in CHROMIUM_COMMANDS:
        exe = shutil.which(cmd)
        if exe:
            return _spawn([exe, *app])
    if shutil.which("flatpak"):
        roots = (Path("/var/lib/flatpak/app"), Path.home() / ".local/share/flatpak/app")
        for ref in CHROMIUM_FLATPAKS:
            if any((root / ref).exists() for root in roots):
                return _spawn(["flatpak", "run", ref, *app])
    opener = shutil.which("xdg-open") or shutil.which("open")
    if opener:
        return _spawn([opener, url])
    webbrowser.open(url)


def running_copy(host, port):
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1) as r:
            return r.read() == b"pcmeter"
    except OSError:
        return False


def quit_when_closed(telemetry, server, grace):
    """Stop once no window has been connected for `grace` seconds (longer before the first one arrives)."""
    seen, idle_since = False, time.monotonic()
    while True:
        time.sleep(1)
        if telemetry.clients > 0:
            seen, idle_since = True, None
            continue
        idle_since = idle_since or time.monotonic()
        if time.monotonic() - idle_since > (grace if seen else max(grace, 90)):
            server.shutdown()
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between samples")
    ap.add_argument("--amd-peak-bw", type=float, help="AMD peak VRAM bandwidth in GB/s (sysfs does not expose it)")
    ap.add_argument("--open", action="store_true", help="open the UI in a browser")
    ap.add_argument("--app", action="store_true",
                    help="show the CPU and GPU cards as desktop gadgets (frameless, translucent, one window each)")
    ap.add_argument("--browser", action="store_true",
                    help="with --app, use a Chromium app window instead of the desktop window; "
                         "reuses a running copy and quits after the last window closes")
    ap.add_argument("--idle-exit", type=float, default=20, metavar="SECONDS",
                    help="with --app --browser, how long to wait after the last window closes (default 20)")
    ap.add_argument("--temp-source", choices=("auto", "cpu", "coolant"), default="auto",
                    help="temperature for the dial: coolant if the machine has a sensor (auto, coolant) or the CPU die (cpu)")
    ap.add_argument("--liquidctl", default="auto", metavar="PATH",
                    help="liquidctl executable for AIO coolers without a kernel driver (default: find it on PATH; 'none' disables)")
    ap.add_argument("--fanctl-state", default="auto", metavar="PATH",
                    help="fanctl state JSON for the fan field (default: /run/fanctl/state.json or the distrobox host copy; 'none' disables)")
    ap.add_argument("--version", action="version", version=f"PCmeter {__version__}")
    args = ap.parse_args()

    desktop = None
    if args.app and not args.browser:
        try:
            import desktop
        except ImportError as exc:
            print(f"Desktop gadgets unavailable ({exc}); using a browser window instead.", file=sys.stderr)
    if args.app and desktop is None and running_copy(args.host, args.port):
        open_window(f"http://{args.host}:{args.port}/")
        return

    def start():
        """Start sampling and serving; returns (telemetry, server, url)."""
        telemetry = Telemetry(args.interval, args.amd_peak_bw,
                              coolant=CoolantSensor(args.temp_source, args.liquidctl, fanctl=args.fanctl_state),
                              fanctl=args.fanctl_state)
        try:
            server = ThreadingHTTPServer((args.host, args.port), make_handler(telemetry))
        except OSError:
            if not args.app:
                raise
            server = ThreadingHTTPServer((args.host, 0), make_handler(telemetry))   # port taken by something else
        server.daemon_threads = True
        url = f"http://{args.host}:{server.server_address[1]}/"
        threading.Thread(target=telemetry.run, daemon=True).start()
        gpu_names = ", ".join(g.name for g in telemetry.gpus) or "none found"
        print(f"PCmeter {__version__} on {url}\n  CPU: {telemetry.cpu.name}\n  GPU: {gpu_names}", flush=True)
        return telemetry, server, url

    if desktop is not None:
        # Qt owns the main thread; the sampler runs beside it and feeds the windows directly (no
        # server). It starts paused and samples only while a gadget is visible and the session unlocked.
        def start_sampler():
            telemetry = Telemetry(args.interval, args.amd_peak_bw, proc_every=5.0,
                                   coolant=CoolantSensor(args.temp_source, args.liquidctl, fanctl=args.fanctl_state),
                                   fanctl=args.fanctl_state)
            threading.Thread(target=telemetry.run, daemon=True).start()
            gpu_names = ", ".join(g.name for g in telemetry.gpus) or "none found"
            print(f"PCmeter {__version__}\n  CPU: {telemetry.cpu.name}\n  GPU: {gpu_names}", flush=True)
            return telemetry
        sys.exit(desktop.run(start_sampler))

    telemetry, server, url = start()
    if args.app:
        threading.Thread(target=quit_when_closed, args=(telemetry, server, args.idle_exit), daemon=True).start()
        open_window(url)
    elif args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
