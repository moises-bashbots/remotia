"""RFB (VNC) protocol client.

Pure asyncio implementation supporting the encodings used by virtually every
real world VNC server: Raw, CopyRect, RRE, Hextile and ZRLE, plus VNC and
Apple (ARD) authentication.

The framebuffer is kept as a bytearray in tight RGBA (byte order R,G,B,A)
so it can be blitted straight into a ``QImage(..., Format_RGBA8888)``.
"""

from __future__ import annotations

import asyncio
import io
import struct
import zlib
from os import urandom
from typing import Callable, Optional

from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key

try:  # TripleDES moved to the decrepit module in newer cryptography releases
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:  # pragma: no cover
    TripleDES = algorithms.TripleDES

# --- Encoding numbers -------------------------------------------------------
RAW = 0
COPYRECT = 1
RRE = 2
HEXTILE = 5
ZRLE = 16
# Pseudo encodings (negative 32-bit values)
LAST_RECT = -224

# --- Security types ---------------------------------------------------------
SEC_NONE = 1
SEC_VNC = 2
SEC_ARD = 33

BLACK = b"\x00\x00\x00\xff"


class RFBError(Exception):
    """Base class for RFB errors."""


class SecurityError(RFBError):
    pass


class AuthError(RFBError):
    pass


class ProtocolError(RFBError):
    pass


# --------------------------------------------------------------------------
# Low level readers
# --------------------------------------------------------------------------
async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    return await reader.readexactly(n)


async def _read_u8(reader: asyncio.StreamReader) -> int:
    return (await reader.readexactly(1))[0]


async def _read_u16(reader: asyncio.StreamReader) -> int:
    return int.from_bytes(await reader.readexactly(2), "big")


async def _read_u32(reader: asyncio.StreamReader) -> int:
    return int.from_bytes(await reader.readexactly(4), "big")


async def _read_i32(reader: asyncio.StreamReader) -> int:
    return struct.unpack(">i", await reader.readexactly(4))[0]


async def _read_text(reader: asyncio.StreamReader) -> str:
    length = await _read_u32(reader)
    return (await reader.readexactly(length)).decode("utf-8", "replace")


# --------------------------------------------------------------------------
# Authentication helpers
# --------------------------------------------------------------------------
def _vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """Compute the DES challenge response used by VNC authentication."""
    key = password.encode("ascii", "replace")[:8].ljust(8, b"\x00")
    # Each key byte has its bits reversed as required by the RFB spec.
    key = bytes(int(bin(n)[:1:-1].ljust(8, "0"), 2) for n in key)
    # 3DES-EDE with K1=K2=K3 is identical to single DES (what VNC uses).
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


def _pack_ard(data: str) -> bytes:
    data = data.encode("utf-8") + b"\x00"
    if len(data) < 64:
        data += urandom(64 - len(data))
    else:
        data = data[:64]
    return data


