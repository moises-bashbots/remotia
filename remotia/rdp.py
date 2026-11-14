"""RDP support.

A pure-Python RDP renderer is not practical, so RDP sessions are handed off
to the best native client available: FreeRDP if it is installed, otherwise
Microsoft Remote Desktop (macOS) via a generated ``.rdp`` file.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import Connection


def _size_args(resolution: str, color_depth: int) -> tuple[str, str]:
    width, height = 0, 0
    if resolution and resolution != "auto":
        try:
            width, height = (int(v) for v in resolution.lower().split("x", 1))
        except ValueError:
            width = height = 0
    return str(width), str(height), str(max(8, min(32, color_depth or 24)))


def _free_rdp_path() -> str | None:
    # Prefer the SDL client (native macOS), then X11/Wayland variants.
    return (
        shutil.which("sdl-freerdp")
        or shutil.which("xfreerdp")
        or shutil.which("wlfreerdp")
        or shutil.which("freerdp")
    )


def build_rdp_file(conn: Connection) -> str:
    """Return the contents of a .rdp file for the connection."""
    width, height, bpp = _size_args(conn.resolution, conn.color_depth)
    lines = [
        "screen mode id:i:2",
        "use multimon:i:0",
        "session bpp:i:" + bpp,
        "full address:s:" + conn.server + ":" + str(conn.effective_port()),
        "prompt for credentials:i:0",
    ]
    if width != "0" and height != "0":
        lines.append("desktopwidth:i:" + width)
        lines.append("desktopheight:i:" + height)
    if conn.username:
        lines.append("username:s:" + conn.username)
    if conn.domain:
        lines.append("domain:s:" + conn.domain)
    return "\n".join(lines) + "\n"


def launch_rdp(conn: Connection):
    """Launch the connection in a native RDP client.

    Returns the ``subprocess.Popen`` handle when FreeRDP is used, otherwise
    ``None`` (Microsoft Remote Desktop via ``open``).
    """
    host = conn.server
    port = conn.effective_port()
    width, height, bpp = _size_args(conn.resolution, conn.color_depth)

    freerdp = _free_rdp_path()
    if freerdp:
        args = [freerdp, f"/v:{host}:{port}"]
        if conn.username:
            args.append(f"/u:{conn.username}")
        if conn.password:
            args.append(f"/p:{conn.password}")
        if conn.domain:
            args.append(f"/d:{conn.domain}")
        if width != "0":
            args.append(f"/size:{width}x{height}")
        args += ["/bpp:" + bpp, "/cert:ignore", "/dynamic-resolution"]
        if conn.extra_args:
            args += conn.extra_args.split()
        return subprocess.Popen(args, start_new_session=True)

    # Fall back to Microsoft Remote Desktop (macOS) via a generated .rdp file.
    rdp = build_rdp_file(conn)
    fd, path = tempfile.mkstemp(suffix=".rdp", prefix="remotia-")
    with open(fd, "w", encoding="utf-8") as fh:
        fh.write(rdp)
    try:
        subprocess.run(["open", path], check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Could not open an RDP client. Install FreeRDP "
            "(brew install freerdp) or Microsoft Remote Desktop from the App Store."
        ) from exc
    finally:
        # Give the app a moment to read the file before removing it.
        Path(path).unlink(missing_ok=True)
    return None
