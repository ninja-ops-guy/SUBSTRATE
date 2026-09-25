"""Linux AF_UNIX transport; identity comes only from SO_PEERCRED."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import socket
import stat
import struct
import threading
import time

from core import Broker, Denied, Peer, canonical, strict_json

MAX_REQUEST = 16384
MAX_RESPONSE = 65536
FRAME_TIMEOUT = 2.0


def peer_credentials(conn: socket.socket) -> Peer:
    pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    if pid <= 0 or uid < 0 or gid < 0:
        raise Denied("peer_credentials_unavailable")
    return Peer(pid, uid, gid)


def read_frame(conn: socket.socket, limit=MAX_REQUEST, timeout=FRAME_TIMEOUT) -> bytes:
    deadline = time.monotonic() + timeout

    def exact(count):
        data = bytearray()
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Denied("frame_timeout")
            conn.settimeout(remaining)
            try:
                part = conn.recv(count - len(data))
            except TimeoutError as exc:
                raise Denied("frame_timeout") from exc
            if not part:
                raise Denied("truncated_frame")
            data.extend(part)
        return bytes(data)

    size = struct.unpack("!I", exact(4))[0]
    if not 0 < size <= limit:
        raise Denied("frame_size_exceeded")
    return exact(size)


def write_frame(conn: socket.socket, value: dict) -> None:
    data = canonical(value).encode()
    if len(data) > MAX_RESPONSE:
        data = b'{"ok":false,"code":"response_limit","may_have_executed":true}'
    conn.settimeout(FRAME_TIMEOUT)
    conn.sendall(struct.pack("!I", len(data)) + data)


class Server:
    def __init__(self, broker: Broker, directory: Path):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o027:
            raise Denied("unsafe_socket_directory")
        self.broker, self.directory = broker, directory
        self.listeners = {}
        self.paths = {}
        self.selector = selectors.DefaultSelector()
        self.slots = {"agent": threading.BoundedSemaphore(8), "admin": threading.BoundedSemaphore(2)}
        try:
            for channel in ("agent", "admin"):
                path = directory / (channel + ".sock")
                if os.path.lexists(path):
                    old = path.lstat()
                    if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.geteuid():
                        raise Denied("unsafe_socket_path")
                    path.unlink()
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    listener.bind(str(path))
                    os.chmod(path, 0o660 if channel == "agent" else 0o600)
                    listener.listen(16)
                    listener.setblocking(False)
                    self.selector.register(listener, selectors.EVENT_READ, channel)
                except BaseException:
                    listener.close()
                    raise
                self.listeners[channel] = listener
                self.paths[path] = path.stat().st_ino
        except BaseException:
            self.close()
            raise

    def serve(self, stop: threading.Event):
        while not stop.is_set():
            for key, _ in self.selector.select(0.1):
                try:
                    conn, _ = key.fileobj.accept()
                except BlockingIOError:
                    continue
                channel = key.data
                if not self.slots[channel].acquire(blocking=False):
                    conn.close()
                    continue
                threading.Thread(target=self._handle, args=(conn, channel), daemon=True).start()

    def _handle(self, conn, channel):
        peer = None
        try:
            with conn:
                peer = peer_credentials(conn)
                # Reject unauthorized admin peers before parsing any supplied authority.
                if channel == "admin" and peer.uid != 0:
                    raise Denied("administrator_required")
                if channel == "agent" and peer.uid not in self.broker.policy.allowed_uids:
                    raise Denied("worker_not_enrolled")
                message = strict_json(read_frame(conn))
                result = self.broker.handle(peer, channel, message)
                write_frame(conn, result)
        except (Denied, OSError, ValueError, RecursionError) as exc:
            if peer is not None:
                self.broker.protocol_denial(peer, channel, str(exc) if isinstance(exc, Denied) else "transport_error")
            # The connection is already closed. The client must treat no response as
            # uncertain and retry only the SAME request ID and identical payload.
        finally:
            self.slots[channel].release()

    def close(self):
        self.selector.close()
        for listener in self.listeners.values():
            listener.close()
        for path, inode in self.paths.items():
            if os.path.lexists(path) and path.lstat().st_ino == inode and stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()


def request(path: str, message: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect(path)
        payload = canonical(message).encode()
        if len(payload) > MAX_REQUEST:
            raise Denied("frame_size_exceeded")
        conn.sendall(struct.pack("!I", len(payload)) + payload)
        return strict_json(read_frame(conn, MAX_RESPONSE, timeout=30))
