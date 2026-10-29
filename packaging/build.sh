#!/bin/sh
# Builds the single-file Linux binary and the release tarball into dist/.
#   pip install -r requirements.txt pyinstaller && packaging/build.sh
set -e
cd "$(dirname "$0")/.."
version=$(python3 -c 'import sys; sys.path.insert(0, "backend"); import pcmeter; print(pcmeter.__version__)')
arch=$(uname -m)
pyinstaller --onefile --clean --noconfirm --name pcmeter \
  --add-data "$PWD/web:web" --hidden-import pynvml \
  --distpath dist --workpath build --specpath build \
  backend/pcmeter.py
stage="dist/PCmeter-$version-linux-$arch"
rm -rf "$stage" && mkdir -p "$stage"
cp dist/pcmeter packaging/install.sh packaging/pcmeter.svg "$stage/"
cp README.md "$stage/README.md"
tar -C dist -czf "$stage.tar.gz" "$(basename "$stage")"
echo "Built $stage.tar.gz"
