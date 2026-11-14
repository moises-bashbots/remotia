# Remotia

A Remmina-like remote desktop client for the desktop, written in Python + Qt.

- **VNC** — fully embedded viewer (renders the remote desktop inside the app,
  forwards keyboard/mouse, clipboard and bell). Supports Raw, CopyRect, RRE,
  Hextile and ZRLE encodings, plus VNC and Apple (ARD) authentication.
- **SSH** — fully embedded terminal (paramiko + pyte) with interactive shell,
  PTY resize and clipboard paste.
- **RDP** — embedded viewer (the remote desktop is rendered inside the app's
  tab). It uses a small native helper built against FreeRDP that renders into
  shared memory, so the pixels are drawn in-app with full mouse/keyboard
  input. Resizing the window resizes the remote session to fit (dynamic
  resolution via the RDP display-control channel). **Clipboard is synced both
  ways**, **remote audio is played locally**, and a connection can share a
  local **folder** with the remote (it appears as `\\tsclient\Remotia` on the
  remote). Falls back to launching FreeRDP / Microsoft Remote Desktop if the
  helper is unavailable.

  > Clipboard is currently text-only; file/image clipboard (copying files
  > between machines) is not wired up yet.

Connection profiles (name, group, server, port, credentials, resolution,
quality…) are stored in SQLite. Passwords are stored in the macOS Keychain
(on Linux they fall back to an obfuscated local file).

## Requirements

- Python 3.10+ (tested with 3.14)
- Qt 6 (installed automatically as `PyQt6`)
- For embedded RDP: FreeRDP (`brew install freerdp`) and a C compiler
  (`xcode-select --install`). `./run.sh` builds the helper automatically.

## Install & run

```bash
./run.sh
```

or manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m remotia
```

## Usage

1. Click **New**, fill in a name, protocol, server, port and credentials.
2. Double-click a connection (or select it and press **Ctrl+Shift+C**) to connect.
3. Each session opens in its own tab — you can open as many concurrent
   sessions as you like. Close a tab to end that session (for RDP this also
   closes the native client window).

The toolbar has a **📁 Share Folder…** button: while an RDP session is active
it lets you pick a local folder and reconnects that session with the folder
shared to the remote (as `\\tsclient\Remotia`). **⏻ Disconnect** closes the
current session tab.

The connections panel floats over the session area (toggle it with the
**☰ Connections** toolbar button, the *View → Connections Panel* menu item,
or **Ctrl+Shift+L**), so showing or hiding it never changes the remote
session's resolution.

Tips:

- VNC display numbers map to ports `5900 + n` (display `:1` → port `5901`).
- For macOS hosts enable *Screen Sharing* and, if you want to use a plain VNC
  password, tick *VNC viewers may control screen with password*.
- For RDP, `brew install freerdp` (its `sdl-freerdp` client runs natively on
  macOS — no XQuartz needed) or install *Microsoft Remote Desktop* from the
  App Store.

## Protocol notes / limitations

- The embedded VNC viewer advertises Raw/CopyRect/RRE/Hextile/ZRLE; servers
  that only offer Tight will still work because they fall back to ZRLE/Hextile.
  Tight and cursor/desktop-resize pseudo-encodings are not implemented yet.
- RDP now renders **inside** the tab via a FreeRDP-based helper; the native
  client launch remains only as a fallback. Tight-style graphics decoding,
  audio, clipboard and multi-monitor are not wired up yet.
- The SSH terminal is monochrome (ANSI colours are on the roadmap).

## Import from Royal TSX

Connections from Royal TSX documents (`.rtsx` / `.rtsz`) can be imported,
including decrypted credentials:

- **GUI:** *File → Import Royal TSX…* (asks for the document password if the
  document is protected).
- **CLI:**

  ```bash
  .venv/bin/python -m remotia.import_royal ~/Documents/RemoteMachines.rtsz
  # add --dry-run to preview, --password … for protected documents
  ```

## Build a macOS app bundle

To package Remotia as a self-contained macOS app (no Python or FreeRDP
needed on the target machine):

```bash
scripts/build_macos_app.sh
```

This produces:

- `dist/Remotia.app` — drag it into `/Applications`.
- `dist/Remotia.dmg` — distributable disk image (with an Applications link).

The bundle includes Python, PyQt, the app, the RDP helper **and** the FreeRDP
libraries, so it runs standalone. It is ad-hoc signed (right-click → Open on
first launch, or sign with your Apple Developer certificate for distribution).

Icons are generated with `scripts/generate_icon.py` (outputs
`assets/Remotia.icns`, `assets/Remotia.png`, and a full-bleed
`assets/AppStore_1024.png` for the App Store listing).

## Distributing on the Mac App Store

See `assets/remotia.entitlements` for the App Sandbox entitlements (sandbox +
outgoing network). An App Store release build is:

```bash
APP_STORE=1 \
SIGN_IDENTITY="Apple Distribution: Your Name (TEAMID)" \
APP_STORE_PROFILE=~/Downloads/remotia.provisionprofile \
scripts/build_macos_app.sh
```

It requires an Apple Developer Program membership, an *Apple Distribution*
certificate, and a macOS App Store provisioning profile created in the Apple
Developer portal. Submission/upload happens in Xcode's Organizer or via
`xcrun altool` / `notarytool` after the bundle is built.

## Tests

```bash
PYTHONPATH=. .venv/bin/python tests/test_decoders.py
```

## License

MIT — see [LICENSE](LICENSE).

Third-party components used at runtime are governed by their own licenses:
PyQt6 (GPLv3 or commercial), FreeRDP (Apache-2.0), Paramiko (LGPL),
Cryptography (Apache-2.0/BSD), Pyte (MIT).
