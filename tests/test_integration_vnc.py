"""End-to-end test of the RFB client against an in-process fake VNC server."""

import asyncio
import struct

from remotia import rfb


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.write(b"RFB 003.008\n")
    await writer.drain()
    # Client responds with its own protocol version before we offer security.
    client_version = await reader.readline()
    assert client_version.startswith(b"RFB ")

    # Offer only security type None (1)
    writer.write(b"\x01\x01")
    await writer.drain()
    assert (await reader.readexactly(1)) == b"\x01"

    writer.write((0).to_bytes(4, "big"))
    await writer.drain()

    # ClientInit (shared flag)
    await reader.readexactly(1)

    width, height = 4, 3
    writer.write(width.to_bytes(2, "big") + height.to_bytes(2, "big"))
    writer.write(
        bytes([32, 24, 0, 1]) + (255).to_bytes(2, "big") * 3 + bytes([16, 8, 0, 0, 0, 0])
    )
    writer.write(len(b"fake-server").to_bytes(4, "big") + b"fake-server")
    await writer.drain()

    # Consume SetPixelFormat / SetEncodings and wait for the update request.
    while True:
        msg_type = (await reader.readexactly(1))[0]
        if msg_type == 0:
            await reader.readexactly(19)
        elif msg_type == 2:
            await reader.readexactly(1)
            n = int.from_bytes(await reader.readexactly(2), "big")
            await reader.readexactly(n * 4)
        elif msg_type == 3:
            await reader.readexactly(9)
            break

    # Send a full-screen raw update: a vertical red/green/blue/white gradient.
    pixels = b""
    for y in range(height):
        for x in range(width):
            if x == 0:
                pixels += bytes([255, 0, 0, 0])
            elif x == 1:
                pixels += bytes([0, 255, 0, 0])
            elif x == 2:
                pixels += bytes([0, 0, 255, 0])
            else:
                pixels += bytes([255, 255, 255, 0])
    header = (
        b"\x00\x00"
        + (1).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + height.to_bytes(2, "big")
        + rfb.RAW.to_bytes(4, "big")
    )
    writer.write(header + pixels)
    await writer.drain()
    await asyncio.sleep(0.3)
    writer.close()


async def main() -> None:
    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    updates = []
    client = await rfb.connect_rfb("127.0.0.1", port)
    assert client.width == 4 and client.height == 3
    assert client.name == "fake-server"
    await client.run(on_update=lambda x, y, w, h, d: updates.append((x, y, w, h, d)))

    assert updates, "no framebuffer update received"
    x, y, w, h, data = updates[0]
    assert (x, y, w, h) == (0, 0, 4, 3)
    # First pixel red, second green, third blue, fourth white (all alpha 255).
    assert data[:4] == bytes([255, 0, 0, 255])
    assert data[4:8] == bytes([0, 255, 0, 255])
    assert data[8:12] == bytes([0, 0, 255, 255])
    assert data[12:16] == bytes([255, 255, 255, 255])
    # Client framebuffer should match (with alpha forced to 255).
    assert bytes(client.fb) == (
        bytes([255, 0, 0, 255])
        + bytes([0, 255, 0, 255])
        + bytes([0, 0, 255, 255])
        + bytes([255, 255, 255, 255])
    ) * 3
    server.close()
    await server.wait_closed()
    print("integration test OK")


if __name__ == "__main__":
    asyncio.run(main())
