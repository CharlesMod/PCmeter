#!/bin/sh
# Installs PCmeter for the current user: the program, an app-menu entry and a desktop shortcut.
# Nothing goes outside your home folder, so it works on immutable systems like Bazzite.
#   ./install.sh              install or update
#   ./install.sh --uninstall  remove everything this installed
set -e
here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/.local/bin"
lib="$HOME/.local/lib/pcmeter"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
desktop=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")

if [ "$1" = "--uninstall" ]; then
  rm -rf "$lib"
  rm -f "$bin/pcmeter" "$apps/pcmeter.desktop" "$icons/pcmeter.svg" "$desktop/pcmeter.desktop"
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/pcmeter.desktop"     # "Launch on startup", if it was on
  command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true
  echo "PCmeter removed."
  exit 0
fi

mkdir -p "$bin" "$apps" "$icons" "$(dirname "$lib")"
rm -rf "$lib.new"
cp -r "$here/pcmeter" "$lib.new"
rm -rf "$lib" && mv "$lib.new" "$lib"          # a running copy keeps its old files until it exits
ln -sfn "$lib/pcmeter" "$bin/pcmeter"
install -m 644 "$here/pcmeter.svg" "$icons/pcmeter.svg"
cat > "$apps/pcmeter.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=PCmeter
GenericName=System Monitor
Comment=CPU and GPU dials, activity and top processes
Exec=$bin/pcmeter --app
Icon=pcmeter
Terminal=false
Categories=System;Monitor;
Keywords=cpu;gpu;temperature;monitor;
StartupNotify=false
StartupWMClass=pcmeter
DESKTOP
chmod 755 "$apps/pcmeter.desktop"

if [ -d "$desktop" ]; then
  install -m 755 "$apps/pcmeter.desktop" "$desktop/pcmeter.desktop"
  # GNOME needs the shortcut marked trusted; KDE Plasma only needs it executable.
  command -v gio >/dev/null && gio set "$desktop/pcmeter.desktop" metadata::trusted true 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true

echo "PCmeter installed."
echo "  Launch it from the PCmeter icon on your desktop or in the app menu."
[ -d "$desktop" ] || echo "  (No Desktop folder found, so only the app-menu entry was added.)"
