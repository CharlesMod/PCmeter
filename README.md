# PCmeter

A CPU and GPU monitor with gadget-style instruments. Each card has a dual dial
(load plus an inset temperature sub-dial, after the Vista CPU meter gadget), a
Geiger-counter activity strip with a flashing LED, and the detail beneath it.

## Skins

Glass (default), Matrix, Hive, Windows, macOS, Linux, Dark and White. Pick one from
the toolbar, or start with one using `?skin=matrix`. Every skin is a block of CSS
tokens at the top of `web/index.html`; the dials and strips read the same tokens,
so adding a skin means adding one block and one `<option>`.

## Install (Linux, including Bazzite and other immutable distros)

Download `PCmeter-<version>-linux-x86_64.tar.gz` from the
[latest release](https://github.com/CharlesMod/PCmeter/releases/latest), then:

```bash
tar xzf PCmeter-*-linux-x86_64.tar.gz
cd PCmeter-*-linux-x86_64
./install.sh
```

That puts PCmeter in `~/.local/bin` and adds an app-menu entry and a desktop
shortcut; nothing outside your home folder changes. On KDE Plasma, the first click on
the desktop icon may ask whether to run it. `./install.sh --uninstall` removes it all.

The shortcut runs `pcmeter --app`, which shows the CPU and GPU cards as desktop gadgets: each card is
its own frameless, translucent window with the desktop blurred behind it, and there is no outer
window. Drag a card anywhere to move it, drag its left or right edge to change its width, and hover
it for its menu and dismiss buttons. The menu (also in the system tray) picks the skin, the view
(Full, Compact, or Gauges only), clicks, keep-on-top, and which cards are shown. Launching PCmeter
again brings back any card you dismissed. On KDE Plasma (Wayland) the cards return to where you left
them. NVIDIA cards need the proprietary driver; AMD cards work out of the box.

The gadgets draw on the CPU only, at 24 frames a second with film-style motion blur, so watching the
GPU never wakes it. `PCMETER_RENDER=gpu` lets them use the GPU instead. `pcmeter --app --browser`
opens the old single-page app window in Chrome, Chromium, Brave, Edge or Vivaldi instead.

**Clicks**: each card's activity strip has a speaker button that mutes or unmutes that card (cards
start muted) and a volume slider on hover; the toolbar's Clicks button (in the gadgets, the menu) is
the master switch.

## Run from source

```bash
pip install -r requirements.txt
python3 backend/pcmeter.py --open
```

The UI is served at http://127.0.0.1:8765. Add `?demo` to see simulated data, or
open `web/index.html` directly from disk.

**Compact** hides the detail and puts a 60-second load graph in each card's header. **Gauges** shows only each card's name and dial.

| Flag | What it does |
| --- | --- |
| `--interval 0.5` | Seconds between samples |
| `--amd-peak-bw 960` | Peak VRAM bandwidth in GB/s for AMD cards, so throughput shows in GB/s |
| `--port 8765` / `--host` | Where to listen |
| `--app` / `--app --browser` | Desktop gadgets, or a browser app window |

## What is measured

- **CPU**: total and per-thread load, clock, temperature (`k10temp`, `coretemp`, ...),
  load average, package power (RAPL; often needs root to read).
- **NVIDIA** (NVML): load, temperature, clocks, power, fan, VRAM, top 10 processes
  with per-process load and VRAM.
- **AMD** (amdgpu sysfs): the same, with per-process data from `/proc/<pid>/fdinfo`.
- **VRAM throughput** is an estimate: memory-controller busy % x bandwidth at the current memory
  clock (clock x bus width x 2), shown in MB/s or GB/s. The busy % is averaged over every driver
  sample between updates. NVML has no direct bytes-per-second counter, and its GPM DRAM counter
  reads zero on GeForce cards without profiling rights. The graph scales to recent traffic.
- **Activity** is a Poisson process whose rate rises steeply with load, from about
  25 counts per minute at idle to about 4,800 at full load. *Clicks* plays them
  as audio, CPU on the left channel and GPU on the right.

## Build the binary

```bash
pip install -r requirements.txt pyinstaller
packaging/build.sh
```

## Roadmap

- Desktop widget: wrap `web/` in Tauri with a transparent, frameless, always-on-top
  window and load it with `?widget` (transparent background).
- Windows: psutil and NVML already work; CPU temperature needs LibreHardwareMonitor.
- macOS: psutil works; temperature and GPU need IOKit / `powermetrics`.
