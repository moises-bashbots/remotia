#!/usr/bin/env python3
"""Generate Remotia app icons (PNG + .icns).

Uses Qt to render a monitor-with-globe mark on a gradient, then iconutil to
build the macOS .icns.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)


def draw_icon(size: int, rounded: bool = True) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    if rounded:
        radius = size * 0.225
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        p.setClipPath(clip)

    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor("#4338CA"))
    grad.setColorAt(1.0, QColor("#0EA5E9"))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(0, 0, size, size)

    s = size / 1024.0

    def R(x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(x * s, y * s, w * s, h * s)

    # Monitor (white)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(R(212, 252, 600, 396), 44 * s, 44 * s)
    p.drawRoundedRect(R(472, 660, 80, 78), 18 * s, 18 * s)
    p.drawRoundedRect(R(398, 750, 228, 56), 20 * s, 20 * s)

    # Globe inside the screen
    cx, cy, r = 512 * s, 450 * s, 128 * s
    p.setBrush(QColor("#1D4ED8"))
    p.drawEllipse(QPointF(cx, cy), r, r)

    p.setPen(QPen(QColor(255, 255, 255, 205), max(2, int(13 * s))))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r * 0.46, r)  # meridian
    p.drawEllipse(QPointF(cx, cy), r, r * 0.46)  # meridian
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))  # equator

    p.end()
    return img


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    assets = root / "assets"
    assets.mkdir(exist_ok=True)

    # Full-bleed, opaque 1024 for the App Store listing (system rounds it).
    draw_icon(1024, rounded=False).save(str(assets / "AppStore_1024.png"))

    # Window icon (used by the running app).
    draw_icon(512, rounded=True).save(str(assets / "Remotia.png"))

    # .iconset for iconutil
    iconset = assets / "Remotia.iconset"
    iconset.mkdir(exist_ok=True)
    pairs = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, size in pairs.items():
        draw_icon(size, rounded=True).save(str(iconset / name))

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "Remotia.icns")],
        check=True,
    )
    print(f"Wrote {assets / 'Remotia.icns'}")
    print(f"Wrote {assets / 'Remotia.png'}")
    print(f"Wrote {assets / 'AppStore_1024.png'}")


if __name__ == "__main__":
    main()
