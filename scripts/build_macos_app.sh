#!/usr/bin/env bash
# Build a self-contained macOS application bundle (and DMG) for Remotia.
#
# The bundle includes Python, PyQt, the application, the RDP helper AND the
# FreeRDP libraries it needs, so the resulting .app has no external dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Build the RDP helper (and audio plugin) if they aren't there yet.
if [ ! -x native/rdp_helper ]; then
    echo "Building RDP helper…"
    scripts/build_rdp_helper.sh
fi

# 2. Make sure PyInstaller is available.
./.venv/bin/python -c "import PyInstaller" 2>/dev/null || ./.venv/bin/pip install pyinstaller

# 3. Generate icons if missing.
if [ ! -f assets/Remotia.icns ]; then
    echo "Generating icons…"
    ./.venv/bin/python scripts/generate_icon.py
fi

# 4. Build the .app bundle.
echo "Building Remotia.app…"
./.venv/bin/pyinstaller --noconfirm --clean --windowed \
    --name "Remotia" \
    --osx-bundle-identifier "com.remotia.client" \
    --icon "assets/Remotia.icns" \
    --add-data "assets/Remotia.png:assets" \
    --add-binary "native/librdpsnd-client-pipe.dylib:." \
    --add-data "native/rdp_helper:." \
    remotia_launcher.py

# 5. Ad-hoc sign so it passes local signature verification.
codesign --force --deep -s - dist/Remotia.app

# 6. Package into a DMG with a /Applications symlink.
echo "Creating Remotia.dmg…"
rm -rf dist/dmg dist/Remotia.dmg
mkdir -p dist/dmg
cp -R dist/Remotia.app dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Remotia" -srcfolder dist/dmg -ov -format UDZO dist/Remotia.dmg

echo
echo "Note: this build is ad-hoc signed (free). Users on other Macs should"
echo "right-click the app and choose Open on first launch (or run"
echo "\`xattr -cr /Applications/Remotia.app\`)."
echo "Done:"
echo "  dist/Remotia.app  — drag to /Applications"
echo "  dist/Remotia.dmg  — distributable disk image"
