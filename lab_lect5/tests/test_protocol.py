import asyncio
import unittest

from twopc.protocol import MAX_FRAME, ProtocolError, encode, envelope, read_message, request


class FramingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tcp_frame_split_across_writes(self):
        received = []

        async def connection(reader, writer):
            try:
                message = await read_message(reader)
                received.append(message)
                raw = encode(envelope("PONG", "P1", text="phục hồi"))
                writer.write(raw[:7])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(raw[7:])
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(connection, "127.0.0.1", 0)
        address = server.sockets[0].getsockname()[:2]
        async with server:
            reader, writer = await asyncio.open_connection(*address)
            raw = encode(envelope("PING", "CLIENT", text="kiểm thử"))
            for part in (raw[:1], raw[1:8], raw[8:]):
                writer.write(part)
                await writer.drain()
                await asyncio.sleep(0)
            response = await read_message(reader)
            writer.close()
            await writer.wait_closed()
        self.assertEqual(received[0]["text"], "kiểm thử")
        self.assertEqual(response["text"], "phục hồi")

    async def test_incomplete_invalid_and_oversized_frames(self):
        for raw in (b'{"type":"PING","sender":"CLIENT"}', b'[]\n', b'bad\n', b'\xff\n'):
            with self.subTest(raw=raw):
                reader = asyncio.StreamReader()
                reader.feed_data(raw)
                reader.feed_eof()
                with self.assertRaises(ProtocolError):
                    await read_message(reader)
        reader = asyncio.StreamReader(limit=MAX_FRAME)
        reader.feed_data(b"x" * (MAX_FRAME + 1) + b"\n")
        with self.assertRaises(ProtocolError):
            await read_message(reader)

    async def test_rpc_timeout_closes_connection(self):
        disconnected = asyncio.Event()

        async def connection(reader, writer):
            try:
                await read_message(reader)
                await reader.read()
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(connection, "127.0.0.1", 0)
        async with server:
            with self.assertRaises(TimeoutError):
                await request(server.sockets[0].getsockname()[:2], envelope("PING", "CLIENT"), 0.05)
            await asyncio.wait_for(disconnected.wait(), 1)


if __name__ == "__main__":
    unittest.main()
