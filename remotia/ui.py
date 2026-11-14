"""Remotia user interface: connection list, editor and session tabs."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import rdp
from .import_royal import import_royal_document
from .models import PROTOCOLS, Connection, Store
from .rdp_embedded import EmbeddedRdpView, helper_path as rdp_helper_path
from .ssh import SshView
from .vnc import VncView

PROTOCOL_LABELS = {"vnc": "VNC", "ssh": "SSH", "rdp": "RDP"}

RESOLUTIONS = [
    "auto",
    "640x480",
    "800x600",
    "1024x768",
    "1280x720",
    "1280x1024",
    "1366x768",
    "1600x900",
    "1920x1080",
    "2560x1440",
    "3840x2160",
]


class ConnectionEditor(QDialog):
    """Dialog for creating/editing a connection profile."""

    def __init__(self, parent: Optional[QWidget] = None, conn: Optional[Connection] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connection" if conn is None else f"Edit {conn.name}")
        self.setMinimumWidth(460)

        form = QFormLayout()

        self.name = QLineEdit()
        self.protocol = QComboBox()
        for proto in PROTOCOLS:
            self.protocol.addItem(PROTOCOL_LABELS[proto], proto)
        self.group = QComboBox()
        self.group.setEditable(True)
        self.server = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setSpecialValueText("default")
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QCheckBox("show")
        self.domain = QLineEdit()
        self.resolution = QComboBox()
        self.resolution.setEditable(True)
        self.resolution.addItems(RESOLUTIONS)
        self.color_depth = QComboBox()
        for depth in ("24", "32", "16", "15"):
            self.color_depth.addItem(depth)
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(0, 9)
        self.quality.setValue(6)
        self.private_key = QLineEdit()
        self.browse_key = QPushButton("Browse…")
        self.extra_args = QLineEdit()
        self.shared_folder = QLineEdit()
        self.browse_shared = QPushButton("Browse…")
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(70)

        pw_row = QHBoxLayout()
        pw_row.addWidget(self.password, 1)
        pw_row.addWidget(self.show_password)
        self.show_password.toggled.connect(
            lambda checked: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )

        key_row = QHBoxLayout()
        key_row.addWidget(self.private_key, 1)
        key_row.addWidget(self.browse_key)
        self.browse_key.clicked.connect(self._browse_key)

        shared_row = QHBoxLayout()
        shared_row.addWidget(self.shared_folder, 1)
        shared_row.addWidget(self.browse_shared)
        self.browse_shared.clicked.connect(self._browse_shared)

        form.addRow("Name", self.name)
        form.addRow("Protocol", self.protocol)
        form.addRow("Group", self.group)
        form.addRow("Server", self.server)
        form.addRow("Port", self.port)
        form.addRow("Username", self.username)
        form.addRow("Password", pw_row)
        form.addRow("Domain (RDP)", self.domain)
        form.addRow("Resolution", self.resolution)
        form.addRow("Color depth (RDP)", self.color_depth)
        form.addRow("Quality", self.quality)
        form.addRow("Private key (SSH)", key_row)
        form.addRow("Shared folder (RDP)", shared_row)
        form.addRow("Extra args", self.extra_args)
        form.addRow("Notes", self.notes)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        self.conn = conn
        if conn is not None:
            self._load(conn)
        else:
            self.protocol.setCurrentIndex(0)
            self.name.setFocus()

        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self._protocol_changed()

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select private key", "", "Key files (*)")
        if path:
            self.private_key.setText(path)

    def _browse_shared(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select folder to share with the remote")
        if path:
            self.shared_folder.setText(path)

    def _protocol_changed(self) -> None:
        proto = self.protocol.currentData()
        is_rdp = proto == "rdp"
        is_ssh = proto == "ssh"
        self.domain.setEnabled(is_rdp)
        self.color_depth.setEnabled(is_rdp)
        self.private_key.setEnabled(is_ssh)
        self.browse_key.setEnabled(is_ssh)
        self.shared_folder.setEnabled(is_rdp)
        self.browse_shared.setEnabled(is_rdp)

    def _load(self, conn: Connection) -> None:
        self.name.setText(conn.name)
        idx = self.protocol.findData(conn.protocol)
        self.protocol.setCurrentIndex(max(0, idx))
        self.group.setCurrentText(conn.group)
        self.server.setText(conn.server)
        self.port.setValue(conn.port or 0)
        self.username.setText(conn.username)
        self.password.setText(conn.password)
        self.domain.setText(conn.domain)
        self.resolution.setCurrentText(conn.resolution or "auto")
        idx = self.color_depth.findText(str(conn.color_depth))
        self.color_depth.setCurrentIndex(max(0, idx))
        self.quality.setValue(conn.quality or 6)
        self.private_key.setText(conn.private_key)
        self.extra_args.setText(conn.extra_args)
        self.shared_folder.setText(conn.shared_folder)
        self.notes.setPlainText(conn.notes)

    def _validate(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a connection name.")
            return
        if not self.server.text().strip():
            QMessageBox.warning(self, "Missing server", "Please enter a server address.")
            return
        self.accept()

    def result_connection(self, conn: Optional[Connection]) -> Connection:
        if conn is None:
            conn = Connection(name="", protocol="vnc", server="")
        conn.name = self.name.text().strip()
        conn.protocol = self.protocol.currentData()
        conn.group = self.group.currentText().strip()
        conn.server = self.server.text().strip()
        conn.port = self.port.value() or None
        conn.username = self.username.text()
        conn.password = self.password.text()
        conn.domain = self.domain.text()
        conn.resolution = self.resolution.currentText().strip() or "auto"
        conn.color_depth = int(self.color_depth.currentText())
        conn.quality = self.quality.value()
        conn.private_key = self.private_key.text()
        conn.extra_args = self.extra_args.text()
        conn.shared_folder = self.shared_folder.text().strip()
        conn.notes = self.notes.toPlainText()
        return conn


class ConnectionList(QWidget):
    connect_requested = pyqtSignal(object)
    new_requested = pyqtSignal()
    edit_requested = pyqtSignal(object)
    duplicate_requested = pyqtSignal(object)
    delete_requested = pyqtSignal(object)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._connections: list[Connection] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self.refresh)

        self.group_filter = QComboBox()
        self.group_filter.currentIndexChanged.connect(self.refresh)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.itemSelectionChanged.connect(self._on_selection)

        buttons = QHBoxLayout()
        self.new_btn = QPushButton("New")
        self.edit_btn = QPushButton("Edit")
        self.dup_btn = QPushButton("Duplicate")
        self.del_btn = QPushButton("Delete")
        for btn in (self.new_btn, self.edit_btn, self.dup_btn, self.del_btn):
            buttons.addWidget(btn)
        self.new_btn.clicked.connect(self.new_requested)
        self.edit_btn.clicked.connect(self._emit_edit)
        self.dup_btn.clicked.connect(self._emit_duplicate)
        self.del_btn.clicked.connect(self._emit_delete)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.search)
        layout.addWidget(self.group_filter)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        self.refresh()

    def set_connections(self, connections: list[Connection]) -> None:
        self._connections = connections
        groups = sorted({c.group for c in connections if c.group})
        current = self.group_filter.currentText()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("All groups")
        self.group_filter.addItems(groups)
        idx = self.group_filter.findText(current)
        self.group_filter.setCurrentIndex(max(0, idx))
        self.group_filter.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        query = self.search.text().strip().lower()
        group = self.group_filter.currentText()
        self.list.clear()
        for conn in self._connections:
            if group not in ("", "All groups") and conn.group != group:
                continue
            haystack = f"{conn.name} {conn.server} {conn.username} {conn.group}".lower()
            if query and query not in haystack:
                continue
            item = QListWidgetItem(
                f"{conn.name}\n{PROTOCOL_LABELS.get(conn.protocol, conn.protocol)} · "
                f"{conn.server}:{conn.effective_port()}"
            )
            item.setData(Qt.ItemDataRole.UserRole, conn)
            self.list.addItem(item)
        self._on_selection()

    def selected(self) -> Optional[Connection]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_selection(self) -> None:
        has = self.selected() is not None
        self.edit_btn.setEnabled(has)
        self.dup_btn.setEnabled(has)
        self.del_btn.setEnabled(has)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        conn = item.data(Qt.ItemDataRole.UserRole)
        if conn:
            self.connect_requested.emit(conn)

    def _emit_edit(self) -> None:
        if self.selected():
            self.edit_requested.emit(self.selected())

    def _emit_duplicate(self) -> None:
        if self.selected():
            self.duplicate_requested.emit(self.selected())

    def _emit_delete(self) -> None:
        if self.selected():
            self.delete_requested.emit(self.selected())


class RdpView(QWidget):
    """A tab representing an RDP session running in a native client window.

    Closing the tab terminates the underlying FreeRDP process.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._proc = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)

        self.label = QLabel("Launching RDP session…")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("color: #aaa; font-size: 15px;")
        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self.label)
        layout.addStretch()

    def launch(self, conn: Connection) -> Optional[str]:
        """Start the session; returns an error message or ``None`` on success."""
        try:
            self._proc = rdp.launch_rdp(conn)
        except Exception as exc:  # noqa: BLE001
            return str(exc)
        if self._proc is not None:
            self.label.setText(
                f"Connected to {conn.server}.\n"
                "The remote desktop opened in a separate window — "
                "closing this tab ends the session."
            )
            self._timer.start()
        else:
            self.label.setText(
                f"Opened {conn.server} in the native RDP client.\n"
                "You can close this tab."
            )
        return None

    def _poll(self) -> None:
        if self._proc is not None and self._proc.poll() is not None:
            self._timer.stop()
            self.label.setText("Session ended.")

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        self._timer.stop()


