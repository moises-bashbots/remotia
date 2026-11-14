"""Application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .models import Store
from .ui import MainWindow


def _icon_path() -> str | None:
    import os

    candidates = [
        os.environ.get("REMOTIA_ICON"),
        getattr(sys, "_MEIPASS", None) and str(Path(sys._MEIPASS) / "assets" / "Remotia.png"),
        str(Path(__file__).resolve().parent.parent / "assets" / "Remotia.png"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Remotia")
    app.setOrganizationName("Remotia")

    icon = _icon_path()
    if icon:
        app.setWindowIcon(QIcon(icon))

    store = Store()
    window = MainWindow(store)
    window.show()
    return app.exec()
