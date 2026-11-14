"""Embedded VNC client: asyncio protocol thread + Qt render widget."""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from PyQt6.QtCore import QObject, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from . import rfb
from .keysym import qt_key_to_keysym

BUTTON_LEFT = 1
BUTTON_MIDDLE = 2
BUTTON_RIGHT = 4


class VncSession(QObject):
    """Runs the RFB client in a background thread and bridges to Qt signals."""

    connected = pyqtSignal(int, int, str)
    updated = pyqtSignal(int, int, int, int, bytes)
    clipboard_changed = pyqtSignal(str)
    bell = pyqtSignal()
    failed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[rfb.RFBClient] = None

    @property
    def is_running(self) -> bool:
        return self._client is not None

    def start(self, host: str, port: int, username: str, password: str) -> None:
        self._thread = threading.Thread(
            target=self._run, args=(host, port, username, password), daemon=True
        )
        self._thread.start()

    def _run(self, host: str, port: int, username: str, password: str) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main(host, port, username, password))
        except rfb.RFBError as exc:
            self.failed.emit(str(exc))
        except OSError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc}")
        finally:
            self._client = None
            try:
                loop.close()
            finally:
                self.closed.emit()

    async def _async_main(self, host: str, port: int, username: str, password: str) -> None:
        client = await rfb.connect_rfb(
            host, port,
            username or None,
            password or None,
        )
        self._client = client
        self.connected.emit(client.width, client.height, client.name)
        await client.run(
            on_update=lambda x, y, w, h, data: self.updated.emit(x, y, w, h, data),
            on_clipboard=self.clipboard_changed.emit,
            on_bell=self.bell.emit,
        )

    # -- input (called from the GUI thread, marshalled onto the loop) -------
    def _call(self, fn, *args) -> None:
        if self._loop is not None and self._client is not None:
            self._loop.call_soon_threadsafe(fn, *args)

    def key_event(self, keysym: int, down: bool) -> None:
        if self._client is not None:
            self._call(self._client.key_event, keysym, down)

    def pointer_event(self, mask: int, x: int, y: int) -> None:
        if self._client is not None:
            self._call(self._client.pointer_event, mask, x, y)

    def send_clipboard(self, text: str) -> None:
        if self._client is not None:
            self._call(self._client.send_clipboard, text)

    def request_refresh(self) -> None:
        if self._client is not None:
            self._call(self._client._request_update, False)

    def stop(self) -> None:
        if self._client is not None:
            self._call(self._client.close)


