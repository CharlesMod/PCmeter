PCmeter is a CPU and GPU monitor with Vista-gadget-style dual dials, Geiger-counter activity strips, per-thread meters, VRAM throughput, fold-out top-process lists, and eight skins (Glass, Matrix, Hive, Windows, macOS, Linux, Dark, White).

## Install on Linux (Bazzite, Fedora, Ubuntu, Arch, …)

Download the `.tar.gz` below, then:

```bash
tar xzf PCmeter-*-linux-x86_64.tar.gz
cd PCmeter-*-linux-x86_64
./install.sh
```

You get a **PCmeter** icon on your desktop and in the app menu. Everything installs inside your home folder, so it works on immutable systems. Remove it with `./install.sh --uninstall`.

- Opens in its own app window when Chrome, Chromium, Brave, Edge or Vivaldi is installed (native or Flatpak); otherwise in your default browser.
- Clicking the icon again reuses the running copy. PCmeter quits 20 seconds after you close its last window, and uses no CPU while no window is open.
- NVIDIA GPUs need the proprietary driver (Bazzite's NVIDIA images include it). AMD GPUs work out of the box.
- On KDE Plasma, the first click on the desktop icon may ask whether to run it.

Requires x86_64 and glibc 2.35 or newer.
