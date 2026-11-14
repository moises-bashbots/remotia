"""Translate Qt key events into X11 keysyms for the VNC backend."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

# X11 keysym constants (the subset we care about)
_SPECIAL = {
    Qt.Key.Key_Backspace: 0xFF08,
    Qt.Key.Key_Tab: 0xFF09,
    Qt.Key.Key_Backtab: 0xFF09,
    Qt.Key.Key_Return: 0xFF0D,
    Qt.Key.Key_Enter: 0xFF8D,
    Qt.Key.Key_Escape: 0xFF1B,
    Qt.Key.Key_Delete: 0xFFFF,
    Qt.Key.Key_Home: 0xFF50,
    Qt.Key.Key_Left: 0xFF51,
    Qt.Key.Key_Up: 0xFF52,
    Qt.Key.Key_Right: 0xFF53,
    Qt.Key.Key_Down: 0xFF54,
    Qt.Key.Key_PageUp: 0xFF55,
    Qt.Key.Key_PageDown: 0xFF56,
    Qt.Key.Key_End: 0xFF57,
    Qt.Key.Key_Insert: 0xFF63,
    Qt.Key.Key_CapsLock: 0xFFE5,
    Qt.Key.Key_NumLock: 0xFF7F,
    Qt.Key.Key_ScrollLock: 0xFF14,
    Qt.Key.Key_Pause: 0xFF13,
    Qt.Key.Key_Space: 0x20,
    # Modifiers - Meta (Cmd) is mapped to Super so it works on Linux remotes.
    Qt.Key.Key_Shift: 0xFFE1,
    Qt.Key.Key_Control: 0xFFE3,
    Qt.Key.Key_Meta: 0xFFEB,
    Qt.Key.Key_Alt: 0xFFE9,
    # Function keys
    Qt.Key.Key_F1: 0xFFBE,
    Qt.Key.Key_F2: 0xFFBF,
    Qt.Key.Key_F3: 0xFFC0,
    Qt.Key.Key_F4: 0xFFC1,
    Qt.Key.Key_F5: 0xFFC2,
    Qt.Key.Key_F6: 0xFFC3,
    Qt.Key.Key_F7: 0xFFC4,
    Qt.Key.Key_F8: 0xFFC5,
    Qt.Key.Key_F9: 0xFFC6,
    Qt.Key.Key_F10: 0xFFC7,
    Qt.Key.Key_F11: 0xFFC8,
    Qt.Key.Key_F12: 0xFFC9,
    # Keypad
    Qt.Key.Key_0: 0x30,
    Qt.Key.Key_1: 0x31,
    Qt.Key.Key_2: 0x32,
    Qt.Key.Key_3: 0x33,
    Qt.Key.Key_4: 0x34,
    Qt.Key.Key_5: 0x35,
    Qt.Key.Key_6: 0x36,
    Qt.Key.Key_7: 0x37,
    Qt.Key.Key_8: 0x38,
    Qt.Key.Key_9: 0x39,
    Qt.Key.Key_A: 0x61,
    Qt.Key.Key_B: 0x62,
    Qt.Key.Key_C: 0x63,
    Qt.Key.Key_D: 0x64,
    Qt.Key.Key_E: 0x65,
    Qt.Key.Key_F: 0x66,
    Qt.Key.Key_G: 0x67,
    Qt.Key.Key_H: 0x68,
    Qt.Key.Key_I: 0x69,
    Qt.Key.Key_J: 0x6A,
    Qt.Key.Key_K: 0x6B,
    Qt.Key.Key_L: 0x6C,
    Qt.Key.Key_M: 0x6D,
    Qt.Key.Key_N: 0x6E,
    Qt.Key.Key_O: 0x6F,
    Qt.Key.Key_P: 0x70,
    Qt.Key.Key_Q: 0x71,
    Qt.Key.Key_R: 0x72,
    Qt.Key.Key_S: 0x73,
    Qt.Key.Key_T: 0x74,
    Qt.Key.Key_U: 0x75,
    Qt.Key.Key_V: 0x76,
    Qt.Key.Key_W: 0x77,
    Qt.Key.Key_X: 0x78,
    Qt.Key.Key_Y: 0x79,
    Qt.Key.Key_Z: 0x7A,
    # US layout punctuation (unshifted values; Shift is sent separately)
    Qt.Key.Key_Minus: 0x2D,
    Qt.Key.Key_Equal: 0x3D,
    Qt.Key.Key_BracketLeft: 0x5B,
    Qt.Key.Key_BracketRight: 0x5D,
    Qt.Key.Key_Backslash: 0x5C,
    Qt.Key.Key_Semicolon: 0x3B,
    Qt.Key.Key_Apostrophe: 0x27,
    Qt.Key.Key_Comma: 0x2C,
    Qt.Key.Key_Period: 0x2E,
    Qt.Key.Key_Slash: 0x2F,
    Qt.Key.Key_QuoteLeft: 0x60,
}


def qt_key_to_keysym(event: QKeyEvent) -> Optional[int]:
    """Return the X11 keysym for a QKeyEvent, or ``None`` to ignore it."""
    key = event.key()

    if key in _SPECIAL:
        return _SPECIAL[key]

    text = event.text()
    if text and len(text) == 1:
        cp = ord(text)
        if cp < 0x100:
            return cp
        # Unicode keysym for characters outside Latin-1
        return 0x01000000 | cp

    return None
