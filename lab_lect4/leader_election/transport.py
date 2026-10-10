from dataclasses import dataclass, field
import errno
import selectors
import socket
import time
from collections.abc import Callable

from .protocol import MAX_FRAME, Message, ProtocolError, decode

SendCallback = Callable[[bool, str], None]


@dataclass
class Connection:
    sock: socket.socket
    deadline: float
    outgoing: bool = False
    payload: bytes = b""
    offset: int = 0
    buffer: bytearray = field(default_factory=bytearray)
    callback: SendCallback | None = None


class Transport:
    """One selector owned by one node thread; every connect/write has a deadline."""

    def __init__(self, address, members, timeout, on_message, on_invalid):
        self.members = members
        self.timeout = timeout
        self.on_message = on_message
        self.on_invalid = on_invalid
        self.selector = selectors.DefaultSelector()
        self.connections: dict[socket.socket, Connection] = {}
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind(address)
            self.listener.listen(128)
            self.listener.setblocking(False)
            self.selector.register(self.listener, selectors.EVENT_READ, None)
        except BaseException:
            self.listener.close()
            self.selector.close()
            raise

    def send(self, address, message: Message, callback: SendCallback):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        connection = Connection(sock, time.monotonic() + self.timeout,
                                outgoing=True, payload=message.encode(), callback=callback)
        try:
            status = sock.connect_ex(address)
            pending = {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                       errno.EINTR, 10035, 10036, 10037}
            if status not in pending:
                sock.close()
                callback(False, f"connect error {status}")
                return
            self.connections[sock] = connection
            self.selector.register(sock, selectors.EVENT_WRITE, connection)
        except OSError as exc:
            if sock in self.connections:
                self._finish(connection, False, str(exc))
            else:
                sock.close()
                callback(False, str(exc))

    def _finish(self, connection, success=False, reason="", notify=True):
        if self.connections.pop(connection.sock, None) is None:
            return
        self.selector.unregister(connection.sock)
        connection.sock.close()
        if notify and connection.callback:
            connection.callback(success, reason)

    def _accept(self):
        while True:
            try:
                sock, _ = self.listener.accept()
            except BlockingIOError:
                return
            sock.setblocking(False)
            connection = Connection(sock, time.monotonic() + self.timeout)
            self.connections[sock] = connection
            self.selector.register(sock, selectors.EVENT_READ, connection)

    def _write(self, connection):
        error = connection.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if error:
            self._finish(connection, False, f"socket error {error}")
            return
        count = connection.sock.send(connection.payload[connection.offset:])
        if not count:
            self._finish(connection, False, "connection closed while sending")
            return
        connection.offset += count
        if connection.offset == len(connection.payload):
            self._finish(connection, True)

    def _read(self, connection):
        chunk = connection.sock.recv(4096)
        if not chunk:
            if connection.buffer:
                self.on_invalid("Connection closed before newline")
            self._finish(connection)
            return
        connection.buffer.extend(chunk)
        split = connection.buffer.find(b"\n")
        if split == -1:
            if len(connection.buffer) > MAX_FRAME:
                self.on_invalid("Frame exceeds 8 KiB")
                self._finish(connection)
            return
        frame = bytes(connection.buffer[:split])
        extra = connection.buffer[split + 1:]
        self._finish(connection)
        try:
            if extra:
                raise ProtocolError("Only one frame is permitted per connection")
            message = decode(frame, self.members)
        except ProtocolError as exc:
            self.on_invalid(str(exc))
            return
        self.on_message(message)

    def poll(self, timeout=0.02):
        for key, _ in self.selector.select(timeout):
            if key.data is None:
                self._accept()
                continue
            connection = key.data
            if connection.sock not in self.connections:
                continue
            if time.monotonic() >= connection.deadline:
                self._expire(connection)
                continue
            try:
                if connection.outgoing:
                    self._write(connection)
                else:
                    self._read(connection)
            except BlockingIOError:
                continue
            except OSError as exc:
                self._finish(connection, False, str(exc))
        now = time.monotonic()
        for connection in list(self.connections.values()):
            if now >= connection.deadline:
                self._expire(connection)

    def _expire(self, connection):
        if not connection.outgoing:
            self.on_invalid("Incoming frame timed out")
        self._finish(connection, False, "socket deadline expired")

    def close(self):
        for connection in list(self.connections.values()):
            self._finish(connection, notify=False)
        self.selector.unregister(self.listener)
        self.listener.close()
        self.selector.close()
