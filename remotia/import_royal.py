"""Import connections from Royal TSX documents (``.rtsx`` / ``.rtsz``).

Royal TS/X stores credentials encrypted with AES-XTS.  The key is a fixed
application secret concatenated with the document password (empty when the
document is not password protected).  This module decrypts those values and
writes the connections into our SQLite store.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf import pbkdf2

from .models import Connection, Store

_APP_SECRET = b"jtWcgJq<MKE]@M#uH3yKZi]CznpP}?}VKr3r]h{<wkp%+FMwUz"

# Royal TSX element name -> our protocol
_PROTOCOLS = {
    "RoyalRDSConnection": "rdp",
    "RoyalVNCConnection": "vnc",
    "RoyalSSHConnection": "ssh",
    "RoyalTerminalConnection": "ssh",
}

_PORT_TAGS = ("RDPPort", "VNCPort", "SSHPort", "TerminalPort", "Port")


def decrypt_royal_password(ciphertext: str, document_password: str = "") -> str:
    """Decrypt a Royal TS/X encrypted credential value."""
    data = base64.b64decode(ciphertext)
    salt, header, body = data[:48], data[48:64], data[64:]

    key_material = _APP_SECRET + document_password.encode("utf-8")
    xts_key = pbkdf2.PBKDF2HMAC(hashes.SHA1(), 32, salt[:40], 1000).derive(key_material)

    decryptor = Cipher(algorithms.AES(xts_key), modes.XTS(b"\xff" * 8 + b"\x00" * 8)).decryptor()
    header = decryptor.update(header) + decryptor.finalize()
    if header[:4] != b"XTS1":
        raise ValueError(
            "Wrong document password (or not a Royal TS encrypted value)"
        )
    text_length = int.from_bytes(header[4:12], "little")
    block_size = int.from_bytes(header[12:16], "little")

    out = bytearray()
    i = 0
    while i * block_size < len(body):
        block = body[i * block_size : (i + 1) * block_size]
        decryptor = Cipher(algorithms.AES(xts_key), modes.XTS(i.to_bytes(16, "little"))).decryptor()
        out += decryptor.update(block) + decryptor.finalize()
        i += 1
    return bytes(out[:text_length]).decode("utf-8")


def _child_text(el: ET.Element, tag: str) -> Optional[str]:
    child = el.find(tag)
    if child is None:
        return None
    text = child.text or ""
    return text.strip()


def parse_royal_document(
    path: str | Path,
    document_password: str = "",
) -> list[Connection]:
    """Parse a Royal TSX document and return a list of :class:`Connection`."""
    tree = ET.parse(str(path))
    root = tree.getroot()

    # Folders (for grouping) and shared credentials.
    folders: dict[str, str] = {}
    credentials: dict[str, tuple[str, str]] = {}
    for el in root.iter():
        if el.tag == "RoyalFolder":
            folder_id = _child_text(el, "ID")
            if folder_id:
                folders[folder_id] = _child_text(el, "Name") or ""
        elif el.tag == "RoyalCredential":
            cred_id = _child_text(el, "ID")
            if cred_id:
                username = _child_text(el, "Username") or ""
                encrypted = _child_text(el, "Password")
                password = ""
                if encrypted:
                    password = decrypt_royal_password(encrypted, document_password)
                credentials[cred_id] = (username, password)

    connections: list[Connection] = []
    for el in root.iter():
        protocol = _PROTOCOLS.get(el.tag)
        if protocol is None:
            continue

        name = _child_text(el, "Name") or "Unnamed"
        uri = _child_text(el, "URI") or ""
        if not uri:
            continue

        port = None
        for tag in _PORT_TAGS:
            value = _child_text(el, tag)
            if value:
                try:
                    port = int(value)
                except ValueError:
                    port = None
                if port:
                    break

        username = _child_text(el, "CredentialUsername") or ""
        encrypted_password = _child_text(el, "CredentialPassword") or ""
        password = ""
        if encrypted_password:
            password = decrypt_royal_password(encrypted_password, document_password)
        else:
            cred_id = _child_text(el, "CredentialID")
            if cred_id and cred_id in credentials:
                username, password = credentials[cred_id]

        parent_id = _child_text(el, "ParentID")
        group = folders.get(parent_id, "")
        if group in ("Connections", ""):
            group = ""

        width = _child_text(el, "DesktopWidth")
        height = _child_text(el, "DesktopHeight")
        resolution = "auto"
        if width and height:
            try:
                if int(width) > 0 and int(height) > 0:
                    resolution = f"{width}x{height}"
            except ValueError:
                pass

        color_depth = 24
        depth = _child_text(el, "ColorDepth")
        if depth:
            try:
                color_depth = int(depth)
            except ValueError:
                color_depth = 24

        connections.append(
            Connection(
                name=name,
                protocol=protocol,
                server=uri,
                port=port,
                username=username,
                password=password,
                group=group,
                resolution=resolution,
                color_depth=color_depth,
            )
        )
    return connections


def import_royal_document(
    path: str | Path,
    store: Store,
    document_password: str = "",
) -> list[Connection]:
    connections = parse_royal_document(path, document_password)
    for conn in connections:
        store.save(conn)
    return connections


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Import a Royal TSX document")
    parser.add_argument("document", help="Path to the .rtsx / .rtsz document")
    parser.add_argument("--password", default="", help="Document password (if protected)")
    parser.add_argument("--dry-run", action="store_true", help="Only print, do not save")
    args = parser.parse_args()

    store = Store()
    connections = parse_royal_document(args.document, args.password)
    if args.dry_run:
        for c in connections:
            print(f"{c.name}\t{c.protocol}\t{c.server}:{c.effective_port()}\t{c.username}")
    else:
        for c in connections:
            store.save(c)
        print(f"Imported {len(connections)} connections.", file=sys.stderr)
    store.close()
