#!/usr/bin/env python3
"""PCmeter backend: samples CPU and GPU telemetry and streams it to the web UI.

Run:  python3 backend/pcmeter.py --open
Then the UI lives at http://127.0.0.1:8765 and the raw stream at /stream (Server-Sent Events).

Sources
  CPU    psutil (all platforms); RAPL package power on Linux when readable.
  NVIDIA NVML via the `nvidia-ml-py` package (Linux and Windows).
  AMD    amdgpu sysfs + /proc/<pid>/fdinfo on Linux.
"""
import argparse
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

try:
    import pynvml
except ImportError:
    pynvml = None

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
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


class CpuSampler:
    def __init__(self):
        self.name = cpu_model()
        self.cores = psutil.cpu_count(logical=False) or 0
        self.threads = psutil.cpu_count(logical=True) or 0
        self.rapl = Rapl()
        self.temp_file = find_cpu_temp_file() if platform.system() == "Linux" else None
        self.procs = []
        self.next_proc_scan = 0.0
        self.prev_ticks, self.last_scan = {}, None
        psutil.cpu_percent(percpu=True)  # prime the counters

    def _scan_procs(self):
        if platform.system() == "Linux":
            self._scan_procs_linux()
        else:
            self._scan_procs_psutil()

    def _scan_procs_linux(self):
        """One read of /proc/<pid>/stat per process: name, CPU ticks and RSS are all in it."""
        now = time.monotonic()
        dt = now - self.last_scan if self.last_scan else None
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
                found.append({
                    "pid": pid,
                    "name": data[data.find(b"(") + 1:close].decode(errors="replace"),
                    "util": (ticks - self.prev_ticks[pid]) / CLK_TCK / dt * 100 / max(1, self.threads),
                    "mem_mb": int(fields[21]) * PAGE_SIZE / 2**20,
                })
        self.prev_ticks, self.last_scan = ticks_now, now
        found.sort(key=lambda p: (p["util"], p["mem_mb"]), reverse=True)
        self.procs = found[:10]
        for p in self.procs:
            if len(p["name"]) == 15:   # the kernel truncates names; take the full one from cmdline
                try:
                    with open(f"/proc/{p['pid']}/cmdline", "rb") as f:
                        argv0 = f.read().split(b"\0", 1)[0].split(b" ", 1)[0]
                    full = os.path.basename(argv0.decode(errors="replace"))
                    if full.startswith(p["name"]):   # some programs rewrite argv; keep only a real match
                        p["name"] = full
                except OSError:
                    pass

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
                # Share of the whole machine, matching the dial (top's per-core % / threads).
                "util": info["cpu_percent"] / max(1, self.threads),
                "mem_mb": info["memory_info"].rss / 2**20 if info["memory_info"] else 0,
            })
        found.sort(key=lambda p: (p["util"], p["mem_mb"]), reverse=True)
        self.procs = found[:10]

    def sample(self):
        now = time.monotonic()
        if now >= self.next_proc_scan:
            self._scan_procs()
            self.next_proc_scan = now + 1.0
        per_core = psutil.cpu_percent(percpu=True)
        freq = psutil.cpu_freq()
        try:
            load = list(psutil.getloadavg())
        except (AttributeError, OSError):
            load = None
        return {
            "name": self.name,
            "cores": self.cores,
            "threads": self.threads,
            "util": sum(per_core) / max(1, len(per_core)),
            "per_core": per_core,
            "temp": _read(self.temp_file, int) / 1000 if self.temp_file else cpu_temp(),
            "freq_mhz": freq.current if freq else None,
            "power_w": self.rapl.watts(),
            "load": load,
            "procs": self.procs,
        }


# ---------------------------------------------------------------- NVIDIA

