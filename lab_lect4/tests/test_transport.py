import socket
import time
import unittest
from unittest.mock import Mock

from leader_election.protocol import Message
from leader_election.transport import Connection, Transport


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.invalid = []
        self.transport = Transport(("127.0.0.1", 0), (1, 2, 3, 4, 5), 0.2,
                                   self.received.append, self.invalid.append)
        self.address = self.transport.listener.getsockname()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.transport.close()

    def connect(self):
        client = socket.create_connection(self.address, timeout=1)
        self.clients.append(client)
        self.transport.poll(0.01)
        return client

    def poll_until(self, predicate, timeout=1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not predicate():
            self.transport.poll(0.01)
        self.assertTrue(predicate())

    def test_fragmented_frame_waits_for_newline(self):
        message = Message("ELECTION", 2, 2, (2, 1), (2,))
        wire = message.encode()
        client = self.connect()
        client.sendall(wire[:8])
        self.transport.poll(0.01)
        self.assertFalse(self.received)
        client.sendall(wire[8:-1])
        self.transport.poll(0.01)
        self.assertFalse(self.received)
        client.sendall(wire[-1:])
        self.poll_until(lambda: bool(self.received))
        self.assertEqual(self.received, [message])

    def test_bad_and_oversized_frames_do_not_stop_listener(self):
        client = self.connect()
        client.sendall(b"not-json\n")
        self.poll_until(lambda: len(self.invalid) == 1)
        client = self.connect()
        client.sendall(b"x" * 9000)
        self.poll_until(lambda: len(self.invalid) == 2)
        client = self.connect()
        client.sendall(Message("HEARTBEAT", 5, leader_id=5).encode())
        self.poll_until(lambda: bool(self.received))
        self.assertEqual(self.received[0].leader_id, 5)

    def test_incomplete_frame_deadline_releases_connection(self):
        client = self.connect()
        client.sendall(b'{"type":')
        self.poll_until(lambda: bool(self.invalid))
        self.assertFalse(self.transport.connections)

    def test_partial_write_keeps_offset_until_complete(self):
        sock = Mock(spec=socket.socket)
        sock.getsockopt.return_value = 0
        sock.send.side_effect = [3, 2, 5]
        connection = Connection(sock, time.monotonic() + 1, outgoing=True,
                                payload=b"0123456789")
        original = self.transport._finish
        finished = Mock()
        self.transport._finish = finished
        try:
            self.transport._write(connection)
            self.assertEqual(connection.offset, 3)
            finished.assert_not_called()
            self.transport._write(connection)
            self.assertEqual(connection.offset, 5)
            finished.assert_not_called()
            self.transport._write(connection)
            finished.assert_called_once_with(connection, True)
            self.assertEqual([call.args[0] for call in sock.send.call_args_list],
                             [b"0123456789", b"3456789", b"56789"])
        finally:
            self.transport._finish = original

    def test_multiple_frames_in_one_connection_rejected(self):
        client = self.connect()
        client.sendall(b"{}\n{}\n")
        self.poll_until(lambda: bool(self.invalid))
        self.assertFalse(self.received)

    def test_ready_socket_does_not_deliver_frame_after_its_deadline(self):
        client = self.connect()
        client.sendall(Message("HEARTBEAT", 5, leader_id=5).encode())
        for connection in self.transport.connections.values():
            connection.deadline = time.monotonic() - 1
        self.transport.poll(0.01)
        self.assertFalse(self.received)
        self.assertEqual(self.invalid, ["Incoming frame timed out"])
        self.assertFalse(self.transport.connections)


if __name__ == "__main__":
    unittest.main()
