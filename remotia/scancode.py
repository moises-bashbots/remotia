"""Translate Qt key events into RDP scan codes (Windows Set 1).

RDP keyboard input uses Windows Set-1 scan codes; extended keys (arrows,
Home/End/PageUp/PageDown/Insert/Delete, the Windows key, …) must OR in
``KBD_FLAGS_EXTENDED`` (0x0100).  The result is the ``rdp_scancode`` passed to
``freerdp_input_send_keyboard_event_ex``.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

KBD_FLAGS_EXTENDED = 0x0100

# Qt.Key -> (scancode, extended)
_SPECIAL = {
    Qt.Key.Key_Escape: (0x01, False),
    Qt.Key.Key_Backspace: (0x0E, False),
    Qt.Key.Key_Tab: (0x0F, False),
    Qt.Key.Key_Return: (0x1C, False),
    Qt.Key.Key_Enter: (0x1C, False),
    Qt.Key.Key_Control: (0x1D, False),
    Qt.Key.Key_Shift: (0x2A, False),
    Qt.Key.Key_Alt: (0x38, False),
    Qt.Key.Key_Space: (0x39, False),
    Qt.Key.Key_CapsLock: (0x3A, False),
    Qt.Key.Key_Meta: (0x5B, True),  # Windows key
    Qt.Key.Key_Minus: (0x0C, False),
    Qt.Key.Key_Equal: (0x0D, False),
    Qt.Key.Key_BracketLeft: (0x1A, False),
    Qt.Key.Key_BracketRight: (0x1B, False),
    Qt.Key.Key_Backslash: (0x2B, False),
    Qt.Key.Key_Semicolon: (0x27, False),
    Qt.Key.Key_Apostrophe: (0x28, False),
    Qt.Key.Key_QuoteLeft: (0x29, False),
    Qt.Key.Key_Comma: (0x33, False),
    Qt.Key.Key_Period: (0x34, False),
    Qt.Key.Key_Slash: (0x35, False),
    Qt.Key.Key_NumLock: (0x45, False),
    Qt.Key.Key_ScrollLock: (0x46, False),
    # Extended keys
    Qt.Key.Key_Home: (0x47, True),
    Qt.Key.Key_Up: (0x48, True),
    Qt.Key.Key_PageUp: (0x49, True),
    Qt.Key.Key_Left: (0x4B, True),
    Qt.Key.Key_Right: (0x4D, True),
    Qt.Key.Key_End: (0x4F, True),
    Qt.Key.Key_Down: (0x50, True),
    Qt.Key.Key_PageDown: (0x51, True),
    Qt.Key.Key_Insert: (0x52, True),
    Qt.Key.Key_Delete: (0x53, True),
    # Function keys
    Qt.Key.Key_F1: (0x3B, False),
    Qt.Key.Key_F2: (0x3C, False),
    Qt.Key.Key_F3: (0x3D, False),
    Qt.Key.Key_F4: (0x3E, False),
    Qt.Key.Key_F5: (0x3F, False),
    Qt.Key.Key_F6: (0x40, False),
    Qt.Key.Key_F7: (0x41, False),
    Qt.Key.Key_F8: (0x42, False),
    Qt.Key.Key_F9: (0x43, False),
    Qt.Key.Key_F10: (0x44, False),
    Qt.Key.Key_F11: (0x57, False),
    Qt.Key.Key_F12: (0x58, False),
}

# Digits 1..9, 0 -> scan codes 0x02..0x0B
for i in range(10):
    _SPECIAL[getattr(Qt.Key, f"Key_{i}")] = (0x02 + (i + 9) % 10, False)

# Letters qwerty layout -> Set-1 scan codes
_LETTERS = {
    Qt.Key.Key_Q: 0x10, Qt.Key.Key_W: 0x11, Qt.Key.Key_E: 0x12, Qt.Key.Key_R: 0x13,
    Qt.Key.Key_T: 0x14, Qt.Key.Key_Y: 0x15, Qt.Key.Key_U: 0x16, Qt.Key.Key_I: 0x17,
    Qt.Key.Key_O: 0x18, Qt.Key.Key_P: 0x19,
    Qt.Key.Key_A: 0x1E, Qt.Key.Key_S: 0x1F, Qt.Key.Key_D: 0x20, Qt.Key.Key_F: 0x21,
    Qt.Key.Key_G: 0x22, Qt.Key.Key_H: 0x23, Qt.Key.Key_J: 0x24, Qt.Key.Key_K: 0x25,
    Qt.Key.Key_L: 0x26,
    Qt.Key.Key_Z: 0x2C, Qt.Key.Key_X: 0x2D, Qt.Key.Key_C: 0x2E, Qt.Key.Key_V: 0x2F,
    Qt.Key.Key_B: 0x30, Qt.Key.Key_N: 0x31, Qt.Key.Key_M: 0x32,
}
for key, code in _LETTERS.items():
    _SPECIAL[key] = (code, False)


def qt_key_to_rdp_scancode(event: QKeyEvent) -> Optional[int]:
    """Return the RDP scan code for a key event, or ``None`` if unmapped."""
    key = event.key()
    if key in _SPECIAL:
        scancode, extended = _SPECIAL[key]
        return scancode | (KBD_FLAGS_EXTENDED if extended else 0)
    return None


def qt_key_to_unicode(event: QKeyEvent) -> Optional[int]:
    """Return a Unicode code point for printable text (fallback input path)."""
    text = event.text()
    if text and len(text) == 1 and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
        return ord(text)
    return None