async def _apple_auth(reader, writer, username: str, password: str) -> None:
    """Apple Remote Desktop (security type 33) authentication."""
    writer.write(b"\x00\x00\x00\x0a\x01\x00RSA1\x00\x00\x00\x00")
    await writer.drain()
    await _read_exact(reader, 4)  # packet length
    await _read_exact(reader, 2)  # packet version
    host_key_length = await _read_u32(reader)
    host_key = await _read_exact(reader, host_key_length)
    host_key = load_der_public_key(host_key)
    await _read_exact(reader, 1)  # unknown

    aes_key = urandom(16)
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    credentials = _pack_ard(username) + _pack_ard(password)
    writer.write(
        b"\x00\x00\x01\x8a\x01\x00RSA1"
        + b"\x00\x01"
        + encryptor.update(credentials)
        + b"\x00\x01"
        + host_key.encrypt(aes_key, asym_padding.PKCS1v15())
    )
    await writer.drain()
    await _read_exact(reader, 4)  # unknown


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------
class RFBClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        width: int,
        height: int,
        name: str,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.width = width
        self.height = height
        self.name = name
        self.fb = bytearray(b"\x00\x00\x00\xff") * (width * height)
        self._zlib = zlib.decompressobj()
        self._closed = False

    # -- server -> client ---------------------------------------------------
    async def run(
        self,
        on_update: Callable[[int, int, int, int, bytes], None],
        on_clipboard: Optional[Callable[[str], None]] = None,
        on_bell: Optional[Callable[[], None]] = None,
    ) -> None:
        """Read server messages until the connection closes."""
        try:
            self._request_update(incremental=False)
            await self.writer.drain()
            while True:
                msg_type = await _read_u8(self.reader)
                if msg_type == 0:  # FramebufferUpdate
                    await self._handle_framebuffer_update(on_update)
                elif msg_type == 1:  # SetColourMapEntries (ignore)
                    await _read_exact(self.reader, 1)
                    await _read_u16(self.reader)
                    count = await _read_u16(self.reader)
                    await _read_exact(self.reader, count * 6)
                elif msg_type == 2:  # Bell
                    if on_bell:
                        on_bell()
                elif msg_type == 3:  # ServerCutText
                    await _read_exact(self.reader, 3)
                    length = await _read_u32(self.reader)
                    text = (await _read_exact(self.reader, length)).decode("latin-1", "replace")
                    if on_clipboard:
                        on_clipboard(text)
                else:
                    raise ProtocolError(f"Unknown server message type {msg_type}")
        except asyncio.IncompleteReadError:
            return
        except OSError:
            return

    async def _handle_framebuffer_update(self, on_update) -> None:
        await _read_exact(self.reader, 1)  # padding
        count = await _read_u16(self.reader)
        for _ in range(count):
            x = await _read_u16(self.reader)
            y = await _read_u16(self.reader)
            w = await _read_u16(self.reader)
            h = await _read_u16(self.reader)
            encoding = await _read_i32(self.reader)
            if encoding == LAST_RECT:
                break
            if encoding == RAW:
                await self._read_raw(x, y, w, h)
            elif encoding == COPYRECT:
                await self._read_copyrect(x, y, w, h)
            elif encoding == RRE:
                await self._read_rre(x, y, w, h)
            elif encoding == HEXTILE:
                await self._read_hextile(x, y, w, h)
            elif encoding == ZRLE:
                await self._read_zrle(x, y, w, h)
            else:
                raise ProtocolError(f"Unsupported encoding {encoding}")
            on_update(x, y, w, h, self.region_rgba(x, y, w, h))
        self._request_update(incremental=True)
        await self.writer.drain()

    # -- decoders -----------------------------------------------------------
    async def _read_pixel(self) -> bytes:
        data = await _read_exact(self.reader, 4)
        return data[:3] + b"\xff"

    def _fill_rect(self, x: int, y: int, w: int, h: int, pixel: bytes) -> None:
        fb = self.fb
        stride = self.width * 4
        row = pixel * w
        for r in range(h):
            off = (y + r) * stride + x * 4
            fb[off : off + w * 4] = row

    def _put_raw(self, x: int, y: int, w: int, h: int, data: bytes) -> None:
        fb = self.fb
        stride = self.width * 4
        row = w * 4
        for r in range(h):
            off = (y + r) * stride + x * 4
            fb[off : off + row] = data[r * row : (r + 1) * row]
            fb[off + 3 : off + row : 4] = b"\xff" * w

    def _put_rgba_tile(self, x: int, y: int, w: int, h: int, rgba: bytes) -> None:
        fb = self.fb
        stride = self.width * 4
        row = w * 4
        for r in range(h):
            off = (y + r) * stride + x * 4
            fb[off : off + row] = rgba[r * row : (r + 1) * row]

    def _copy_rect(self, sx: int, sy: int, dx: int, dy: int, w: int, h: int) -> None:
        fb = self.fb
        stride = self.width * 4
        row = w * 4
        tmp = bytearray(row * h)
        for r in range(h):
            off = (sy + r) * stride + sx * 4
            tmp[r * row : (r + 1) * row] = fb[off : off + row]
        for r in range(h):
            off = (dy + r) * stride + dx * 4
            fb[off : off + row] = tmp[r * row : (r + 1) * row]

    def region_rgba(self, x: int, y: int, w: int, h: int) -> bytes:
        """Return a tightly packed RGBA buffer for the requested region."""
        fb = self.fb
        stride = self.width * 4
        row = w * 4
        out = bytearray(row * h)
        for r in range(h):
            off = (y + r) * stride + x * 4
            out[r * row : (r + 1) * row] = fb[off : off + row]
        return bytes(out)

    async def _read_raw(self, x: int, y: int, w: int, h: int) -> None:
        data = await _read_exact(self.reader, w * h * 4)
        self._put_raw(x, y, w, h, data)

    async def _read_copyrect(self, x: int, y: int, w: int, h: int) -> None:
        sx = await _read_u16(self.reader)
        sy = await _read_u16(self.reader)
        self._copy_rect(sx, sy, x, y, w, h)

    async def _read_rre(self, x: int, y: int, w: int, h: int) -> None:
        count = await _read_u32(self.reader)
        bg = await self._read_pixel()
        self._fill_rect(x, y, w, h, bg)
        for _ in range(count):
            pixel = await self._read_pixel()
            sx = await _read_u16(self.reader)
            sy = await _read_u16(self.reader)
            sw = await _read_u16(self.reader)
            sh = await _read_u16(self.reader)
            self._fill_rect(x + sx, y + sy, sw, sh, pixel)

    async def _read_hextile(self, x: int, y: int, w: int, h: int) -> None:
        tiles_x = (w + 15) // 16
        tiles_y = (h + 15) // 16
        bg: Optional[bytes] = None
        fg: Optional[bytes] = None
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                px = x + tx * 16
                py = y + ty * 16
                tw = min(16, w - tx * 16)
                th = min(16, h - ty * 16)
                sub = await _read_u8(self.reader)
                if sub & 0x01:  # Raw
                    data = await _read_exact(self.reader, tw * th * 4)
                    self._put_raw(px, py, tw, th, data)
                    bg = fg = None
                    continue
                if sub & 0x02:  # BackgroundSpecified
                    bg = await self._read_pixel()
                if sub & 0x04:  # ForegroundSpecified
                    fg = await self._read_pixel()
                self._fill_rect(px, py, tw, th, bg if bg is not None else BLACK)
                if sub & 0x08:  # AnySubrects
                    n = await _read_u8(self.reader)
                    for _ in range(n):
                        if sub & 0x10:  # SubrectsColoured
                            color = await self._read_pixel()
                        else:
                            color = fg
                        if color is None:
                            color = BLACK
                        xy = await _read_u8(self.reader)
                        wh = await _read_u8(self.reader)
                        sx = xy >> 4
                        sy = xy & 0x0F
                        sw = (wh >> 4) + 1
                        sh = (wh & 0x0F) + 1
                        self._fill_rect(px + sx, py + sy, sw, sh, color)
                if sub & 0x10:  # foreground may not carry over from SubrectsColoured
                    fg = None

    async def _read_zrle(self, x: int, y: int, w: int, h: int) -> None:
        length = await _read_u32(self.reader)
        comp = await _read_exact(self.reader, length)
        data = self._zlib.decompress(comp)
        buf = io.BytesIO(data)
        tiles_x = (w + 63) // 64
        tiles_y = (h + 63) // 64
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                px = x + tx * 64
                py = y + ty * 64
                tw = min(64, w - tx * 64)
                th = min(64, h - ty * 64)
                sub = buf.read(1)[0]
                total = tw * th
                if sub == 0:  # raw CPIXELs
                    raw = buf.read(total * 3)
                    self._put_rgba_tile(px, py, tw, th, self._cpixels_to_rgba(raw, total))
                elif sub == 1:  # solid
                    self._fill_rect(px, py, tw, th, buf.read(3) + b"\xff")
                elif 2 <= sub <= 16:  # packed palette
                    self._put_rgba_tile(px, py, tw, th, self._zrle_palette(buf, tw, th, sub))
                elif sub == 128:  # plain RLE
                    self._put_rgba_tile(px, py, tw, th, self._zrle_rle(buf, total))
                elif 130 <= sub <= 255:  # palette RLE
                    self._put_rgba_tile(px, py, tw, th, self._zrle_palette_rle(buf, total, sub - 128))
                else:
                    raise ProtocolError(f"Unknown ZRLE subencoding {sub}")

    @staticmethod
    def _cpixels_to_rgba(data: bytes, count: int) -> bytes:
        out = bytearray(count * 4)
        out[0::4] = data[0::3]
        out[1::4] = data[1::3]
        out[2::4] = data[2::3]
        out[3::4] = b"\xff" * count
        return bytes(out)

    @staticmethod
    def _read_rle_length(buf: io.BytesIO) -> int:
        length = 0
        while True:
            b = buf.read(1)[0]
            length += b
            if b != 255:
                break
        return length + 1

    def _zrle_palette(self, buf: io.BytesIO, w: int, h: int, palette_size: int) -> bytes:
        bits = 1 if palette_size <= 2 else 2 if palette_size <= 4 else 4
        palette = [buf.read(3) + b"\xff" for _ in range(palette_size)]
        mask = (1 << bits) - 1
        row_bytes = (w * bits + 7) // 8
        tile = bytearray(w * h * 4)
        idx = 0
        for _ in range(h):
            packed = buf.read(row_bytes)
            bitpos = 0
            for _ in range(w):
                index = (packed[bitpos >> 3] >> (8 - bits - (bitpos & 7))) & mask
                bitpos += bits
                tile[idx : idx + 4] = palette[index]
                idx += 4
        return bytes(tile)

    def _zrle_rle(self, buf: io.BytesIO, total: int) -> bytes:
        tile = bytearray(total * 4)
        i = 0
        while i < total:
            color = buf.read(3) + b"\xff"
            length = self._read_rle_length(buf)
            for _ in range(length):
                tile[i * 4 : i * 4 + 4] = color
                i += 1
        return bytes(tile)

    def _zrle_palette_rle(self, buf: io.BytesIO, total: int, palette_size: int) -> bytes:
        palette = [buf.read(3) + b"\xff" for _ in range(palette_size)]
        tile = bytearray(total * 4)
        i = 0
        while i < total:
            b = buf.read(1)[0]
            if b < 128:
                index = b
                length = 1
            else:
                index = b - 128
                length = self._read_rle_length(buf)
            color = palette[index]
            for _ in range(length):
                tile[i * 4 : i * 4 + 4] = color
                i += 1
        return bytes(tile)

    # -- client -> server ---------------------------------------------------
    def _request_update(self, incremental: bool) -> None:
        self.writer.write(
            b"\x03"
            + bytes([1 if incremental else 0])
            + struct.pack(">HHHH", 0, 0, self.width, self.height)
        )

    def key_event(self, keysym: int, down: bool) -> None:
        self.writer.write(b"\x04" + bytes([1 if down else 0, 0, 0]) + struct.pack(">I", keysym))

    def pointer_event(self, mask: int, x: int, y: int) -> None:
        self.writer.write(b"\x05" + bytes([mask & 0xFF]) + struct.pack(">HH", x, y))

    def send_clipboard(self, text: str) -> None:
        data = text.encode("latin-1", "replace")
        self.writer.write(b"\x06\x00\x00\x00" + struct.pack(">I", len(data)) + data)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# Connection entry point
