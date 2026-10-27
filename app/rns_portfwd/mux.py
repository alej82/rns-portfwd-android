"""Stream registry, id allocation and message dispatch for a Link."""

from __future__ import annotations

import threading
from typing import cast

from .constants import STREAM_ID_MAX
from .protocol import (
    CloseMessage,
    DataMessage,
    OpenErrMessage,
    OpenMessage,
    OpenOkMessage,
    WindowMessage,
)
from .streams import ChannelTransport, StreamSession


class Mux:
    def __init__(
        self,
        channel,
        window: int,
        compress: bool,
        max_streams: int,
        logger,
        is_client: bool = False,
        open_handler=None,
    ):
        self._closed_event = threading.Event()
        self.transport = ChannelTransport(
            channel, compress=compress, closed_event=self._closed_event
        )
        self.window = window
        self.max_streams = max_streams
        self.logger = logger
        self.is_client = is_client
        self.open_handler = open_handler

        self._streams: dict[int, StreamSession] = {}
        self._pending: set[int] = set()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._next_id = 1 if is_client else None
        self._closed = False

        channel.add_message_handler(self.handle)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._streams) + len(self._pending)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def allocate_id(self) -> int | None:
        if not self.is_client:
            raise RuntimeError("only the client allocates stream ids")
        with self._lock:
            while self._next_id is not None and self._next_id <= STREAM_ID_MAX:
                candidate = self._next_id
                self._next_id += 1
                if candidate not in self._streams and candidate not in self._pending:
                    return candidate
            return None

    def wait_for_slot(self, timeout: float, stop_event: threading.Event) -> bool:
        with self._cond:
            while len(self._streams) + len(self._pending) >= self.max_streams:
                if self._closed or stop_event.is_set():
                    return False
                if not self._cond.wait(timeout=min(timeout, 0.2)):
                    timeout -= 0.2
                    if timeout <= 0:
                        return False
            return not self._closed

    def reserve(self, stream_id: int) -> bool:
        with self._lock:
            if self._closed or stream_id in self._streams or stream_id in self._pending:
                return False
            if len(self._streams) + len(self._pending) >= self.max_streams:
                return False
            self._pending.add(stream_id)
            return True

    def install(self, stream_id: int, stream: StreamSession) -> bool:
        with self._lock:
            if self._closed:
                self._pending.discard(stream_id)
                return False
            self._pending.discard(stream_id)
            if stream_id in self._streams:
                return False
            self._streams[stream_id] = stream
            self._cond.notify_all()
            return True

    def release_reservation(self, stream_id: int) -> None:
        with self._lock:
            self._pending.discard(stream_id)
            self._cond.notify_all()

    def get(self, stream_id: int) -> StreamSession | None:
        with self._lock:
            return self._streams.get(stream_id)

    def remove(self, stream_id: int) -> None:
        with self._lock:
            self._streams.pop(stream_id, None)
            self._pending.discard(stream_id)
            self._cond.notify_all()

    def handle(self, message) -> bool:
        if isinstance(message, OpenMessage):
            if self.open_handler is not None:
                try:
                    self.open_handler(message)
                except Exception:
                    self.logger.exception("open handler failed")
            return True

        stream_id = getattr(message, "stream_id", None)
        if not isinstance(stream_id, int):
            return True
        stream = self.get(stream_id)
        if stream is None:
            return True

        try:
            if isinstance(message, DataMessage):
                stream.on_remote_data(message.data, message.eof, message.compressed)
            elif isinstance(message, WindowMessage):
                stream.on_window(cast(int, getattr(message, "credit", 0)))
            elif isinstance(message, CloseMessage):
                stream.on_remote_close(cast(int, getattr(message, "reason", 0)))
            elif isinstance(message, OpenOkMessage):
                stream.on_open_ok(cast(int, getattr(message, "credit", 0)))
            elif isinstance(message, OpenErrMessage):
                stream.on_open_err(cast(int, getattr(message, "code", 0)))
        except Exception:
            self.logger.exception("error dispatching stream message")
            stream.abort("dispatch error")
        return True

    def close_all(self, reason: str, send_close: bool = False) -> None:
        self._closed_event.set()
        with self._lock:
            self._closed = True
            streams = list(self._streams.values())
            self._streams.clear()
            self._pending.clear()
            self._cond.notify_all()
        for stream in streams:
            try:
                stream.abort(reason, send_close=send_close)
            except Exception:
                self.logger.exception("error closing stream")
