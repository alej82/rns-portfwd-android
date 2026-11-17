"""Datagram sessions: one UDP flow per multiplexed stream."""

from __future__ import annotations

import queue
import select
import socket
import threading
import time

from .constants import (
    DEFAULT_OPEN_TIMEOUT,
    DEFAULT_UDP_IDLE_TIMEOUT,
    DEFAULT_UDP_QUEUE,
    WINDOW_UPDATE_DIVISOR,
)
from .streams import SessionBase

_CLOSE = object()
_MSG_TRUNC = getattr(socket, "MSG_TRUNC", 0)


class DatagramSession(SessionBase):
    """Bridges one UDP flow to one multiplexed stream.

    The session owns its backend socket on the server. On the client the
    listener socket is shared by several sessions and ``peer`` identifies the
    local source that created this one.
    """

    def __init__(
        self,
        stream_id: int,
        sock: socket.socket,
        transport,
        window: int,
        logger,
        on_closed=None,
        label: str = "",
        peer=None,
        owns_socket: bool = False,
        idle_timeout: float = DEFAULT_UDP_IDLE_TIMEOUT,
        queue_size: int = DEFAULT_UDP_QUEUE,
    ):
        super().__init__(stream_id, transport, window, logger, on_closed, label)
        self.sock = sock
        self.peer = peer
        self.owns_socket = owns_socket
        self.idle_timeout = idle_timeout

        self._out_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._in_queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._peer_window: int | None = None
        self._peer_window_event = threading.Event()
        self._peer_lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._recv_unacked = 0
        self._started = False
        self._threads: list[threading.Thread] = []
        self.dropped_oversized = 0
        self.dropped_queue_full = 0

    @property
    def last_activity(self) -> float:
        return self._last_activity

    def idle_for(self, now: float | None = None) -> float:
        return (now if now is not None else time.monotonic()) - self._last_activity

    def _touch(self) -> None:
        self._last_activity = time.monotonic()

    def max_datagram(self) -> int:
        limit = self.transport.max_datagram()
        with self._peer_lock:
            peer_window = self._peer_window
        if peer_window is not None:
            limit = min(limit, peer_window)
        return max(1, limit)

    def _set_peer_window(self, credit: int) -> None:
        with self._peer_lock:
            if self._peer_window is None or credit > self._peer_window:
                self._peer_window = credit
        self._peer_window_event.set()

    def on_open_ok(self, credit: int) -> None:
        super().on_open_ok(credit)
        self._set_peer_window(credit)

    def on_window(self, credit: int) -> None:
        super().on_window(credit)
        self._set_peer_window(credit)

    def start(self) -> None:
        if self._started or self._closed.is_set():
            return
        self._started = True
        sender = threading.Thread(
            target=self._sender_loop,
            name=f"dgram-{self.stream_id}-sender",
            daemon=True,
        )
        writer = threading.Thread(
            target=self._writer_loop,
            name=f"dgram-{self.stream_id}-writer",
            daemon=True,
        )
        self._threads = [sender, writer]
        if self.owns_socket:
            receiver = threading.Thread(
                target=self._receiver_loop,
                name=f"dgram-{self.stream_id}-receiver",
                daemon=True,
            )
            self._threads.append(receiver)
        for thread in self._threads:
            thread.start()

    def send_local(self, data: bytes) -> bool:
        """Queue one datagram from the local socket; never blocks.

        Returns ``False`` when the datagram is dropped.
        """
        if self._closed.is_set():
            return False
        limit = self.max_datagram()
        if len(data) > limit:
            self.dropped_oversized += 1
            self.logger.debug(
                "dropping oversized datagram on %s: %d > %d bytes",
                self.label,
                len(data),
                limit,
            )
            return False
        try:
            self._out_queue.put_nowait(data)
        except queue.Full:
            self.dropped_queue_full += 1
            self.logger.debug("dropping datagram on %s: queue full", self.label)
            return False
        self._touch()
        return True

    def on_remote_datagram(self, data: bytes) -> None:
        if self._closed.is_set():
            return
        self.bytes_in += len(data)
        self._touch()
        try:
            self._in_queue.put_nowait(data)
        except queue.Full:
            self.dropped_queue_full += 1
            self.logger.debug(
                "dropping remote datagram on %s: local queue full", self.label
            )

    def _sender_loop(self) -> None:
        while not self._closed.is_set():
            item = self._out_queue.get()
            if item is _CLOSE or self._closed.is_set():
                return
            if not self._peer_window_event.wait(DEFAULT_OPEN_TIMEOUT):
                self.abort("no datagram credit window")
                return
            limit = self.max_datagram()
            if len(item) > limit:
                self.dropped_oversized += 1
                self.logger.debug(
                    "dropping oversized datagram on %s: %d > %d bytes",
                    self.label,
                    len(item),
                    limit,
                )
                continue
            if not self._send_credit.take(len(item), self._closed):
                return
            try:
                self.transport.send_datagram(self.stream_id, item)
            except Exception as exc:
                self.abort(f"could not send datagram: {exc}")
                return
            self.bytes_out += len(item)

    def _writer_loop(self) -> None:
        while not self._closed.is_set():
            item = self._in_queue.get()
            if item is _CLOSE or self._closed.is_set():
                return
            try:
                if self.peer is None:
                    self.sock.send(item)
                else:
                    self.sock.sendto(item, self.peer)
            except OSError as exc:
                self.abort(f"local datagram write failed: {exc}")
                return
            self._recv_unacked += len(item)
            if self._recv_unacked >= max(1, self.window // WINDOW_UPDATE_DIVISOR):
                try:
                    self.transport.send_window(self.stream_id, self._recv_unacked)
                except Exception as exc:
                    self.abort(f"could not send window: {exc}")
                    return
                self._recv_unacked = 0

    def _receiver_loop(self) -> None:
        while not self._closed.is_set():
            if self.idle_for() >= self.idle_timeout:
                self.abort("idle timeout")
                return
            try:
                readable, _, _ = select.select([self.sock], [], [], 0.5)
            except (OSError, ValueError) as exc:
                self.abort(f"datagram select failed: {exc}")
                return
            if self._closed.is_set():
                return
            if not readable:
                continue
            data = self.recv_datagram()
            if data is not None:
                self.send_local(data)

    def recv_datagram(self) -> bytes | None:
        """Read one datagram, returning ``None`` when it must be dropped."""
        limit = self.transport.max_datagram()
        try:
            data, _, flags, _ = self.sock.recvmsg(limit + 1)
        except OSError as exc:
            self.abort(f"local datagram read failed: {exc}")
            return None
        if (flags & _MSG_TRUNC) or len(data) > limit:
            self.dropped_oversized += 1
            self.logger.debug(
                "dropping oversized local datagram on %s: more than %d bytes",
                self.label,
                limit,
            )
            return None
        return data

    def _close_local(self) -> None:
        for wake_queue in (self._out_queue, self._in_queue):
            try:
                wake_queue.put_nowait(_CLOSE)
            except queue.Full:
                try:
                    wake_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    wake_queue.put_nowait(_CLOSE)
                except queue.Full:
                    pass
        self._peer_window_event.set()
        if self.owns_socket:
            try:
                self.sock.close()
            except OSError:
                pass

    def join(self, timeout: float = 1.0) -> None:
        for thread in self._threads:
            thread.join(timeout=timeout)