class VncView(QWidget):
    """Widget that renders the remote framebuffer and forwards input."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)

        self.session = VncSession()
        self.session.connected.connect(self._on_connected)
        self.session.updated.connect(self._on_update)
        self.session.bell.connect(self._on_bell)
        self.session.clipboard_changed.connect(self._on_clipboard)

        self._fb = bytearray()
        self._fb_w = 0
        self._fb_h = 0
        self._pixmap: Optional[QPixmap] = None
        self._dirty = QRect()
        self._button_mask = 0
        self._connected = False
        self._scale = True

    # -- public API ---------------------------------------------------------
    def connect_to(self, host: str, port: int, username: str, password: str) -> None:
        self._clipboard_suppress = False
        QApplication.clipboard().dataChanged.connect(self._on_local_clipboard_changed)
        self.session.start(host, port, username, password)

    def stop(self) -> None:
        self.session.stop()

    def set_scaling(self, enabled: bool) -> None:
        self._scale = enabled
        self.update()

    def send_local_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if text:
            self.session.send_clipboard(text)

    def _on_local_clipboard_changed(self) -> None:
        if not getattr(self, "_clipboard_suppress", False):
            self.send_local_clipboard()

    def _set_clipboard(self, text: str) -> None:
        self._clipboard_suppress = True
        try:
            QApplication.clipboard().setText(text)
        finally:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: setattr(self, "_clipboard_suppress", False))

    # -- internal slots -----------------------------------------------------
    def _on_connected(self, width: int, height: int, name: str) -> None:
        self._fb_w = width
        self._fb_h = height
        self._fb = bytearray(b"\x00\x00\x00\xff") * (width * height)
        self._pixmap = QPixmap(width, height)
        self._pixmap.fill(Qt.GlobalColor.black)
        self._dirty = QRect(0, 0, width, height)
        self._connected = True
        self.update()

    def _on_update(self, x: int, y: int, w: int, h: int, data: bytes) -> None:
        if not self._connected or w <= 0 or h <= 0:
            return
        stride = self._fb_w * 4
        row = w * 4
        for r in range(h):
            off = (y + r) * stride + x * 4
            self._fb[off : off + row] = data[r * row : (r + 1) * row]
        rect = QRect(x, y, w, h)
        self._dirty = rect if self._dirty.isNull() else self._dirty.united(rect)
        self.update()

    def _on_bell(self) -> None:
        QApplication.beep()

    def _on_clipboard(self, text: str) -> None:
        self._set_clipboard(text)

    # -- painting -----------------------------------------------------------
    def _fit_rect(self) -> QRect:
        if not self._scale or self._pixmap is None:
            return QRect(0, 0, self._pixmap.width(), self._pixmap.height())
        widget = self.rect()
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return widget
        scale = min(widget.width() / pw, widget.height() / ph)
        w = int(pw * scale)
        h = int(ph * scale)
        x = (widget.width() - w) // 2
        y = (widget.height() - h) // 2
        return QRect(x, y, w, h)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._pixmap is None:
            painter.end()
            return

        if not self._dirty.isNull():
            rect = self._dirty.intersected(QRect(0, 0, self._fb_w, self._fb_h))
            if not rect.isNull():
                data = self._region_rgba(rect)
                img = QImage(
                    data,
                    rect.width(),
                    rect.height(),
                    rect.width() * 4,
                    QImage.Format.Format_RGBA8888,
                )
                pp = QPainter(self._pixmap)
                pp.drawImage(rect.x(), rect.y(), img)
                pp.end()
            self._dirty = QRect()

        target = self._fit_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target, self._pixmap)
        painter.end()

    def _region_rgba(self, rect: QRect) -> bytes:
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        stride = self._fb_w * 4
        row = w * 4
        out = bytearray(row * h)
        for r in range(h):
            off = (y + r) * stride + x * 4
            out[r * row : (r + 1) * row] = self._fb[off : off + row]
        return bytes(out)

    # -- input --------------------------------------------------------------
    def _to_framebuffer(self, pos) -> tuple[int, int]:
        target = self._fit_rect()
        if self._scale and self._pixmap and target.width() and target.height():
            fx = (pos.x() - target.x()) * self._fb_w / target.width()
            fy = (pos.y() - target.y()) * self._fb_h / target.height()
        else:
            fx, fy = pos.x(), pos.y()
        fx = max(0, min(self._fb_w - 1, int(fx)))
        fy = max(0, min(self._fb_h - 1, int(fy)))
        return fx, fy

    def _update_mask(self, button: Qt.MouseButton, pressed: bool) -> None:
        if button == Qt.MouseButton.LeftButton:
            bit = BUTTON_LEFT
        elif button == Qt.MouseButton.RightButton:
            bit = BUTTON_RIGHT
        elif button == Qt.MouseButton.MiddleButton:
            bit = BUTTON_MIDDLE
        else:
            return
        if pressed:
            self._button_mask |= bit
        else:
            self._button_mask &= ~bit

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._update_mask(event.button(), True)
        fx, fy = self._to_framebuffer(event.position().toPoint())
        self.session.pointer_event(self._button_mask, fx, fy)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._update_mask(event.button(), False)
        fx, fy = self._to_framebuffer(event.position().toPoint())
        self.session.pointer_event(self._button_mask, fx, fy)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._connected and self._button_mask:
            fx, fy = self._to_framebuffer(event.position().toPoint())
            self.session.pointer_event(self._button_mask, fx, fy)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._connected:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        fx, fy = self._to_framebuffer(event.position().toPoint())
        for _ in range(max(1, abs(delta) // 120)):
            mask = self._button_mask | (8 if delta > 0 else 16)
            self.session.pointer_event(mask, fx, fy)
            self.session.pointer_event(self._button_mask, fx, fy)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._connected or event.isAutoRepeat():
            return
        keysym = qt_key_to_keysym(event)
        if keysym is not None:
            self.session.key_event(keysym, True)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._connected or event.isAutoRepeat():
            return
        keysym = qt_key_to_keysym(event)
        if keysym is not None:
            self.session.key_event(keysym, False)
