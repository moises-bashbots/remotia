"""Connection profiles and SQLite persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import secrets

PROTOCOLS = ("vnc", "ssh", "rdp")
DEFAULT_PORTS = {"vnc": 5900, "ssh": 22, "rdp": 3389}


def _data_dir() -> Path:
    path = Path.home() / ".config" / "remotia"
    path.mkdir(parents=True, exist_ok=True)
    return path


DB_PATH = _data_dir() / "connections.db"


@dataclass
class Connection:
    name: str
    protocol: str
    server: str
    port: Optional[int] = None
    username: str = ""
    password: str = ""
    domain: str = ""
    group: str = ""
    resolution: str = "auto"  # auto | WIDTHxHEIGHT
    color_depth: int = 24
    quality: int = 6
    private_key: str = ""
    extra_args: str = ""
    shared_folder: str = ""
    notes: str = ""
    id: Optional[int] = None

    def effective_port(self) -> int:
        if self.port:
            return self.port
        return DEFAULT_PORTS.get(self.protocol, 0)

    def to_row(self) -> dict:
        return {
            "name": self.name,
            "protocol": self.protocol,
            "server": self.server,
            "port": self.port,
            "username": self.username,
            "domain": self.domain,
            "group": self.group,
            "resolution": self.resolution,
            "color_depth": self.color_depth,
            "quality": self.quality,
            "private_key": self.private_key,
            "extra_args": self.extra_args,
            "shared_folder": self.shared_folder,
            "notes": self.notes,
        }


class Store:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                protocol TEXT NOT NULL,
                server TEXT NOT NULL,
                port INTEGER,
                username TEXT DEFAULT '',
                domain TEXT DEFAULT '',
                grp TEXT DEFAULT '',
                resolution TEXT DEFAULT 'auto',
                color_depth INTEGER DEFAULT 24,
                quality INTEGER DEFAULT 6,
                private_key TEXT DEFAULT '',
                extra_args TEXT DEFAULT '',
                shared_folder TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            )
            """
        )
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(connections)")}
        if "shared_folder" not in cols:
            self._conn.execute("ALTER TABLE connections ADD COLUMN shared_folder TEXT DEFAULT ''")
            self._conn.commit()

    def _secret_account(self, conn_id: int) -> str:
        return f"connection:{conn_id}"

    def list(self) -> list[Connection]:
        rows = self._conn.execute("SELECT * FROM connections ORDER BY grp, name").fetchall()
        return [self._row_to_conn(r) for r in rows]

    def get(self, conn_id: int) -> Optional[Connection]:
        row = self._conn.execute("SELECT * FROM connections WHERE id = ?", (conn_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_conn(row)

    def save(self, conn: Connection) -> Connection:
        if conn.id is None:
            cur = self._conn.execute(
                """
                INSERT INTO connections
                    (name, protocol, server, port, username, domain, grp,
                     resolution, color_depth, quality, private_key, extra_args,
                     shared_folder, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    conn.name, conn.protocol, conn.server, conn.port, conn.username,
                    conn.domain, conn.group, conn.resolution, conn.color_depth,
                    conn.quality, conn.private_key, conn.extra_args, conn.shared_folder,
                    conn.notes,
                ),
            )
            conn.id = cur.lastrowid
        else:
            self._conn.execute(
                """
                UPDATE connections SET
                    name=?, protocol=?, server=?, port=?, username=?, domain=?,
                    grp=?, resolution=?, color_depth=?, quality=?, private_key=?,
                    extra_args=?, shared_folder=?, notes=?
                WHERE id=?
                """,
                (
                    conn.name, conn.protocol, conn.server, conn.port, conn.username,
                    conn.domain, conn.group, conn.resolution, conn.color_depth,
                    conn.quality, conn.private_key, conn.extra_args, conn.shared_folder,
                    conn.notes, conn.id,
                ),
            )
        self._conn.commit()
        if conn.password:
            secrets.set_secret(self._secret_account(conn.id), conn.password)
        else:
            secrets.delete_secret(self._secret_account(conn.id))
        return conn

    def delete(self, conn_id: int) -> None:
        self._conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
        self._conn.commit()
        secrets.delete_secret(self._secret_account(conn_id))

    def _row_to_conn(self, row: sqlite3.Row) -> Connection:
        conn_id = row["id"]
        return Connection(
            id=conn_id,
            name=row["name"],
            protocol=row["protocol"],
            server=row["server"],
            port=row["port"],
            username=row["username"],
            password=secrets.get_secret(self._secret_account(conn_id)),
            domain=row["domain"],
            group=row["grp"],
            resolution=row["resolution"],
            color_depth=row["color_depth"],
            quality=row["quality"],
            private_key=row["private_key"],
            extra_args=row["extra_args"],
            shared_folder=row["shared_folder"],
            notes=row["notes"],
        )

    def close(self) -> None:
        self._conn.close()