class MainWindow(QMainWindow):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store

        self.setWindowTitle("Remotia — Remote Desktop Client")
        self.resize(1100, 720)

        self.connection_list = ConnectionList()
        self.connection_list.connect_requested.connect(self.connect_connection)
        self.connection_list.new_requested.connect(self.new_connection)
        self.connection_list.edit_requested.connect(self.edit_connection)
        self.connection_list.duplicate_requested.connect(self.duplicate_connection)
        self.connection_list.delete_requested.connect(self.delete_connection)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._update_session_actions)
        self._tab_conns: dict[int, Connection] = {}

        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        title = QLabel("Remotia")
        title.setStyleSheet("font-size: 26px; font-weight: bold; color: #888;")
        subtitle = QLabel(
            "Select a connection on the left, or create a new one.\n"
            "Supported protocols: VNC (embedded), SSH (embedded), RDP (native client)."
        )
        subtitle.setStyleSheet("color: #777;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addStretch()
        wl.addWidget(title)
        wl.addWidget(subtitle)
        wl.addStretch()
        self.tabs.addTab(welcome, "Welcome")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)
        self.tabs.tabBar().setTabsClosable(True)

        # The tabs fill the whole window; the connections list floats on top
        # of them so showing it never resizes the remote session.
        self.setCentralWidget(self.tabs)

        self.connection_panel = QFrame(self.tabs)
        self.connection_panel.setObjectName("connectionPanel")
        self.connection_panel.setStyleSheet(
            "QFrame#connectionPanel {"
            "  background: #26282c; border: 1px solid #3a3d41; border-left: none;"
            "  border-radius: 0 8px 8px 0;"
            "}"
        )
        panel_layout = QVBoxLayout(self.connection_panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        title_row = QHBoxLayout()
        panel_title = QLabel("Connections")
        panel_title.setStyleSheet("font-weight: bold; color: #ccc;")
        panel_close = QPushButton("✕")
        panel_close.setFlat(True)
        panel_close.setFixedSize(22, 22)
        panel_close.setToolTip("Hide connections (Ctrl+Shift+L)")
        panel_close.setStyleSheet("QPushButton { color: #999; } QPushButton:hover { color: #fff; }")
        panel_close.clicked.connect(lambda: self.connections_toggle.setChecked(False))
        title_row.addWidget(panel_title)
        title_row.addStretch()
        title_row.addWidget(panel_close)
        panel_layout.addLayout(title_row)
        panel_layout.addWidget(self.connection_list, 1)
        self.connection_panel.hide()

        self._build_menus()
        self._build_toolbar()
        self._sync_connections_panel(True)
        self.statusBar().showMessage("Ready")
        self.reload_connections()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.connections_toggle = toolbar.addAction("☰ Connections")
        self.connections_toggle.setCheckable(True)
        self.connections_toggle.setChecked(True)
        self.connections_toggle.setShortcut("Ctrl+Shift+L")
        self.connections_toggle.toggled.connect(self._sync_connections_panel)
        self.connections_menu_action.toggled.connect(self._sync_connections_panel)

        toolbar.addSeparator()
        self.share_folder_action = toolbar.addAction("📁 Share Folder…")
        self.share_folder_action.triggered.connect(self._share_folder_current)
        self.disconnect_action = toolbar.addAction("⏻ Disconnect")
        self.disconnect_action.triggered.connect(self._disconnect_current)
        self._update_session_actions()

    def _sync_connections_panel(self, visible: bool) -> None:
        for act in (self.connections_toggle, self.connections_menu_action):
            act.blockSignals(True)
            act.setChecked(visible)
            act.blockSignals(False)
        if visible:
            self._position_connections_panel()
            self.connection_panel.show()
            self.connection_panel.raise_()
        else:
            self.connection_panel.hide()

    def _position_connections_panel(self) -> None:
        width = 290
        y = self.tabs.tabBar().height()
        height = self.tabs.height() - y
        self.connection_panel.setGeometry(0, y, width, max(0, height))
        self.connection_panel.raise_()

    # -- per-session actions -------------------------------------------------
    def _update_session_actions(self) -> None:
        if not hasattr(self, "share_folder_action"):
            return
        widget = self.tabs.widget(self.tabs.currentIndex())
        is_rdp = isinstance(widget, EmbeddedRdpView)
        self.share_folder_action.setEnabled(is_rdp)
        self.disconnect_action.setEnabled(widget is not None and hasattr(widget, "stop"))

    def _share_folder_current(self) -> None:
        widget = self.tabs.widget(self.tabs.currentIndex())
        if not isinstance(widget, EmbeddedRdpView):
            return
        conn = self._tab_conns.get(id(widget))
        if conn is None:
            return
        path = QFileDialog.getExistingDirectory(self, "Share a folder with the remote")
        if not path:
            return
        conn.shared_folder = path
        self.store.save(conn)
        index = self.tabs.currentIndex()
        self.close_tab(index)
        self.connect_connection(conn)
        self.statusBar().showMessage(f"Reconnected sharing: {path}")

    def _disconnect_current(self) -> None:
        index = self.tabs.currentIndex()
        if index >= 0:
            self.close_tab(index)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.connection_panel.isVisible():
            self._position_connections_panel()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.connection_panel.isVisible():
            self._position_connections_panel()

    # -- menus & actions ----------------------------------------------------
    def _build_menus(self) -> None:
        from PyQt6.QtGui import QAction, QKeySequence

        def action(text, shortcut, slot):
            act = QAction(text, self)
            act.setShortcut(QKeySequence(shortcut))
            act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            act.triggered.connect(slot)
            return act

        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")
        file_menu.addAction(action("New Connection", "Ctrl+N", self.new_connection))
        file_menu.addAction(action("Edit Connection", "Ctrl+E", self._edit_selected))
        file_menu.addAction(action("Duplicate Connection", "Ctrl+D", self._duplicate_selected))
        file_menu.addAction(action("Delete Connection", "Ctrl+Backspace", self._delete_selected))
        file_menu.addSeparator()
        file_menu.addAction(action("Import Royal TSX…", "", self.import_royal_tsx))
        file_menu.addSeparator()
        quit_act = QAction("Quit", self)
        quit_act.setShortcut(QKeySequence("Ctrl+Q"))
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        conn_menu = menubar.addMenu("&Connection")
        conn_menu.addAction(action("Connect", "Ctrl+Shift+C", self._connect_selected))

        view_menu = menubar.addMenu("&View")
        self.connections_menu_action = view_menu.addAction("Connections Panel")
        self.connections_menu_action.setCheckable(True)
        self.connections_menu_action.setChecked(True)
        self.connections_menu_action.setShortcut("Ctrl+Shift+L")

    def _connect_selected(self) -> None:
        conn = self.connection_list.selected()
        if conn:
            self.connect_connection(conn)

    def _edit_selected(self) -> None:
        conn = self.connection_list.selected()
        if conn:
            self.edit_connection(conn)

    def _delete_selected(self) -> None:
        conn = self.connection_list.selected()
        if conn:
            self.delete_connection(conn)

    def _duplicate_selected(self) -> None:
        conn = self.connection_list.selected()
        if conn:
            self.duplicate_connection(conn)

    def import_royal_tsx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Royal TSX document",
            str(Path.home()),
            "Royal TSX documents (*.rtsx *.rtsz)",
        )
        if not path:
            return
        try:
            connections = import_royal_document(path, self.store)
        except ValueError:
            password, ok = QInputDialog.getText(
                self,
                "Document password",
                "This document is password protected. Enter the password:",
                QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            try:
                connections = import_royal_document(path, self.store, password)
            except ValueError as exc:
                QMessageBox.warning(self, "Import failed", str(exc))
                return
        self.reload_connections()
        QMessageBox.information(self, "Import", f"Imported {len(connections)} connections.")

    # -- connection CRUD ----------------------------------------------------
    def reload_connections(self) -> None:
        self.connection_list.set_connections(self.store.list())

    def new_connection(self) -> None:
        dialog = ConnectionEditor(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            conn = dialog.result_connection(None)
            self.store.save(conn)
            self.reload_connections()

    def edit_connection(self, conn: Connection) -> None:
        dialog = ConnectionEditor(self, conn)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.save(dialog.result_connection(conn))
            self.reload_connections()

    def duplicate_connection(self, conn: Connection) -> None:
        clone = Connection(**{k: v for k, v in conn.__dict__.items() if k != "id"})
        clone.id = None
        clone.name = f"{conn.name} (copy)"
        dialog = ConnectionEditor(self, clone)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.save(dialog.result_connection(clone))
            self.reload_connections()

    def delete_connection(self, conn: Connection) -> None:
        answer = QMessageBox.question(
            self,
            "Delete connection",
            f'Delete "{conn.name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete(conn.id)
            self.reload_connections()

    # -- sessions -----------------------------------------------------------
    def connect_connection(self, conn: Connection) -> None:
        self.statusBar().showMessage(f"Connecting to {conn.server}…")
        if conn.protocol == "vnc":
            self._connect_vnc(conn)
        elif conn.protocol == "ssh":
            self._connect_ssh(conn)
        elif conn.protocol == "rdp":
            self._connect_rdp(conn)
        else:
            QMessageBox.warning(self, "Unsupported", f"Unknown protocol: {conn.protocol}")

    def _add_tab(self, widget: QWidget, title: str) -> int:
        index = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(index)
        return index

    def _connect_vnc(self, conn: Connection) -> None:
        view = VncView()
        self._tab_conns[id(view)] = conn
        self._add_tab(view, f"{conn.name}")
        view.session.failed.connect(lambda msg, w=view: self._session_failed(w, msg))
        view.session.closed.connect(lambda w=view: self._session_closed(w))
        view.connect_to(
            conn.server, conn.effective_port(), conn.username, conn.password
        )

    def _connect_ssh(self, conn: Connection) -> None:
        view = SshView()
        self._tab_conns[id(view)] = conn
        self._add_tab(view, f"{conn.name}")
        view.session.failed.connect(lambda msg, w=view: self._session_failed(w, msg))
        view.session.closed.connect(lambda w=view: self._session_closed(w))
        view.connect_to(
            conn.server, conn.effective_port(), conn.username, conn.password, conn.private_key
        )

    def _connect_rdp(self, conn: Connection) -> None:
        # Prefer the in-app embedded viewer; fall back to a native client.
        if rdp_helper_path() is not None:
            view = EmbeddedRdpView()
            self._tab_conns[id(view)] = conn
            self._add_tab(view, f"{conn.name}")
            view.failed.connect(lambda msg, w=view: self._session_failed(w, msg))
            view.closed.connect(lambda w=view: self._session_closed(w))
            view.connect_to(conn)
            return

        view = RdpView()
        self._tab_conns[id(view)] = conn
        self._add_tab(view, f"{conn.name}")
        error = view.launch(conn)
        if error:
            QMessageBox.warning(self, "RDP", error)
            self.statusBar().showMessage("RDP launch failed", 5000)

    def _session_failed(self, widget: QWidget, message: str) -> None:
        self.statusBar().showMessage("Connection failed", 5000)
        QMessageBox.warning(self, "Connection failed", message)
        index = self.tabs.indexOf(widget)
        if index >= 0:
            self.tabs.removeTab(index)
        self._tab_conns.pop(id(widget), None)
        if hasattr(widget, "stop"):
            widget.stop()

    def _session_closed(self, widget: QWidget) -> None:
        self.statusBar().showMessage("Disconnected", 3000)
        index = self.tabs.indexOf(widget)
        if index >= 0:
            title = self.tabs.tabText(index)
            if " (disconnected)" not in title:
                self.tabs.setTabText(index, title + " (disconnected)")

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is not None and hasattr(widget, "stop"):
            widget.stop()
        self._tab_conns.pop(id(widget), None)
        self.tabs.removeTab(index)

    def closeEvent(self, event) -> None:  # noqa: N802
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if widget is not None and hasattr(widget, "stop"):
                widget.stop()
        self.store.close()
        super().closeEvent(event)
