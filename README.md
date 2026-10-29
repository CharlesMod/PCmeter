# PCmeter

A CPU and GPU monitor with gadget-style instruments. Each card has a dual dial
(load plus an inset temperature sub-dial, after the Vista CPU meter gadget), a
Geiger-counter activity strip with a flashing LED, and the detail beneath it.

## Skins

Glass (default), Matrix, Hive, Windows, macOS, Linux, Dark and White. Pick one from
the toolbar, or start with one using `?skin=matrix`. Every skin is a block of CSS
tokens at the top of `web/index.html`; the dials and strips read the same tokens,
so adding a skin means adding one block and one `<option>`.

## Run (Linux)

```bash
pip install -r requirements.txt
python3 backend/pcmeter.py --open
```

The UI is served at http://127.0.0.1:8765. Add `?demo` to see simulated data, or
open `web/index.html` directly from disk.

**Compact** hides the detail and puts a 60-second load graph in each card's header.

| Flag | What it does |
| --- | --- |
| `--interval 0.5` | Seconds between samples |
| `--amd-peak-bw 960` | Peak VRAM bandwidth in GB/s for AMD cards, so throughput shows in GB/s |
| `--port 8765` / `--host` | Where to listen |

## What is measured

- **CPU**: total and per-thread load, clock, temperature (`k10temp`, `coretemp`, ...),
  load average, package power (RAPL; often needs root to read).
- **NVIDIA** (NVML): load, temperature, clocks, power, fan, VRAM, top 10 processes
  with per-process load and VRAM.
- **AMD** (amdgpu sysfs): the same, with per-process data from `/proc/<pid>/fdinfo`.
- **VRAM throughput** is an estimate: memory-controller busy % x peak bandwidth
  (memory clock x bus width x 2). NVML has no direct bytes-per-second counter.
- **Activity** is a Poisson process whose rate rises steeply with load, from about
  25 counts per minute at idle to about 4,800 at full load. *Clicks* plays them
  as audio, CPU on the left channel and GPU on the right.

## Roadmap

- Desktop widget: wrap `web/` in Tauri with a transparent, frameless, always-on-top
  window and load it with `?widget` (transparent background).
- Windows: psutil and NVML already work; CPU temperature needs LibreHardwareMonitor.
- macOS: psutil works; temperature and GPU need IOKit / `powermetrics`.
