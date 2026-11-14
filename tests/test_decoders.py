"""Tests for the RFB protocol decoders."""

import asyncio
import io
import zlib

from remotia import rfb


class FakeReader:
    def __init__(self, data: bytes):
        self.buf = bytearray(data)

    async def readexactly(self, n: int) -> bytes:
        if len(self.buf) < n:
            raise asyncio.IncompleteReadError(partial=bytes(self.buf), expected=n)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out


def make_client(width=16, height=16, reader_data=b"") -> rfb.RFBClient:
    client = rfb.RFBClient.__new__(rfb.RFBClient)
    client.reader = FakeReader(reader_data)
    client.writer = None
    client.width = width
    client.height = height
    client.fb = bytearray(b"\x00\x00\x00\xff") * (width * height)
    client._zlib = zlib.decompressobj()
    client._closed = False
    return client


def pixel(r, g, b):
    return bytes([r, g, b, 0xFF])


def test_cpixels_to_rgba():
    out = rfb.RFBClient._cpixels_to_rgba(b"\x01\x02\x03\x04\x05\x06", 2)
    assert out == bytes([1, 2, 3, 255, 4, 5, 6, 255])


def test_read_rle_length():
    assert rfb.RFBClient._read_rle_length(io.BytesIO(b"\x00")) == 1
    assert rfb.RFBClient._read_rle_length(io.BytesIO(b"\xfe")) == 255
    assert rfb.RFBClient._read_rle_length(io.BytesIO(b"\xff\x00")) == 256
    assert rfb.RFBClient._read_rle_length(io.BytesIO(b"\xff\xfe")) == 510


def test_zrle_rle():
    client = make_client()
    # total = 2, one run of length 2 with color (10,20,30)
    buf = io.BytesIO(b"\x0a\x14\x1e\x01")  # CPIXEL + run-length-1 = 1 -> length 2
    tile = client._zrle_rle(buf, 2)
    assert tile == pixel(10, 20, 30) * 2


def test_zrle_palette():
    client = make_client()
    # palette size 2 -> 1 bit per pixel; w=2 h=2 -> 4 pixels
    buf = io.BytesIO(
        b"\xff\x00\x00"  # palette[0] red
        + b"\x00\xff\x00"  # palette[1] green
        + b"\x50"  # packed: 0,1,0,1 (0b0101_0000)
        + b"\x50"
    )
    tile = client._zrle_palette(buf, 2, 2, 2)
    assert tile == pixel(255, 0, 0) + pixel(0, 255, 0) + pixel(255, 0, 0) + pixel(0, 255, 0)


def test_zrle_palette_rle():
    client = make_client()
    # palette size 2; runs: [0] single, [128+1] len 2, [0] single -> total 4
    buf = io.BytesIO(
        b"\xff\x00\x00"  # palette[0] red
        + b"\x00\xff\x00"  # palette[1] green
        + b"\x00"  # index 0, length 1
        + b"\x81\x01"  # index 1, length 2
        + b"\x00"  # index 0, length 1
    )
    tile = client._zrle_palette_rle(buf, 4, 2)
    assert tile == pixel(255, 0, 0) + pixel(0, 255, 0) * 2 + pixel(255, 0, 0)


def test_zrle_full_rect():
    client = make_client(width=2, height=2)
    # One 2x2 tile, subencoding 1 (solid), CPIXEL (1,2,3)
    raw = b"\x01\x01\x02\x03"
    comp = zlib.compress(raw)
    data = len(comp).to_bytes(4, "big") + comp
    client.reader = FakeReader(data)
    asyncio.run(client._read_zrle(0, 0, 2, 2))
    assert bytes(client.fb) == pixel(1, 2, 3) * 4


def test_raw():
    client = make_client(width=2, height=2)
    data = pixel(1, 2, 3) * 4
    client.reader = FakeReader(data)
    asyncio.run(client._read_raw(0, 0, 2, 2))
    assert bytes(client.fb) == data


def test_rre():
    client = make_client(width=4, height=4)
    data = (1).to_bytes(4, "big")  # 1 subrect
    data += pixel(255, 0, 0)  # background
    data += pixel(0, 255, 0)  # subrect pixel
    data += (1).to_bytes(2, "big") + (1).to_bytes(2, "big")  # x, y
    data += (2).to_bytes(2, "big") + (2).to_bytes(2, "big")  # w, h
    client.reader = FakeReader(data)
    asyncio.run(client._read_rre(0, 0, 4, 4))
    assert bytes(client.fb[:4]) == pixel(255, 0, 0)  # top-left background
    # pixel at (1,1) is green
    off = (1 * 4 + 1) * 4
    assert bytes(client.fb[off:off + 4]) == pixel(0, 255, 0)


def test_hextile_solid_background():
    client = make_client(width=16, height=16)
    data = b"\x02" + pixel(9, 9, 9)
    client.reader = FakeReader(data)
    asyncio.run(client._read_hextile(0, 0, 16, 16))
    assert bytes(client.fb) == pixel(9, 9, 9) * 256


def test_hextile_subrects():
    client = make_client(width=16, height=16)
    sub = 0x02 | 0x04 | 0x08 | 0x10  # bg + fg + any subrects + coloured
    data = bytes([sub])
    data += pixel(1, 1, 1)  # background
    data += pixel(2, 2, 2)  # foreground (unused since coloured)
    data += b"\x01"  # one subrect
    data += pixel(3, 3, 3)  # subrect colour
    data += b"\x00"  # x=0, y=0
    data += b"\x11"  # w=2, h=2
    client.reader = FakeReader(data)
    asyncio.run(client._read_hextile(0, 0, 16, 16))
    assert bytes(client.fb[:4]) == pixel(3, 3, 3)  # subrect top-left
    assert bytes(client.fb[4:8]) == pixel(3, 3, 3)
    # pixel at (0, 2) is background
    off = 2 * 64
    assert bytes(client.fb[off:off + 4]) == pixel(1, 1, 1)


def test_copyrect():
    client = make_client(width=4, height=4)
    # put a marker at (0,0)
    client.fb[0:4] = pixel(7, 8, 9)
    data = (0).to_bytes(2, "big") + (0).to_bytes(2, "big")
    client.reader = FakeReader(data)
    asyncio.run(client._read_copyrect(1, 1, 2, 2))
    off = (1 * 4 + 1) * 4
    assert bytes(client.fb[off:off + 4]) == pixel(7, 8, 9)


def test_vnc_auth_deterministic():
    challenge = bytes(range(16))
    a = rfb._vnc_auth_response("password", challenge)
    b = rfb._vnc_auth_response("password", challenge)
    assert len(a) == 16
    assert a == b
    assert a != challenge


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failures else 0)