# --------------------------------------------------------------------------
async def connect_rfb(
    host: str,
    port: int = 5900,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> RFBClient:
    """Open a connection and complete the RFB handshake."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        intro = await reader.readline()
        if not intro.startswith(b"RFB "):
            raise ProtocolError("Not a VNC server (bad protocol banner)")

        writer.write(b"RFB 003.008\n")
        await writer.drain()

        n_types = await _read_u8(reader)
        if n_types == 0:
            reason = await _read_text(reader)
            raise SecurityError(reason)
        types = set(await _read_exact(reader, n_types))

        candidates = {SEC_NONE}
        if password is not None and SEC_VNC in types:
            candidates.add(SEC_VNC)
        if username is not None and password is not None and SEC_ARD in types:
            candidates.add(SEC_ARD)

        chosen = None
        for t in (SEC_ARD, SEC_VNC, SEC_NONE):
            if t in candidates:
                chosen = t
                break
        if chosen is None:
            offered = ", ".join(str(t) for t in sorted(types))
            raise SecurityError(f"No supported security type (server offers: {offered})")

        writer.write(bytes([chosen]))
        await writer.drain()

        if chosen == SEC_VNC:
            challenge = await _read_exact(reader, 16)
            writer.write(_vnc_auth_response(password, challenge))
            await writer.drain()
        elif chosen == SEC_ARD:
            await _apple_auth(reader, writer, username, password)

        result = await _read_u32(reader)
        if result == 1:
            raise AuthError("Authentication failed (wrong password)")
        if result == 2:
            raise AuthError("Authentication failed (too many attempts)")
        if result != 0:
            reason = (await _read_exact(reader, result)).decode("utf-8", "replace")
            raise AuthError(reason)

        writer.write(b"\x01")  # shared flag
        await writer.drain()

        width = await _read_u16(reader)
        height = await _read_u16(reader)
        await _read_exact(reader, 16)  # server pixel format (ignored; we override)
        name = await _read_text(reader)

        # Request a 32-bit true-colour pixel format with byte order R, G, B, X.
        pixel_format = (
            bytes([32, 24, 0, 1])
            + (255).to_bytes(2, "big") * 3
            + bytes([0, 8, 16, 0, 0, 0])
        )
        writer.write(b"\x00\x00\x00\x00" + pixel_format)

        # Advertise the encodings we can decode, highest preference first.
        encodings = [ZRLE, HEXTILE, RRE, COPYRECT, RAW, LAST_RECT]
        writer.write(
            b"\x02\x00"
            + struct.pack(">H", len(encodings))
            + b"".join(struct.pack(">i", e) for e in encodings)
        )
        await writer.drain()

        return RFBClient(reader, writer, width, height, name)
    except BaseException:
        writer.close()
        raise
