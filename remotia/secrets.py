"""Credential storage.

On macOS secrets are stored in the login Keychain via the ``security`` CLI.
On other platforms we fall back to a local obfuscated file (with a clear
warning that it is not encrypted).
"""

from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
from pathlib import Path

SERVICE = "remotia"
_FALLBACK_FILE = Path.home() / ".config" / "remotia" / "secrets.json"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def set_secret(account: str, value: str) -> None:
    if _is_macos():
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-a",
                account,
                "-s",
                SERVICE,
                "-w",
                value,
                "-U",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    _fallback_store(account, value)


def get_secret(account: str) -> str:
    if _is_macos():
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.rstrip("\n")
        return ""
    return _fallback_load(account)


def delete_secret(account: str) -> None:
    if _is_macos():
        subprocess.run(
            ["security", "delete-generic-password", "-a", account, "-s", SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    data = _fallback_read()
    data.pop(account, None)
    _fallback_write(data)


# --- fallback (non-macOS) -------------------------------------------------
def _fallback_read() -> dict:
    try:
        raw = json.loads(_FALLBACK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw.get("secrets", {})


def _fallback_write(data: dict) -> None:
    _FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FALLBACK_FILE.write_text(json.dumps({"secrets": data}), encoding="utf-8")


def _fallback_store(account: str, value: str) -> None:
    data = _fallback_read()
    data[account] = base64.b64encode(value.encode("utf-8")).decode("ascii")
    _fallback_write(data)


def _fallback_load(account: str) -> str:
    encoded = _fallback_read().get(account)
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""
