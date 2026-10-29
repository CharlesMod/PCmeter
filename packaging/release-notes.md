PCmeter is a CPU and GPU monitor with Vista-gadget-style dual dials, Geiger-counter activity strips, per-thread meters, VRAM throughput, fold-out top-process lists, and eight skins (Glass, Matrix, Hive, Windows, macOS, Linux, Dark, White).

## Install on Linux (Bazzite, Fedora, Ubuntu, Arch, …)

Download the `.tar.gz` below, then:

```bash
tar xzf PCmeter-*-linux-x86_64.tar.gz
cd PCmeter-*-linux-x86_64
./install.sh
```

You get a **PCmeter** icon on your desktop and in the app menu. Everything installs inside your home folder, so it works on immutable systems. Remove it with `./install.sh --uninstall`.

- The CPU and GPU cards are desktop gadgets: frameless, translucent windows you drag anywhere, resize, condense to gauges only, or dismiss. Skin, view, clicks and keep-on-top live in the card menu and the system tray.
- Drawn on the CPU at 24 fps with motion blur, so it never wakes the GPU it is watching.
- Clicking the icon again brings back dismissed cards. Quit from the tray or the card menu.
- NVIDIA GPUs need the proprietary driver (Bazzite's NVIDIA images include it). AMD GPUs work out of the box.
- On KDE Plasma, the first click on the desktop icon may ask whether to run it.

Requires x86_64 and glibc 2.35 or newer.
