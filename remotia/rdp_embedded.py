"""Embedded RDP viewer widget.

Launches the native ``rdp_helper`` process, displays the shared-memory
framebuffer it renders into, and forwards mouse/keyboard events.
"""

from __future__ import annotations

import mmap
import os
import struct
import tempfile
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PyQt6.QtWidgets import QApplication, QWidget

from .models import Connection
from .scancode import qt_key_to_rdp_scancode, qt_key_to_unicode

SHM_HEADER = 64
MAX_FB = 4096 * 2160 * 4  # 33 MB, generous enough for any desktop

# Pointer flags (FreeRDP)
PTR_FLAGS_WHEEL = 0x0200
PTR_FLAGS_WHEEL_NEGATIVE = 0x0100
PTR_FLAGS_MOVE = 0x0800
PTR_FLAGS_DOWN = 0x8000
PTR_FLAGS_BUTTON1 = 0x1000
PTR_FLAGS_BUTTON2 = 0x2000
PTR_FLAGS_BUTTON3 = 0x4000


def helper_path() -> Optional[str]:
    """Locate the compiled rdp_helper binary (dev tree or app bundle)."""
    import sys

    candidates = [
        os.environ.get("REMOTIA_RDP_HELPER"),
        # PyInstaller bundle
        getattr(sys, "_MEIPASS", None) and Path(sys._MEIPASS) / "rdp_helper",
        Path(sys.executable).resolve().parent / "rdp_helper",
        # Source tree layout
        Path(__file__).resolve().parent.parent / "native" / "rdp_helper",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


class EmbeddedRdpView(QWidget):
    """Renders an RDP session inside the Remotia window."""

    failed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._proc: Optional[QProcess] = None
        self._mm: Optional[mmap.mmap] = None
        self._shm_path: Optional[str] = None
        self._fb_w = 0
        self._fb_h = 0
        self._stride = 0
        self._connected = False
        self._button_mask = 0
        self._status_text = "Connecting…"
        self._scale = True
        self._error_reported = False
        self._stderr_buf = bytearray()
        self._cursor_x = 0
        self._cursor_y = 0
        self._cursor_visible = False
        self._mouse_pos = None

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(400)
        self._resize_timer.timeout.connect(self._send_resize)

        self._clipboard_suppress = False
        self._stdout_buf = bytearray()
        self._audio_sink = None
        self._audio_io = None

    # -- lifecycle ----------------------------------------------------------
    def connect_to(self, conn: Connection) -> None:
        path = helper_path()
        if path is None:
            self.failed.emit("rdp_helper binary not found (run scripts/build_rdp_helper.sh)")
            return

        width, height = self._resolve_size(conn)

        fd, shm_path = tempfile.mkstemp(prefix="remotia-rdp-", suffix=".shm")
        os.close(fd)
        with open(shm_path, "r+b") as fh:
            fh.truncate(SHM_HEADER + MAX_FB)
        self._shm_path = shm_path
        self._mm = mmap.mmap(os.open(shm_path, os.O_RDWR), 0)

        proc = QProcess(self)
        proc.setProgram(path)
        args = [
            conn.server,
            str(conn.effective_port()),
            conn.username or "",
            conn.password or "",
            str(width),
            str(height),
            shm_path,
        ]
        if conn.shared_folder:
            args.append(conn.shared_folder)
        proc.setArguments(args)
        proc.readyReadStandardOutput.connect(self._on_notify)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_process_error)
        proc.start()
        self._proc = proc
        self._clipboard_suppress = False
        self._stdout_buf = bytearray()
        QApplication.clipboard().dataChanged.connect(self._on_local_clipboard_changed)
        # Push the current local clipboard so the remote can paste right away.
        QTimer.singleShot(1500, self._push_local_clipboard)

    def stop(self) -> None:
        self._stop_audio()
        if self._proc is None:
            self._cleanup()
            return
        try:
            self._proc.write(b"X")
        except Exception:  # noqa: BLE001
            pass
        # FreeRDP handles SIGTERM gracefully; give it a moment, then escalate.
        QTimer.singleShot(500, self._terminate_if_running)
        QTimer.singleShot(2500, self._kill_if_running)

    def set_scaling(self, enabled: bool) -> None:
        self._scale = enabled
        self.update()

    def _terminate_if_running(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.terminate()
            QTimer.singleShot(2000, self._kill_if_running)

    def _kill_if_running(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()

    def _cleanup(self) -> None:
        if self._mm is not None:
            try:
                self._mm.close()
            except Exception:  # noqa: BLE001
                pass
            self._mm = None
        if self._shm_path and os.path.exists(self._shm_path):
            try:
                os.unlink(self._shm_path)
            except OSError:
                pass
            self._shm_path = None

    # -- process callbacks --------------------------------------------------
    def _on_notify(self) -> None:
        if self._proc is not None:
            self._stdout_buf += bytes(self._proc.readAllStandardOutput())
        self._parse_stdout()

    def _parse_stdout(self) -> None:
        buf = self._stdout_buf
        while len(buf) >= 1:
            tag = buf[0]
            if tag == 0x01:  # frame ready
                del buf[0]
                self._refresh()
            elif tag == 0x02:  # clipboard data: u32 length + utf-16le
                if len(buf) < 5:
                    break
                length = struct.unpack_from(">I", buf, 1)[0]
                if len(buf) < 5 + length:
                    break
                data = bytes(buf[5 : 5 + length])
                del buf[: 5 + length]
                text = data.decode("utf-16-le", "replace").rstrip("\x00")
                self._on_remote_clipboard(text)
            elif tag == 0x03:  # audio format
                if len(buf) < 11:
                    break
                format_tag = struct.unpack_from("<H", buf, 1)[0]
                channels = struct.unpack_from("<H", buf, 3)[0]
                rate = struct.unpack_from("<I", buf, 5)[0]
                bits = struct.unpack_from("<H", buf, 9)[0]
                del buf[:11]
                self._start_audio(format_tag, channels, rate, bits)
            elif tag == 0x04:  # audio data
                if len(buf) < 5:
                    break
                length = struct.unpack_from(">I", buf, 1)[0]
                if len(buf) < 5 + length:
                    break
                data = bytes(buf[5 : 5 + length])
                del buf[: 5 + length]
                self._play_audio(data)
            else:
                del buf[0]

    def _on_stderr(self) -> None:
        if self._proc is not None:
            self._stderr_buf += bytes(self._proc.readAllStandardError())

    @staticmethod
    def _error_message(code: int) -> str:
        return {
            0x00020014: "Authentication failed (wrong username or password)",
            0x00020015: "Account is locked out",
            0x00020016: "Password has expired",
            0x00020009: "Connection failed (host unreachable or RDP disabled)",
            0x0002000D: "Connection failed (network error)",
            0x0002000B: "Connection cancelled",
        }.get(code, f"RDP error 0x{code:08X}")

    def _refresh(self) -> None:
        if self._mm is None:
            return
        mm = self._mm
        if mm[0:4] != b"RTDP":
            return
        w, h = struct.unpack_from("<II", mm, 4)
        stride = struct.unpack_from("<I", mm, 12)[0]
        status = struct.unpack_from("<I", mm, 24)[0]
        error = struct.unpack_from("<I", mm, 28)[0]
        if w > 0 and h > 0:
            self._fb_w, self._fb_h, self._stride = w, h, stride
        self._cursor_x = struct.unpack_from("<I", mm, 32)[0]
        self._cursor_y = struct.unpack_from("<I", mm, 36)[0]
        self._cursor_visible = struct.unpack_from("<I", mm, 40)[0] != 0
        if status == 1:
            was = self._connected
            self._connected = True
            self._status_text = ""
            if not was:
                # Sync the remote resolution to the window size. Windows may
                # ignore the first layout request, so retry a few times.
                for delay in (1000, 2000, 4000):
                    QTimer.singleShot(delay, self._send_resize)
        elif status == 3:
            self._connected = False
            self._status_text = self._error_message(error)
            if not self._error_reported:
                self._error_reported = True
                self.failed.emit(self._status_text)
        self.update()

    def _on_finished(self, exit_code: int, exit_status) -> None:
        self._stop_audio()
        if not self._connected and not self._error_reported and exit_code != 0:
            self._error_reported = True
            self.failed.emit(self._error_message(exit_code))
        self._connected = False
        self._proc = None
        self._cleanup()
        self.update()
        self.closed.emit()

    def _on_process_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            if not self._error_reported:
                self._error_reported = True
                self.failed.emit("Could not start rdp_helper (build it with scripts/build_rdp_helper.sh)")
        elif error == QProcess.ProcessError.Crashed:
            if not self._error_reported:
                self._error_reported = True
                self.failed.emit("rdp_helper crashed")

    # -- size / painting ----------------------------------------------------
    def _resolve_size(self, conn: Connection) -> tuple[int, int]:
        res = (conn.resolution or "auto").strip().lower()
        if res and res != "auto":
            try:
                w, h = (int(v) for v in res.split("x", 1))
                return max(320, min(3840, w)), max(240, min(2160, h))
            except ValueError:
                pass
        size = self.size()
        if size.width() > 200 and size.height() > 200:
            return size.width(), size.height()
        return 1280, 800

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._connected:
            self._resize_timer.start()

    def _send_resize(self) -> None:
        """Request the remote desktop to resize to the current window size."""
        if not self._connected or self._proc is None:
            return
        w = max(200, min(8192, self.width()))
        h = max(200, min(8192, self.height()))
        if (w, h) == (self._fb_w, self._fb_h):
            return
        self._send(b"R" + struct.pack(">HH", w, h))

    def _fit_rect(self, w: int, h: int):
        from PyQt6.QtCore import QRect

        widget = self.rect()
        if not self._scale:
            return QRect(0, 0, w, h)
        scale = min(widget.width() / w, widget.height() / h)
        tw, th = int(w * scale), int(h * scale)
        x = (widget.width() - tw) // 2
        y = (widget.height() - th) // 2
        return QRect(x, y, tw, th)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)

        if not self._connected or self._mm is None or self._fb_w <= 0:
            painter.setPen(Qt.GlobalColor.gray)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._status_text)
            painter.end()
            return

        mm = self._mm
        w, h, stride = self._fb_w, self._fb_h, self._stride
        nbytes = stride * h
        if 64 + nbytes > len(mm):
            painter.end()
            return
        data = bytes(mm[64 : 64 + nbytes])
        image = QImage(data, w, h, stride, QImage.Format.Format_RGBA8888)
        target = self._fit_rect(w, h)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, image)
        if self._cursor_visible and self._mouse_pos is None:
            self._draw_cursor(painter, w, h)
        elif self._mouse_pos is not None:
            self._cursor_x, self._cursor_y = self._mouse_pos
            self._draw_cursor(painter, w, h)
        painter.end()

    def _remote_to_widget(self, w: int, h: int, rx: int, ry: int):
        target = self._fit_rect(w, h)
        if target.width() <= 0 or target.height() <= 0:
            return 0, 0
        wx = target.x() + rx * target.width() / w
        wy = target.y() + ry * target.height() / h
        return int(wx), int(wy)

    def _draw_cursor(self, painter: QPainter, w: int, h: int) -> None:
        from PyQt6.QtGui import QColor, QPolygon

        x, y = self._remote_to_widget(w, h, self._cursor_x, self._cursor_y)
        # Classic arrow cursor (16x18 at 100%), scaled by the fit factor.
        scale = self._fit_rect(w, h).width() / w if w else 1.0
        s = max(1.0, scale)
        pts = [
            (0, 0), (0, 16), (5, 11), (8, 18), (10, 16), (7, 10), (12, 9),
        ]
        polygon = QPolygon()
        for px, py in pts:
            polygon.append(self._qpoint(int(x + px * s), int(y + py * s)))
        painter.setPen(QColor(0, 0, 0))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawPolygon(polygon)

    @staticmethod
    def _qpoint(x: int, y: int):
        from PyQt6.QtCore import QPoint

        return QPoint(x, y)

    # -- input --------------------------------------------------------------
    def _send(self, data: bytes) -> None:
        if self._proc is not None and self._proc.state() == QProcess.ProcessState.Running:
            try:
                self._proc.write(data)
            except Exception:  # noqa: BLE001
                pass

    # -- clipboard ----------------------------------------------------------
    def _push_local_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text or not self._connected:
            return
        payload = text.encode("utf-16-le")
        self._send(b"C" + struct.pack(">I", len(payload)) + payload)

    def _on_local_clipboard_changed(self) -> None:
        if self._clipboard_suppress:
            return
        self._push_local_clipboard()

    def _on_remote_clipboard(self, text: str) -> None:
        self._clipboard_suppress = True
        try:
            QApplication.clipboard().setText(text)
        finally:
            QTimer.singleShot(0, self._clear_clipboard_suppress)

    def _clear_clipboard_suppress(self) -> None:
        self._clipboard_suppress = False

    # -- audio --------------------------------------------------------------
    def _start_audio(self, format_tag: int, channels: int, rate: int, bits: int) -> None:
        from .audio import AudioQueuePlayer

        if format_tag == 1:  # PCM
            is_float = False
            if bits not in (8, 16, 32):
                return
        elif format_tag == 3:  # IEEE float
            is_float = True
            if bits != 32:
                return
        else:
            return
        self._stop_audio()
        self._audio_sink = AudioQueuePlayer(rate, channels, bits, is_float)
        if not self._audio_sink.active:
            self._audio_sink = None

    def _play_audio(self, data: bytes) -> None:
        if self._audio_sink is not None:
            self._audio_sink.write(data)

    def _stop_audio(self) -> None:
        if self._audio_sink is not None:
            try:
                self._audio_sink.stop()
            except Exception:  # noqa: BLE001
                pass
            self._audio_sink = None

    def _to_framebuffer(self, pos) -> tuple[int, int]:
        from PyQt6.QtCore import QRect

        w, h = self._fb_w, self._fb_h
        if w <= 0 or h <= 0:
            return 0, 0
        target = self._fit_rect(w, h)
        if self._scale and target.width() and target.height():
            fx = (pos.x() - target.x()) * w / target.width()
            fy = (pos.y() - target.y()) * h / target.height()
        else:
            fx, fy = pos.x(), pos.y()
        return max(0, min(w - 1, int(fx))), max(0, min(h - 1, int(fy)))

    def _button_flag(self, button: Qt.MouseButton) -> int:
        return {
            Qt.MouseButton.LeftButton: PTR_FLAGS_BUTTON1,
            Qt.MouseButton.RightButton: PTR_FLAGS_BUTTON2,
            Qt.MouseButton.MiddleButton: PTR_FLAGS_BUTTON3,
        }.get(button, 0)

    def _send_mouse(self, flags: int, x: int, y: int) -> None:
        self._send(b"M" + struct.pack(">HHH", flags, x, y))

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        flag = self._button_flag(event.button())
        self._button_mask |= flag
        fx, fy = self._to_framebuffer(event.position().toPoint())
        self._mouse_pos = (fx, fy)
        self._send_mouse(PTR_FLAGS_DOWN | flag, fx, fy)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        flag = self._button_flag(event.button())
        self._button_mask &= ~flag
        fx, fy = self._to_framebuffer(event.position().toPoint())
        self._send_mouse(flag, fx, fy)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._connected:
            fx, fy = self._to_framebuffer(event.position().toPoint())
            self._mouse_pos = (fx, fy)
            self._send_mouse(PTR_FLAGS_MOVE, fx, fy)
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._connected:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        fx, fy = self._to_framebuffer(event.position().toPoint())
        for _ in range(max(1, abs(delta) // 120)):
            if delta > 0:
                flags = PTR_FLAGS_WHEEL | 120
            else:
                flags = PTR_FLAGS_WHEEL | PTR_FLAGS_WHEEL_NEGATIVE | 120
            self._send_mouse(flags, fx, fy)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._connected or event.isAutoRepeat():
            return
        scancode = qt_key_to_rdp_scancode(event)
        if scancode is not None:
            self._send(b"K" + struct.pack(">I", scancode) + b"\x01")
            return
        code = qt_key_to_unicode(event)
        if code is not None:
            self._send(b"U" + struct.pack(">H", code) + b"\x01")

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._connected or event.isAutoRepeat():
            return
        scancode = qt_key_to_rdp_scancode(event)
        if scancode is not None:
            self._send(b"K" + struct.pack(">I", scancode) + b"\x00")
            return
        code = qt_key_to_unicode(event)
        if code is not None:
            self._send(b"U" + struct.pack(">H", code) + b"\x00")
