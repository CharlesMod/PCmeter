PCmeter 1.0 is a CPU and GPU monitor that lives on your desktop: Vista-gadget-style dual dials (load with an inset temperature sub-dial), Geiger-counter activity strips with optional clicks, per-thread meters, VRAM throughput, and fold-out top-process lists, in eight skins (Glass, Matrix, Hive, Windows, macOS, Linux, Dark, White).

## What's new in 1.0

- **Desktop gadgets.** The CPU and GPU cards are separate frameless, translucent windows with the desktop blurred behind them, and no outer window. Drag a card anywhere, drag its side edges to resize it, and hover it for its menu and dismiss buttons. On KDE Plasma they return to where you left them and stay off the taskbar.
- **Gauges only.** Condense both cards to just their dials, or use Compact or Full.
- **Light on the system.** The gadgets draw on the CPU only, so watching your GPU never wakes it, at 24 frames a second with film-style motion blur (about a quarter of one core on the Glass skin).
- **Clicks per card.** Each activity strip has its own mute and volume slider; the menu has the master switch.
- **VRAM throughput** in MB/s or GB/s, on a graph that scales to recent traffic.
- The menu, also in the system tray, picks the skin, view, clicks, keep-on-top and which cards are shown. Launching PCmeter again brings back a dismissed card.

## Install on Linux (Bazzite, Fedora, Ubuntu, Arch, …)

Download the `.tar.gz` below, then:

```bash
tar xzf PCmeter-*-linux-x86_64.tar.gz
cd PCmeter-*-linux-x86_64
./install.sh
```

You get a **PCmeter** icon on your desktop and in the app menu. Everything installs inside your home folder, so it works on immutable systems, and it replaces an earlier PCmeter install. Remove it with `./install.sh --uninstall`.

- NVIDIA GPUs need the proprietary driver (Bazzite's NVIDIA images include it). AMD GPUs work out of the box. PCmeter also finds the NVIDIA driver from inside a distrobox or toolbox.
- Window placement, blur and keep-on-top use KDE Plasma's KWin. On other desktops the gadgets still work, and the compositor places them.
- `pcmeter --app --browser` opens the single-page view in a Chrome-family browser window instead.
- On KDE Plasma, the first click on the desktop icon may ask whether to run it.

Requires x86_64 and glibc 2.35 or newer.
