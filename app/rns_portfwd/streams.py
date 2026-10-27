"""Per-stream socket pumps and the Channel transport adapter."""

from __future__ import annotations

import bz2
import queue
import select
import socket
import threading
import time

from RNS.Channel import CEType, ChannelException

from .constants import (
    COMPRESSION_THRESHOLD,
    MAX_DECOMPRESSED_PAYLOAD,
    WINDOW_UPDATE_DIVISOR,
)
from .credits import CreditWindow
from .errors import ProtocolError
from .protocol import (
    CloseMessage,
    DataMessage,
    OpenErrMessage,
    OpenMessage,
    OpenOkMessage,
    WindowMessage,
)

_EOF = object()


def decompress_payload(
    data: bytes,
    limit: int = MAX_DECOMPRESSED_PAYLOAD,
) -> bytes:
    decompressor = bz2.BZ2Decompressor()
    try:
        out = decompressor.decompress(data, max_length=limit + 1)
    except (OSError, ValueError, EOFError) as exc:
        raise ProtocolError(f"invalid compressed payload: {exc}") from None
    if len(out) > limit or not decompressor.eof or decompressor.unused_data:
        raise ProtocolError("invalid or oversized compressed payload")
    return out


class ChannelTransport:
    """Builds protocol messages and sends them over a Channel."""

    def __init__(
        self,
        channel,
        compress: bool = True,
        closed_event: threading.Event | None = None,
        send_timeout: float = 30.0,
    ):
        self.channel = channel
        self.compress = compress
        self.closed_event = closed_event
        self.send_timeout = send_timeout

    def _send(self, message) -> None:
        deadline = time.monotonic() + self.send_timeout
        while True:
            if self.closed_event is not None and self.closed_event.is_set():
                raise RuntimeError("channel is closed")
            try:
                self.channel.send(message)
                return
            except ChannelException as exc:
                if exc.type != CEType.ME_LINK_NOT_READY:
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def max_payload(self) -> int:
        return max(64, int(self.channel.mdu) - 3)

    def send_open(self, stream_id: int, version: int, service: str, password: str) -> None:
        self._send(
            OpenMessage(
                stream_id=stream_id,
                version=version,
                service=service,
                password=password,
            )
        )

    def send_open_ok(self, stream_id: int, credit: int) -> None:
        self._send(OpenOkMessage(stream_id=stream_id, credit=credit))

    def send_open_err(self, stream_id: int, code: int) -> None:
        self._send(OpenErrMessage(stream_id=stream_id, code=code))

    def send_data(self, stream_id: int, data: bytes, eof: bool = False) -> None:
        payload = data
        compressed = False
        if self.compress and len(data) >= COMPRESSION_THRESHOLD:
            candidate = bz2.compress(data)
            if len(candidate) < len(data):
                payload = candidate
                compressed = True
        self._send(
            DataMessage(
                stream_id=stream_id,
                data=payload,
                eof=eof,
                compressed=compressed,
            )
        )

    def send_window(self, stream_id: int, credit: int) -> None:
        self._send(WindowMessage(stream_id=stream_id, credit=credit))

    def send_close(self, stream_id: int, reason: int) -> None:
        self._send(CloseMessage(stream_id=stream_id, reason=reason))


