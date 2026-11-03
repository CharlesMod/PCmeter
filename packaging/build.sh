#!/bin/sh
# Builds the Linux app folder and the release tarball into dist/.
#   pip install -r requirements.txt pyinstaller && packaging/build.sh
# The desktop cards are Qt Quick (qml/), no browser engine, so the folder is small; it stays one-dir
# because a single file would unpack the Qt libraries into /tmp on every launch. web/ is still bundled
# for browser mode (pcmeter --browser) and the HTTP server.
set -e
cd "$(dirname "$0")/.."
version=$(python3 -c 'import sys; sys.path.insert(0, "backend"); import pcmeter; print(pcmeter.__version__)')
arch=$(uname -m)
pyinstaller --onedir --clean --noconfirm --name pcmeter --contents-directory lib \
  --add-data "$PWD/web:web" --add-data "$PWD/qml:qml" --add-data "$PWD/packaging/pcmeter.svg:." \
  --hidden-import pynvml --hidden-import desktop --hidden-import PySide6.QtQuick --hidden-import PySide6.QtQml \
  --hidden-import PySide6.QtMultimedia --hidden-import PySide6.QtDBus --paths backend \
  --exclude-module PySide6.QtQuick3D --exclude-module PySide6.QtPdf --exclude-module PySide6.Qt3DCore \
  --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization \
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtWebEngineWidgets \
  --distpath dist --workpath build --specpath build \
  backend/pcmeter.py
# Libraries that must come from the system the app runs on, never from the build machine: the
# graphics stack (GBM looks for the host's driver at the build distro's path, so a bundled one sends
# Qt off the GPU), the C++ runtime that host drivers need at its newest, audio, D-Bus, X11,
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
# PyInstaller's QML hook bundles every Qt module's QML plugin, WebEngine alone is 200 MB. The cards use
# QtQuick, QtQml and QtMultimedia (the click sound); drop the rest of the Qt libraries and QML modules.
qt="$lib/PySide6/Qt"
for m in Qt3D QtQuick3D Qt5Compat QtCharts QtDataVisualization QtGraphs QtLocation QtPositioning QtRemoteObjects \
         QtScxml QtSensors QtTest QtTextToSpeech QtWebChannel QtWebEngine QtWebSockets QtWebView; do
  rm -rf "$qt/qml/$m"
done
for pattern in 'libQt63D*' 'libQt6Quick3D*' 'libQt6Charts*' 'libQt6DataVisualization*' 'libQt6Graphs*' 'libQt6Location*' \
               'libQt6Positioning*' 'libQt6RemoteObjects*' 'libQt6Scxml*' 'libQt6Sensors*' 'libQt6TextToSpeech*' \
               'libQt6VirtualKeyboard*' 'libQt6WebChannel*' 'libQt6WebEngine*' 'libQt6WebSockets*' 'libQt6WebView*' \
               'libQt6QuickTest*' 'libQt6Test*' 'libQt6SpatialAudio*'; do
  find "$qt/lib" -maxdepth 1 -name "$pattern" -delete
done
rm -rf "$qt/plugins/qmltooling"
stage="dist/PCmeter-$version-linux-$arch"
rm -rf "$stage" && mkdir -p "$stage"
cp -r dist/pcmeter "$stage/pcmeter"
cp packaging/install.sh packaging/pcmeter.svg README.md "$stage/"
tar -C dist -czf "$stage.tar.gz" "$(basename "$stage")"
echo "Built $stage.tar.gz"
