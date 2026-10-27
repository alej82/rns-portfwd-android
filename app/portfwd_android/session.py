import math
import random
import threading
import time

from rns_portfwd.constants import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_STREAMS,
    DEFAULT_OPEN_TIMEOUT,
    DEFAULT_PATH_TIMEOUT,
    DEFAULT_WINDOW,
    PROTOCOL_VERSION,
    ErrorCode,
)
from rns_portfwd.errors import AuthError, ResolutionError
from rns_portfwd.logging import get_logger
from rns_portfwd.mux import Mux
from rns_portfwd.protocol import register_message_types
from rns_portfwd.streams import StreamSession

from . import rns_ops
from .listeners import BindFailure, open_all
from .validation import ValidationError


class Retryable(RuntimeError):
    def __init__(self, code, message="", stage="resolve"):
        super().__init__(message or code)
        self.code = code
        self.stage = stage


class Terminal(RuntimeError):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


def _jittered(attempt):
    base = min(60.0, float(2 ** max(0, attempt - 1)))
    return max(0.5, base * random.uniform(0.8, 1.2))


class Session:
    def __init__(
        self,
        engine,
        server_hash,
        forwards,
        rns_dir,
        shared_port,
        control_port,
        require_shared,
        path_timeout=DEFAULT_PATH_TIMEOUT,
        connect_timeout=DEFAULT_CONNECT_TIMEOUT,
        window=DEFAULT_WINDOW,
        max_streams=DEFAULT_MAX_STREAMS,
        compress=True,
        logger=None,
    ):
        self.engine = engine
        self.server_hash = server_hash
        self.server_hash_bytes = bytes.fromhex(server_hash)
        self.forward_specs = [dict(forward) for forward in forwards]
        self.rns_dir = rns_dir
        self.shared_port = shared_port
        self.control_port = control_port
        self.require_shared = require_shared
        self.path_timeout = path_timeout
        self.connect_timeout = connect_timeout
        self.window = window
        self.max_streams = max_streams
        self.compress = compress
        self.logger = logger if logger is not None else get_logger()

        self.state = "idle"
        self.wanted = False
        self.error = None
        self.error_code = None
        self.retry_attempt = 0
        self.retry_in = None
        self.link_ready = False
        self.link = None
        self.mux = None
        self.identity = None
        self.listeners = []
        self._accept_threads = []
        self._active = {}
        self._forward_stats = []
        for forward in self.forward_specs:
            self._forward_stats.append(
                {
                    "name": forward["name"],
                    "requested_port": int(forward["local_port"]),
                    "actual_port": None,
                    "streams": 0,
                    "connections": 0,
                    "bytes_in": 0,
                    "bytes_out": 0,
                    "ipv6": False,
                    "degraded": False,
                    "last_error": None,
                }
            )

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._link_lost = threading.Event()
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread is not None:
                return self
            self.wanted = True
            self._thread = threading.Thread(target=self._run, name="portfwd-session", daemon=True)
            self._thread.start()
        return self

    @property
    def active(self):
        return self.wanted or self.state in ("resolving", "linking", "probing", "listening", "reconnecting")

    def _publish(self, event_type, **fields):
        self.engine._publish_session(self, event_type, **fields)

    def _set_state(self, state, **fields):
        with self._lock:
            self.state = state
            if "retry_attempt" in fields:
                self.retry_attempt = fields.pop("retry_attempt")
            if "retry_in" in fields:
                self.retry_in = fields.pop("retry_in")
        self._publish(
            "session_state",
            state=state,
            retry_attempt=self.retry_attempt,
            retry_in=self.retry_in,
            **fields,
        )

    def _run(self):
        try:
            self._initial_connect()
            self._open_listeners()
            self._start_accept_threads()
            self._supervise()
        except Terminal as error:
            self._fail(error.code, str(error))
        except Retryable:
            self._fail("link_failed", "could not establish the session")
        except rns_ops.Cancelled:
            pass
        except Exception as error:
            self.logger.exception("session failed")
            self._fail("internal", f"{type(error).__name__}: {error}")
        finally:
            self._close_listeners()
            self._cleanup_link(send_close=False)
            with self._lock:
                if self.state not in ("error",):
                    self.state = "idle"
                    self.wanted = False
                    self.retry_in = None
            self._publish("session_state", state=self.state, retry_attempt=self.retry_attempt)

    def _initial_connect(self):
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            with self._lock:
                self.retry_attempt = attempt
                self.retry_in = None
            try:
                self._establish(initial=True)
                with self._lock:
                    self.retry_attempt = 0
                    self.retry_in = None
                return
            except Retryable as error:
                self._cleanup_link(send_close=False)
                if not self._sleep_retry(attempt, error.code):
                    raise rns_ops.Cancelled() from None
        raise rns_ops.Cancelled()

    def _establish(self, initial=True):
        # Re-resolve on every attempt: after a shared-instance outage the
        # cached identity is still valid, but the RNS path table is not.
        self._set_state("resolving", retry_attempt=self.retry_attempt)
        try:
            self.identity = rns_ops.resolve_identity(
                self.server_hash_bytes, self.path_timeout, self._stop
            )
        except ResolutionError as error:
            raise Retryable("no_path", str(error), "resolve") from None

        self._set_state("linking", retry_attempt=self.retry_attempt)
        try:
            link = rns_ops.open_link(
                self.identity,
                self.connect_timeout,
                self._stop,
                on_closed=self._on_link_closed,
            )
        except ResolutionError as error:
            raise Retryable("link_failed", str(error), "link") from None
        with self._lock:
            self.link = link
            self.link_ready = False

        self._set_state("probing", retry_attempt=self.retry_attempt)
        try:
            self._probe_all(link)
        except AuthError as error:
            rns_ops.close_link(link)
            with self._lock:
                self.link = None
            raise Terminal("auth", str(error)) from None
        except ResolutionError as error:
            rns_ops.close_link(link)
            with self._lock:
                self.link = None
            raise Retryable("link_failed", str(error), "probe") from None
        except Exception as error:
            rns_ops.close_link(link)
            with self._lock:
                self.link = None
            raise Retryable("link_failed", f"{type(error).__name__}: {error}", "probe") from None

        try:
            channel = link.get_channel()
            register_message_types(channel)
            mux = Mux(
                channel=channel,
                window=self.window,
                compress=self.compress,
                max_streams=self.max_streams,
                logger=self.logger,
                is_client=True,
            )
        except Exception as error:
            rns_ops.close_link(link)
            with self._lock:
                self.link = None
            raise Retryable("link_failed", f"{type(error).__name__}: {error}", "link") from None

        with self._lock:
            self.mux = mux
            self.link_ready = True
            self._link_lost.clear()
            self.error = None
            self.error_code = None
            self.retry_in = None
            self.retry_attempt = 0
            self.state = "probing" if initial else "listening"
        self._publish(
            "session_state",
            state=self.state,
            retry_attempt=0,
            retry_in=None,
            link=True,
        )

    def _probe_all(self, link):
        checked = set()
        for forward in self.forward_specs:
            if forward["name"] in checked:
                continue
            checked.add(forward["name"])
            response = rns_ops.link_request(
                link,
                "/probe",
                {"service": forward["name"], "password": forward["password"]},
                self.connect_timeout,
                self._stop,
            )
            if not isinstance(response, dict) or not response.get("ok"):
                raise AuthError("authentication failed")

    def _sleep_retry(self, attempt, code):
        delay = _jittered(attempt)
        deadline = time.monotonic() + delay
        with self._lock:
            self.retry_attempt = attempt
            self.retry_in = int(math.ceil(delay))
        self._publish(
            "session_retry",
            attempt=attempt,
            code=code,
            delay=round(delay, 2),
            retry_in=self.retry_in,
        )
        last = self.retry_in
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    self.retry_in = None
                return True
            value = int(math.ceil(remaining))
            if value != last:
                last = value
                with self._lock:
                    self.retry_in = value
            self._stop.wait(min(0.2, remaining))
        with self._lock:
            self.retry_in = None
        return False

    def _supervise(self):
        while not self._stop.is_set():
            if self._link_lost.wait(0.2):
                self._link_lost.clear()
                self._set_state("reconnecting", retry_attempt=0, retry_in=None)
                self._cleanup_link(send_close=False)
                attempt = 0
                while not self._stop.is_set():
                    attempt += 1
                    try:
                        self._establish(initial=False)
                        break
                    except Terminal:
                        raise
                    except Retryable as error:
                        if not self._sleep_retry(attempt, error.code):
                            return
                continue
            with self._lock:
                if not self.link_ready:
                    self._link_lost.set()
                    continue

    def _on_link_closed(self, link):
        if self._stop.is_set():
            return
        with self._lock:
            if self.link is not link:
                return
            self.link_ready = False
        self._set_state("reconnecting", retry_attempt=0, retry_in=None)
        self._publish("link_lost")
        self._link_lost.set()

    def _open_listeners(self):
        try:
            self.listeners = open_all(self.forward_specs)
        except BindFailure as error:
            self._close_listeners()
            raise Terminal(error.code, str(error)) from None
        except ValidationError as error:
            self._close_listeners()
            raise Terminal(error.code, str(error)) from None
        except Exception as error:
            self._close_listeners()
            raise Terminal("bind_failed", f"{type(error).__name__}: {error}") from None

        with self._lock:
            for index, bound in enumerate(self.listeners):
                if index >= len(self._forward_stats):
                    break
                stats = self._forward_stats[index]
                bound.stats = stats
                stats["actual_port"] = bound.actual_port
                stats["ipv6"] = bound.ipv6
                stats["degraded"] = bound.degraded
            self.state = "listening"
        self._publish(
            "session_state",
            state="listening",
            retry_attempt=0,
            retry_in=None,
            forwards=self.snapshot_forwards(),
        )

    def _start_accept_threads(self):
        for bound in self.listeners:
            for sock in list(bound.sockets):
                thread = threading.Thread(
                    target=self._accept_loop,
                    args=(bound, sock),
                    name=f"accept-{bound.name}-{bound.actual_port}",
                    daemon=True,
                )
                thread.start()
                self._accept_threads.append(thread)

    def _accept_loop(self, bound, listener):
        while not self._stop.is_set():
            try:
                conn, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._handle_connection,
                args=(bound, conn),
                name=f"conn-{bound.name}",
                daemon=True,
            ).start()

    def _wait_link(self):
        deadline = time.monotonic() + self.connect_timeout
        while not self._stop.is_set():
            with self._lock:
                if self.link_ready and self.mux is not None:
                    return self.mux
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)
        return None

    def _wait_opened(self, stream):
        deadline = time.monotonic() + DEFAULT_OPEN_TIMEOUT
        while not stream.opened.is_set():
            if self._stop.is_set():
                return False
            if time.monotonic() >= deadline:
                return False
            stream.opened.wait(0.1)
        return True

    def _handle_connection(self, bound, conn):
        stats = getattr(bound, "stats", None)
        if stats is not None:
            with self._lock:
                stats["connections"] += 1
        try:
            mux = self._wait_link()
            if mux is None:
                self._record_reject(stats, "link unavailable")
                self._close_socket(conn)
                return
            if not mux.wait_for_slot(self.connect_timeout, self._stop):
                self._record_reject(stats, "stream limit")
                self._close_socket(conn)
                return
            stream_id = mux.allocate_id()
            if stream_id is None:
                self._record_reject(stats, "stream ids exhausted")
                self._close_socket(conn)
                return
            if not mux.reserve(stream_id):
                self._record_reject(stats, "reservation failed")
                self._close_socket(conn)
                return

            forward = bound.forward or {}
            if not forward:
                mux.release_reservation(stream_id)
                self._record_reject(stats, "forward missing")
                self._close_socket(conn)
                return

            stream = StreamSession(
                stream_id=stream_id,
                sock=conn,
                transport=mux.transport,
                window=self.window,
                logger=self.logger,
                on_closed=lambda closed, name=bound.name, stats=stats: self._stream_closed(
                    stats, name, closed
                ),
                label=f"{bound.name}/{stream_id}",
            )
            if not mux.install(stream_id, stream):
                mux.release_reservation(stream_id)
                stream.on_closed = None
                stream.abort("could not install stream", send_close=False)
                return
            with self._lock:
                self._active[stream_id] = {
                    "stream": stream,
                    "name": bound.name,
                    "stats": stats,
                }
                if stats is not None:
                    stats["streams"] += 1
            try:
                mux.transport.send_open(
                    stream_id, PROTOCOL_VERSION, forward["name"], forward["password"]
                )
            except Exception as error:
                mux.remove(stream_id)
                stream.on_closed = None
                stream.abort(f"could not send OPEN: {error}", send_close=False)
                return

            if not self._wait_opened(stream):
                stream.abort("open timeout")
                return
            if stream.open_error is not None:
                code = int(stream.open_error)
                if code == int(ErrorCode.VERSION):
                    raise Terminal("version", "server rejected the protocol version")
                if stats is not None:
                    with self._lock:
                        stats["last_error"] = (
                            "auth" if code == int(ErrorCode.AUTH) else "backend"
                        )
                return
            if stream.closed:
                return
            stream.send_initial_window()
            stream.start()
            self._publish(
                "stream_open",
                name=bound.name,
                port=bound.actual_port,
                stream_id=stream_id,
            )
        except Terminal:
            self._fail("version", "server rejected the protocol version")
        except Exception as error:
            self.logger.debug("connection setup failed: %s", error)
            self._record_reject(stats, "stream setup failed")

    @staticmethod
    def _close_socket(sock):
        try:
            sock.close()
        except OSError:
            pass

    def _record_reject(self, stats, reason):
        if stats is not None:
            with self._lock:
                stats["last_error"] = reason
        self._publish("stream_rejected", reason=reason, name=stats["name"] if stats else None)

    def _stream_closed(self, stats, name, stream):
        with self._lock:
            self._active.pop(stream.stream_id, None)
            if stats is not None:
                stats["streams"] = max(0, stats["streams"] - 1)
                stats["bytes_in"] += int(stream.bytes_in)
                stats["bytes_out"] += int(stream.bytes_out)
                if stream.close_reason and "open rejected" in str(stream.close_reason):
                    stats["last_error"] = "auth"
            if self.mux is not None and self.mux.get(stream.stream_id) is stream:
                self.mux.remove(stream.stream_id)
            duration = time.monotonic() - stream.opened_at
            bytes_in = int(stream.bytes_in)
            bytes_out = int(stream.bytes_out)
            reason = stream.close_reason or "closed"
        self._publish(
            "stream_close",
            name=name,
            stream_id=stream.stream_id,
            duration=round(duration, 3),
            bytes_in=bytes_in,
            bytes_out=bytes_out,
            reason=reason,
        )

    def _fail(self, code, message):
        with self._lock:
            if self.state == "error":
                return
            self.state = "error"
            self.wanted = False
            self.error_code = code
            self.error = message
            self.retry_in = None
        self._stop.set()
        self._link_lost.set()
        self._publish("error", code=code, message=message)
        self._publish("session_state", state="error", error_code=code)
        self._close_listeners()
        self._cleanup_link(send_close=True)

    def _cleanup_link(self, send_close):
        with self._lock:
            mux = self.mux
            link = self.link
            self.mux = None
            self.link = None
            self.link_ready = False
            self._active.clear()
            for stats in self._forward_stats:
                stats["streams"] = 0
        if mux is not None:
            try:
                mux.close_all("link closed", send_close=send_close)
            except Exception:
                pass
        rns_ops.close_link(link)

    def _close_listeners(self):
        with self._lock:
            listeners = self.listeners
            self.listeners = []
        for bound in listeners:
            bound.close()

    def stop(self):
        with self._lock:
            self.wanted = False
        self._stop.set()
        self._link_lost.set()
        self._close_listeners()
        self._cleanup_link(send_close=True)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            self.state = "idle"
            self.error = None
            self.error_code = None
            self.retry_attempt = 0
            self.retry_in = None
        self._publish("session_state", state="idle", retry_attempt=0, retry_in=None)

    def snapshot(self):
        with self._lock:
            forwards = []
            live = {}
            for entry in self._active.values():
                live.setdefault(id(entry["stats"]), []).append(entry["stream"])
            for stats in self._forward_stats:
                item = {
                    "name": stats["name"],
                    "requested_port": stats["requested_port"],
                    "actual_port": stats["actual_port"],
                    "streams": stats["streams"],
                    "connections": stats["connections"],
                    "bytes_in": stats["bytes_in"],
                    "bytes_out": stats["bytes_out"],
                    "ipv6": stats["ipv6"],
                    "degraded": stats["degraded"],
                    "last_error": stats["last_error"],
                }
                for stream in live.get(id(stats), []):
                    item["bytes_in"] += int(stream.bytes_in)
                    item["bytes_out"] += int(stream.bytes_out)
                forwards.append(item)
            return {
                "state": self.state,
                "wanted": self.wanted,
                "retry_attempt": self.retry_attempt,
                "retry_in": self.retry_in,
                "error_code": self.error_code,
                "error": self.error,
                "link": bool(self.link_ready),
                "server_hash": self.server_hash,
                "forwards": forwards,
                "counters": {
                    "streams": len(self._active),
                    "connections": sum(stats["connections"] for stats in self._forward_stats),
                    "bytes_in": sum(item["bytes_in"] for item in forwards),
                    "bytes_out": sum(item["bytes_out"] for item in forwards),
                },
            }

    def snapshot_forwards(self):
        return self.snapshot()["forwards"]