class StreamSession:
    """Bridges one TCP socket to one multiplexed stream.

    The same class runs on both peers: ``sock`` is always the local TCP side
    and ``transport`` is always the Reticulum Channel.
    """

    def __init__(
        self,
        stream_id: int,
        sock: socket.socket,
        transport: ChannelTransport,
        window: int,
        logger,
        on_closed=None,
        label: str = "",
    ):
        self.stream_id = stream_id
        self.sock = sock
        self.transport = transport
        self.window = window
        self.logger = logger
        self.on_closed = on_closed
        self.label = label

        self._send_credit = CreditWindow(0)
        self._queue: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        self._opened = threading.Event()
        self._lock = threading.Lock()
        self._finish_lock = threading.Lock()
        self._closed_cb_called = False
        self._started = False
        self._threads: list[threading.Thread] = []
        self._local_eof_sent = False
        self._remote_eof_done = False
        self._recv_unacked = 0
        self.open_error: int | None = None
        self.close_reason: str | None = None
        self.opened_at = time.monotonic()
        self.bytes_in = 0
        self.bytes_out = 0

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def opened(self) -> threading.Event:
        return self._opened

    def on_open_ok(self, credit: int) -> None:
        try:
            self._send_credit.add(credit)
        except ValueError:
            self.abort("invalid initial credit", send_close=False)
            return
        self._opened.set()

    def on_open_err(self, code: int) -> None:
        self.open_error = code
        self._opened.set()
        self.abort(f"open rejected with code {code}", send_close=False)

    def send_initial_window(self) -> None:
        try:
            self.transport.send_window(self.stream_id, self.window)
        except Exception as exc:
            self.abort(f"could not send initial window: {exc}")

    def start(self) -> None:
        if self._started or self._closed.is_set():
            return
        self._started = True
        writer = threading.Thread(
            target=self._write_loop,
            name=f"stream-{self.stream_id}-writer",
            daemon=True,
        )
        reader = threading.Thread(
            target=self._read_loop,
            name=f"stream-{self.stream_id}-reader",
            daemon=True,
        )
        self._threads = [writer, reader]
        writer.start()
        reader.start()

    def on_remote_data(self, data: bytes, eof: bool, compressed: bool) -> None:
        if self._closed.is_set():
            return
        if data:
            try:
                if compressed:
                    data = decompress_payload(data)
            except ProtocolError as exc:
                self.abort(f"bad payload: {exc}")
                return
            self.bytes_in += len(data)
            self._queue.put(data)
        if eof:
            self._queue.put(_EOF)

    def on_window(self, credit: int) -> None:
        try:
            self._send_credit.add(credit)
        except ValueError as exc:
            self.abort(f"invalid window: {exc}")

    def on_remote_close(self, reason: int) -> None:
        self.abort(f"remote close reason={reason}", send_close=False)

    def abort(self, reason: str, send_close: bool = True) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self.close_reason = reason
        self.logger.debug("stream %s closing: %s", self.label, reason)
        if send_close:
            try:
                self.transport.send_close(self.stream_id, 1)
            except Exception:
                pass
        self._send_credit.close()
        self._queue.put(_EOF)
        self._opened.set()
        self._close_socket()
        self._notify_closed()

    def _close_socket(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _notify_closed(self) -> None:
        with self._finish_lock:
            if self._closed_cb_called:
                return
            self._closed_cb_called = True
        duration = time.monotonic() - self.opened_at
        self.logger.info(
            "stream %s closed: %s (%d bytes in, %d bytes out, %.1fs)",
            self.label,
            self.close_reason or "closed",
            self.bytes_in,
            self.bytes_out,
            duration,
        )
        if self.on_closed is not None:
            try:
                self.on_closed(self)
            except Exception:
                self.logger.exception("stream close callback failed")

    def _maybe_finish(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            if not (self._local_eof_sent and self._remote_eof_done):
                return
            self._closed.set()
            self.close_reason = "both directions closed"
        self._close_socket()
        self._notify_closed()

    def _write_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _EOF:
                with self._lock:
                    self._remote_eof_done = True
                try:
                    self.sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                self._maybe_finish()
                return
            if self._closed.is_set():
                return
            try:
                self.sock.sendall(item)
            except OSError as exc:
                self.abort(f"local write failed: {exc}")
                return
            if self._closed.is_set():
                return
            self._recv_unacked += len(item)
            if self._recv_unacked >= max(1, self.window // WINDOW_UPDATE_DIVISOR):
                try:
                    self.transport.send_window(self.stream_id, self._recv_unacked)
                except Exception as exc:
                    self.abort(f"could not send window: {exc}")
                    return
                self._recv_unacked = 0

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                readable, _, _ = select.select([self.sock], [], [], 0.5)
            except (OSError, ValueError) as exc:
                self.abort(f"socket select failed: {exc}")
                return
            if self._closed.is_set():
                return
            if not readable:
                continue
            try:
                data = self.sock.recv(self.transport.max_payload())
            except OSError as exc:
                self.abort(f"local read failed: {exc}")
                return
            if not data:
                with self._lock:
                    self._local_eof_sent = True
                try:
                    self.transport.send_data(self.stream_id, b"", eof=True)
                except Exception as exc:
                    self.abort(f"could not send eof: {exc}", send_close=False)
                    return
                self._maybe_finish()
                return
            remaining = memoryview(data)
            while remaining and not self._closed.is_set():
                chunk = remaining[: self.transport.max_payload()]
                taken = self._send_credit.take_up_to(len(chunk), self._closed)
                if taken == 0:
                    return
                try:
                    self.transport.send_data(self.stream_id, bytes(chunk[:taken]))
                except Exception as exc:
                    self.abort(f"could not send data: {exc}")
                    return
                self.bytes_out += taken
                remaining = remaining[taken:]

    def join(self, timeout: float = 1.0) -> None:
        for thread in self._threads:
            thread.join(timeout=timeout)