class NvidiaGpu:
    vendor = "NVIDIA"

    def __init__(self, handle):
        n = pynvml
        self.h = handle
        name = n.nvmlDeviceGetName(handle)
        self.name = name.decode() if isinstance(name, bytes) else name
        self.bus_width = self._try(n.nvmlDeviceGetMemoryBusWidth)
        self.max_mem_clock = self._try(n.nvmlDeviceGetMaxClockInfo, n.NVML_CLOCK_MEM)
        self.power_limit = self._try(n.nvmlDeviceGetEnforcedPowerLimit)
        self.last_proc_ts = 0
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

    def sample(self):
        n = pynvml
        rates = self._try(n.nvmlDeviceGetUtilizationRates)
        mem = self._try(n.nvmlDeviceGetMemoryInfo)
        mem_clock = self._try(n.nvmlDeviceGetClockInfo, n.NVML_CLOCK_MEM)
        power = self._try(n.nvmlDeviceGetPowerUsage)
        now = time.monotonic()
        if now >= self.next_proc_scan:
            self._scan_procs()
            self.next_proc_scan = now + 1.0
        mem_util = rates.memory if rates else None
        bw_now = self._bandwidth(mem_clock)
        return {
            "name": self.name,
            "vendor": self.vendor,
            "util": rates.gpu if rates else None,
            "mem_util": mem_util,
            "temp": self._try(n.nvmlDeviceGetTemperature, n.NVML_TEMPERATURE_GPU),
            "clock_mhz": self._try(n.nvmlDeviceGetClockInfo, n.NVML_CLOCK_GRAPHICS),
            "power_w": power / 1000 if power is not None else None,
            "power_limit_w": self.power_limit / 1000 if self.power_limit else None,
            "fan_pct": self._try(n.nvmlDeviceGetFanSpeed),
            "vram_used_mb": mem.used / 2**20 if mem else None,
            "vram_total_mb": mem.total / 2**20 if mem else None,
            # Memory-controller busy time x bandwidth at the current memory clock.
            "bw_gbps": bw_now * mem_util / 100 if bw_now is not None and mem_util is not None else None,
            "bw_peak_gbps": self._bandwidth(self.max_mem_clock),
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

    def __init__(self, dev, peak_bw=None):
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

    def sample(self):
        now = time.monotonic()
        if now >= self.next_proc_scan:
            full = now >= self.next_full_scan
            self.procs = self.clients.scan(full)
            self.next_proc_scan = now + 2.0
            if full:
                self.next_full_scan = now + 10.0
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
            "bus_width": None,
            "procs": self.procs,
        }


def detect_gpus(amd_peak_bw=None):
    gpus = []
    if pynvml is not None:
        try:
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                gpus.append(NvidiaGpu(pynvml.nvmlDeviceGetHandleByIndex(i)))
        except pynvml.NVMLError:
            pass
    if platform.system() == "Linux":
        for card in sorted(Path("/sys/class/drm").glob("card*")):
            dev = card / "device"
            if re.fullmatch(r"card\d+", card.name) and _read(dev / "vendor") == "0x1002" \
                    and (dev / "gpu_busy_percent").exists():
                gpus.append(AmdGpu(dev, amd_peak_bw))
    return gpus


# ---------------------------------------------------------------- server

class Telemetry:
    def __init__(self, interval, amd_peak_bw):
        self.interval = interval
        self.cpu = CpuSampler()
        self.gpus = detect_gpus(amd_peak_bw)
        self.host = socket.gethostname()
        self.latest = None
        self.cond = threading.Condition()
        self.clients = 0
        self.watched = threading.Event()   # set while at least one stream is open

    def attach(self, delta):
        with self.cond:
            self.clients += delta
            if self.clients > 0:
                self.watched.set()
            else:
                self.watched.clear()

    def run(self):
        while True:
            self.watched.wait()   # no window open: sleep instead of sampling
            started = time.monotonic()
            gpus = []
            for gpu in self.gpus:
                try:
                    gpus.append(gpu.sample())
                except Exception as exc:  # one flaky device should not stop the stream
                    gpus.append({"name": gpu.name, "vendor": gpu.vendor, "error": str(exc)})
            snapshot = json.dumps({
                "t": time.time(),
                "interval": self.interval,
                "host": self.host,
                "cpu": self.cpu.sample(),
                "gpus": gpus,
            })
            with self.cond:
                self.latest = snapshot
                self.cond.notify_all()
            time.sleep(max(0.0, self.interval - (time.monotonic() - started)))


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
            elif path == "/snapshot":
                telemetry.attach(1)
                try:
                    with telemetry.cond:
                        telemetry.cond.wait(timeout=2 * telemetry.interval)
                        data = telemetry.latest or "{}"
                finally:
                    telemetry.attach(-1)
                self._send(data.encode(), "application/json")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between samples")
    ap.add_argument("--amd-peak-bw", type=float, help="AMD peak VRAM bandwidth in GB/s (sysfs does not expose it)")
    ap.add_argument("--open", action="store_true", help="open the UI in a browser")
    args = ap.parse_args()

    telemetry = Telemetry(args.interval, args.amd_peak_bw)
    threading.Thread(target=telemetry.run, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(telemetry))
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    gpu_names = ", ".join(g.name for g in telemetry.gpus) or "none found"
    print(f"PCmeter on {url}\n  CPU: {telemetry.cpu.name}\n  GPU: {gpu_names}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
