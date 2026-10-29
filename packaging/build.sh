#!/bin/sh
# Builds the Linux app folder and the release tarball into dist/.
#   pip install -r requirements.txt pyinstaller && packaging/build.sh
# The desktop window embeds Qt WebEngine, so the build is a folder (one-dir), not a single file:
# a single file would unpack a few hundred MB into /tmp on every launch.
set -e
cd "$(dirname "$0")/.."
version=$(python3 -c 'import sys; sys.path.insert(0, "backend"); import pcmeter; print(pcmeter.__version__)')
arch=$(uname -m)
pyinstaller --onedir --clean --noconfirm --name pcmeter --contents-directory lib \
  --add-data "$PWD/web:web" --add-data "$PWD/packaging/pcmeter.svg:." \
  --hidden-import pynvml --hidden-import desktop --paths backend \
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQuick3D \
  --exclude-module PySide6.QtPdf --exclude-module PySide6.QtMultimedia --exclude-module PySide6.Qt3DCore \
  --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtWebEngineQuick \
  --distpath dist --workpath build --specpath build \
  backend/pcmeter.py
# Libraries that must come from the system the app runs on, never from the build machine: the
# graphics stack (GBM looks for the host's driver at the build distro's path, so a bundled one sends
# Chromium off the GPU), the C++ runtime that host drivers need at its newest, audio, D-Bus, X11,
# fonts, and the GTK pulled in by Qt's GTK theme plugin (not needed). Same idea as AppImage's excludelist.
lib=dist/pcmeter/lib
for pattern in 'libgbm.so*' 'libdrm.so*' 'libEGL.so*' 'libGL.so*' 'libGLX.so*' 'libGLdispatch.so*' 'libOpenGL.so*' \
               'libvulkan.so*' 'libstdc++.so*' 'libgcc_s.so*' 'libasound.so*' 'libdbus-1.so*' 'libsystemd.so*' \
               'libudev.so*' 'libX11.so*' 'libX11-xcb.so*' 'libxcb.so*' 'libxcb-glx.so*' 'libxcb-randr.so*' \
               'libxcb-render.so*' 'libxcb-shape.so*' 'libxcb-shm.so*' 'libxcb-sync.so*' 'libxcb-xfixes.so*' \
               'libxcb-xkb.so*' 'libXau.so*' 'libXdmcp.so*' 'libXcomposite.so*' 'libXcursor.so*' 'libXdamage.so*' \
               'libXext.so*' 'libXfixes.so*' 'libXi.so*' 'libXinerama.so*' 'libXrandr.so*' 'libXrender.so*' \
               'libXtst.so*' 'libxkbcommon.so*' 'libxkbcommon-x11.so*' 'libxkbfile.so*' 'libfontconfig.so*' \
               'libfreetype.so*' 'libharfbuzz.so*' 'libexpat.so*' 'libgtk-3.so*' 'libgdk-3.so*' 'libatk-1.0.so*' \
               'libatk-bridge-2.0.so*' 'libatspi.so*' 'libcairo.so*' 'libcairo-gobject.so*' 'libpango-1.0.so*' \
               'libpangocairo-1.0.so*' 'libpangoft2-1.0.so*' 'libgdk_pixbuf-2.0.so*' 'libepoxy.so*' 'libcups.so*' \
               'libavahi-client.so*' 'libavahi-common.so*'; do
  find "$lib" -maxdepth 1 -name "$pattern" -delete
done
find "$lib" -path '*/plugins/platformthemes/libqgtk3.so' -delete
stage="dist/PCmeter-$version-linux-$arch"
rm -rf "$stage" && mkdir -p "$stage"
cp -r dist/pcmeter "$stage/pcmeter"
cp packaging/install.sh packaging/pcmeter.svg README.md "$stage/"
tar -C dist -czf "$stage.tar.gz" "$(basename "$stage")"
echo "Built $stage.tar.gz"
