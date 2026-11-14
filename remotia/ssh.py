"""Embedded SSH terminal using paramiko + pyte."""

from __future__ import annotations

import threading
from typing import Optional

import paramiko
import pyte
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QWidget

_TERM_STYLE = """
QPlainTextEdit {
    background-color: #1d1f21;
    color: #e8e8e8;
    border: none;
    selection-background-color: #3a3d41;
    padding: 4px;
}
"""


def key_event_to_bytes(event: QKeyEvent) -> bytes:
    """Translate a QKeyEvent into the byte sequence sent to a PTY."""
    key = int(event.key())
    mods = event.modifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)

    if key in (int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)):
        return b"\r"
    if key == int(Qt.Key.Key_Backspace):
        return b"\x7f"
    if key == int(Qt.Key.Key_Tab):
        return b"\x1b[Z" if shift else b"\t"
    if key == int(Qt.Key.Key_Escape):
        return b"\x1b"

    # Ctrl+letter -> control character
    if ctrl and not alt and int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        return bytes([key - int(Qt.Key.Key_A) + 1])

    mod_code = 0
    if shift:
        mod_code = 2
    if alt:
        mod_code = 3 if mod_code == 0 else mod_code + 1
    if ctrl:
        mod_code = 5 if mod_code == 0 else mod_code + 4

    cursor = {
        int(Qt.Key.Key_Up): "A",
        int(Qt.Key.Key_Down): "B",
        int(Qt.Key.Key_Right): "C",
        int(Qt.Key.Key_Left): "D",
        int(Qt.Key.Key_Home): "H",
        int(Qt.Key.Key_End): "F",
    }
    if key in cursor:
        code = cursor[key]
        if mod_code:
            return f"\x1b[1;{mod_code}{code}".encode()
        return f"\x1b[{code}".encode()

    if key == int(Qt.Key.Key_Delete):
        return f"\x1b[3;{mod_code}~".encode() if mod_code else b"\x1b[3~"
    if key == int(Qt.Key.Key_Insert):
        return f"\x1b[2;{mod_code}~".encode() if mod_code else b"\x1b[2~"
    if key == int(Qt.Key.Key_PageUp):
        return f"\x1b[5;{mod_code}~".encode() if mod_code else b"\x1b[5~"
    if key == int(Qt.Key.Key_PageDown):
        return f"\x1b[6;{mod_code}~".encode() if mod_code else b"\x1b[6~"

    fkeys = {
        int(Qt.Key.Key_F1): b"\x1bOP",
        int(Qt.Key.Key_F2): b"\x1bOQ",
        int(Qt.Key.Key_F3): b"\x1bOR",
        int(Qt.Key.Key_F4): b"\x1bOS",
        int(Qt.Key.Key_F5): b"\x1b[15~",
        int(Qt.Key.Key_F6): b"\x1b[17~",
        int(Qt.Key.Key_F7): b"\x1b[18~",
        int(Qt.Key.Key_F8): b"\x1b[19~",
        int(Qt.Key.Key_F9): b"\x1b[20~",
        int(Qt.Key.Key_F10): b"\x1b[21~",
        int(Qt.Key.Key_F11): b"\x1b[23~",
        int(Qt.Key.Key_F12): b"\x1b[24~",
    }
    if key in fkeys:
        return fkeys[key]

    text = event.text()
    if text and not ctrl:
        data = text.encode("utf-8")
        if alt:
            return b"\x1b" + data
        return data
    return b""


class SshSession(QObject):
    connected = pyqtSignal(int, int)  # cols, rows
    data_received = pyqtSignal(bytes)
    failed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._channel: Optional[paramiko.Channel] = None
        self._client: Optional[paramiko.SSHClient] = None

    @property
    def channel(self) -> Optional[paramiko.Channel]:
        return self._channel

    def start(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        private_key: str,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        self._thread = threading.Thread(
            target=self._run,
            args=(host, port, username, password, private_key, cols, rows),
            daemon=True,
        )
        self._thread.start()

    def _run(self, host, port, username, password, private_key, cols, rows) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                host,
                port=port,
                username=username or None,
                password=password or None,
                key_filename=private_key or None,
                timeout=15,
                allow_agent=True,
                look_for_keys=True,
                banner_timeout=15,
                auth_timeout=15,
            )
            channel = client.invoke_shell(width=cols, height=rows, term="xterm-256color")
            channel.settimeout(0.0)
            self._channel = channel
            self._client = client
            self.connected.emit(cols, rows)
            while not channel.closed:
                try:
                    data = channel.recv(4096)
                except Exception:  # noqa: BLE001
                    break
                if not data:
                    break
                self.data_received.emit(data)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self._channel = None
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            self.closed.emit()

    def send(self, data: bytes) -> None:
        if self._channel is not None and not self._channel.closed:
            try:
                self._channel.send(data)
            except Exception:  # noqa: BLE001
                pass

    def resize_pty(self, cols: int, rows: int) -> None:
        if self._channel is not None and not self._channel.closed:
            try:
                self._channel.resize_pty(width=cols, height=rows)
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


class SshView(QPlainTextEdit):
    """Terminal widget for an SSH session."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setStyleSheet(_TERM_STYLE)
        font = QFont("Menlo", 13)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        self.setFont(font)

        self._screen = pyte.Screen(80, 24)
        self._stream = pyte.ByteStream(self._screen)
        self._session = SshSession()
        self._session.data_received.connect(self._feed)
        self._session.connected.connect(self._on_connected)
        self._last_text = ""

    @property
    def session(self) -> SshSession:
        return self._session

    def stop(self) -> None:
        self._session.stop()

    def connect_to(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        private_key: str,
    ) -> None:
        cols, rows = self._current_size()
        self._screen.resize(lines=rows, columns=cols)
        self._session.start(host, port, username, password, private_key, cols, rows)

    def _current_size(self) -> tuple[int, int]:
        fm = self.fontMetrics()
        cols = max(20, self.viewport().width() // max(1, fm.horizontalAdvance("M")))
        rows = max(5, self.viewport().height() // max(1, fm.height()))
        return cols, rows

    def _on_connected(self, cols: int, rows: int) -> None:
        self._screen.resize(lines=rows, columns=cols)
        self._render()

    def _feed(self, data: bytes) -> None:
        try:
            self._stream.feed(data)
        except Exception:  # noqa: BLE001
            pass
        self._render()

    def _render(self) -> None:
        text = "\n".join(self._screen.display)
        if text != self._last_text:
            self._last_text = text
            self.setPlainText(text)
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._session.channel is not None:
            cols, rows = self._current_size()
            self._screen.resize(lines=rows, columns=cols)
            self._session.resize_pty(cols, rows)
            self._render()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        from PyQt6.QtGui import QKeySequence

        # Let the widget handle copy/select-all, and paste the clipboard.
        if event.matches(QKeySequence.StandardKey.Paste):
            text = QApplication.clipboard().text()
            if text:
                self._session.send(text.encode("utf-8"))
            return
        if event.matches(QKeySequence.StandardKey.Copy) or event.matches(
            QKeySequence.StandardKey.SelectAll
        ):
            super().keyPressEvent(event)
            return

        if self._session.channel is None:
            return
        data = key_event_to_bytes(event)
        if data:
            self._session.send(data)
