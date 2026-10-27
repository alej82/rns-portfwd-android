"""Server: Reticulum destination, request handlers and multiplexed streams."""

from __future__ import annotations

import socket
import threading

import RNS

from .auth import AuthTracker
from .config import Service
from .constants import (
    AUTH_FAILURE_DELAY,
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_BACKEND_TIMEOUT,
    DEFAULT_MAX_CONNECTIONS_PER_SERVICE,
    DEFAULT_MAX_LINKS,
    DEFAULT_MAX_STREAMS,
    DEFAULT_WINDOW,
    MAX_AUTH_FAILURES,
    PROTOCOL_VERSION,
    ErrorCode,
)
from .discovery import (
    init_reticulum,
    load_or_create_identity,
    make_in_destination,
    set_default_app_data,
)
from .mux import Mux
from .protocol import register_message_types
from .registry import ServiceRegistry
from .streams import StreamSession


class LinkContext:
    def __init__(self, link, channel, mux, tracker):
        self.link = link
        self.channel = channel
        self.mux: Mux | None = mux
        self.tracker = tracker
        self.timer: threading.Timer | None = None
        self.closing = False
        self.opened = False
        self.reserved: dict[int, str] = {}


class PortfwdServer:
    def __init__(
        self,
        services: list[Service],
        list_password: str | None,
        identity_path: str,
        name: str,
        config_dir: str | None,
        logger,
        announce: bool = False,
        announce_interval: float = 900.0,
        window: int = DEFAULT_WINDOW,
        compress: bool = True,
        max_links: int = DEFAULT_MAX_LINKS,
        max_streams: int = DEFAULT_MAX_STREAMS,
        max_connections_per_service: int = DEFAULT_MAX_CONNECTIONS_PER_SERVICE,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        backend_timeout: float = DEFAULT_BACKEND_TIMEOUT,
        verbosity: int = 0,
    ):
        self.services = services
        self.list_password = list_password
        self.identity_path = identity_path
        self.name = name
        self.config_dir = config_dir
        self.logger = logger
        self.announce_enabled = announce
        self.announce_interval = announce_interval
        self.window = window
        self.compress = compress
        self.max_links = max_links
        self.max_streams = max_streams
        self.auth_timeout = auth_timeout
        self.backend_timeout = backend_timeout
        self.verbosity = verbosity

        self.registry = ServiceRegistry(
            services=services,
            list_password=list_password,
            max_connections_per_service=max_connections_per_service,
        )
        self.destination = None
        self.identity = None
        self.reticulum = None
        self._links: dict[int, LinkContext] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._announce_thread: threading.Thread | None = None
        self._started = False

    @property
    def hexhash(self) -> str:
        return self.destination.hexhash if self.destination is not None else ""

    @property
    def service_names(self) -> list[str]:
        return [service.name for service in self.services]

    def start(self) -> str:
        self.reticulum = init_reticulum(self.config_dir, self.verbosity)
        self.identity = load_or_create_identity(self.identity_path)
        self.destination = make_in_destination(self.identity)
        set_default_app_data(self.destination, self.name, self.list_password is not None)
        self.destination.set_max_request_size(16384)
        self.destination.set_link_established_callback(self._link_established)
        self.destination.register_request_handler(
            "/list", self._handle_list, allow=RNS.Destination.ALLOW_ALL
        )
        self.destination.register_request_handler(
            "/probe", self._handle_probe, allow=RNS.Destination.ALLOW_ALL
        )
        self.destination.accepts_links(True)

        if self.announce_enabled:
            try:
                self.destination.announce()
            except Exception as exc:
                self.logger.warning("initial announce failed: %s", exc)
            self._announce_thread = threading.Thread(
                target=self._announce_loop, name="announce", daemon=True
            )
            self._announce_thread.start()

        self._started = True
        self.logger.info(
            "server ready: %s (%d services, listing %s)",
            self.destination.hexhash,
            len(self.services),
            "enabled" if self.registry.list_enabled else "disabled",
        )
        return self.destination.hexhash

    def _announce_loop(self) -> None:
        while not self._stop.wait(self.announce_interval):
            if self.destination is None:
                return
            try:
                self.destination.announce()
            except Exception as exc:
                self.logger.warning("announce failed: %s", exc)

    def wait(self) -> None:
        self._stop.wait()

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        with self._lock:
            contexts = list(self._links.values())
            self._links.clear()
        for ctx in contexts:
            ctx.closing = True
            if ctx.timer is not None:
                ctx.timer.cancel()
            try:
                if ctx.mux is not None:
                    ctx.mux.close_all("server shutting down", send_close=True)
            except Exception:
                pass
            try:
                if ctx.link.status != RNS.Link.CLOSED:
                    ctx.link.teardown()
            except Exception:
                pass

        if self.destination is not None:
            for path in ("/list", "/probe"):
                try:
                    self.destination.deregister_request_handler(path)
                except Exception:
                    pass

        try:
            RNS.Reticulum.exit_handler()
        except Exception:
            pass
        self._started = False

    def _link_established(self, link) -> None:
        if self._stop.is_set():
            link.teardown()
            return
        with self._lock:
            if len(self._links) >= self.max_links:
                self.logger.warning("refusing link: maximum number of links reached")
                link.teardown()
                return

        channel = link.get_channel()
        register_message_types(channel)
        ctx = LinkContext(link, channel, None, AuthTracker(max_failures=MAX_AUTH_FAILURES))
        mux = Mux(
            channel=channel,
            window=self.window,
            compress=self.compress,
            max_streams=self.max_streams,
            logger=self.logger,
            is_client=False,
            open_handler=lambda message, ctx=ctx: self._handle_open(ctx, message),
        )
        ctx.mux = mux
        link.set_link_closed_callback(self._link_closed)
        ctx.timer = threading.Timer(self.auth_timeout, self._auth_timeout, args=(ctx,))
        ctx.timer.daemon = True
        ctx.timer.start()
        with self._lock:
            self._links[id(link)] = ctx
        self.logger.debug("link established from %s", getattr(link, "link_id", b"").hex())

    def _auth_timeout(self, ctx: LinkContext) -> None:
        if ctx.closing or ctx.opened or self._stop.is_set():
            return
        self.logger.warning("closing link: no stream opened within authentication timeout")
        try:
            ctx.link.teardown()
        except Exception:
            pass

    def _link_closed(self, link) -> None:
        ctx = None
        with self._lock:
            ctx = self._links.pop(id(link), None)
        if ctx is None:
            return
        ctx.closing = True
        if ctx.timer is not None:
            ctx.timer.cancel()
        if ctx.mux is not None:
            ctx.mux.close_all("link lost", send_close=False)
        with self._lock:
            pending = list(ctx.reserved.items())
            ctx.reserved.clear()
        for _, service_name in pending:
            self.registry.release(service_name)
        self.logger.info("link closed")

    def _send_error(self, ctx: LinkContext, stream_id: int, code: int) -> None:
        try:
            if ctx.mux is not None and not ctx.closing:
                ctx.mux.transport.send_open_err(stream_id, code)
        except Exception:
            pass

    def _handle_open(self, ctx: LinkContext, message) -> None:
        if ctx.closing or self._stop.is_set() or ctx.mux is None:
            return
        service = self.registry.authenticate(message.service, message.password)
        if service is None:
            close_link = ctx.tracker.record_failure()
            self.logger.warning(
                "authentication failed for service %r on link (attempt %d)",
                message.service,
                ctx.tracker.failures,
            )

            def delayed_error():
                self._send_error(ctx, message.stream_id, ErrorCode.AUTH)
                if close_link:
                    try:
                        ctx.link.teardown()
                    except Exception:
                        pass

            timer = threading.Timer(AUTH_FAILURE_DELAY, delayed_error)
            timer.daemon = True
            timer.start()
            return

        if message.version != PROTOCOL_VERSION:
            self._send_error(ctx, message.stream_id, ErrorCode.VERSION)
            return

        if not self.registry.acquire(service.name):
            self.logger.warning("service %s reached its connection limit", service.name)
            self._send_error(ctx, message.stream_id, ErrorCode.LIMIT)
            return

        if not ctx.mux.reserve(message.stream_id):
            self.registry.release(service.name)
            self._send_error(ctx, message.stream_id, ErrorCode.LIMIT)
            return

        ctx.tracker.reset()
        ctx.opened = True
        if ctx.timer is not None:
            ctx.timer.cancel()
        with self._lock:
            ctx.reserved[message.stream_id] = service.name

        threading.Thread(
            target=self._open_backend,
            args=(ctx, service, message.stream_id),
            name=f"open-{service.name}-{message.stream_id}",
            daemon=True,
        ).start()

    def _release_reservation(self, ctx: LinkContext, stream_id: int) -> None:
        with self._lock:
            service_name = ctx.reserved.pop(stream_id, None)
        if service_name is not None:
            self.registry.release(service_name)

    def _open_backend(self, ctx: LinkContext, service: Service, stream_id: int) -> None:
        if ctx.closing:
            self._release_reservation(ctx, stream_id)
            ctx.mux.release_reservation(stream_id)
            return
        try:
            sock = socket.create_connection(
                (service.host, service.port), timeout=self.backend_timeout
            )
            sock.settimeout(None)
        except OSError:
            self.logger.warning("backend for service %s is unavailable", service.name)
            self._release_reservation(ctx, stream_id)
            ctx.mux.release_reservation(stream_id)
            self._send_error(ctx, stream_id, ErrorCode.BACKEND)
            return

        if ctx.closing:
            sock.close()
            self._release_reservation(ctx, stream_id)
            ctx.mux.release_reservation(stream_id)
            return

        stream = StreamSession(
            stream_id=stream_id,
            sock=sock,
            transport=ctx.mux.transport,
            window=self.window,
            logger=self.logger,
            on_closed=lambda closed, ctx=ctx: self._stream_closed(ctx, closed),
            label=f"{service.name}/{stream_id}",
        )
        stream.service_name = service.name  # type: ignore[attr-defined]
        if not ctx.mux.install(stream_id, stream):
            self._release_reservation(ctx, stream_id)
            stream.on_closed = None
            stream.abort("could not register stream", send_close=False)
            self._send_error(ctx, stream_id, ErrorCode.LIMIT)
            return
        with self._lock:
            ctx.reserved.pop(stream_id, None)

        try:
            ctx.mux.transport.send_open_ok(stream_id, self.window)
        except Exception:
            stream.abort("could not confirm open", send_close=False)
            return

        stream.start()
        self.logger.info("stream %s opened", stream.label)

    def _stream_closed(self, ctx: LinkContext, stream: StreamSession) -> None:
        if ctx.mux is not None:
            ctx.mux.remove(stream.stream_id)
        service_name = getattr(stream, "service_name", None)
        if service_name is not None and not getattr(stream, "service_released", False):
            stream.service_released = True  # type: ignore[attr-defined]
            self.registry.release(service_name)

    def _handle_list(self, path, data, request_id, link_id, remote_identity, requested_at):
        if not self.registry.list_enabled:
            return {"ok": False, "error": "disabled"}
        password = None
        if isinstance(data, dict):
            password = data.get("password")
        if not isinstance(password, str) or not self.registry.check_list_password(password):
            import time

            time.sleep(AUTH_FAILURE_DELAY)
            return {"ok": False, "error": "auth"}
        return {"ok": True, "services": self.registry.list_public()}

    def _handle_probe(self, path, data, request_id, link_id, remote_identity, requested_at):
        service_name = None
        password = None
        if isinstance(data, dict):
            service_name = data.get("service")
            password = data.get("password")
        if not isinstance(service_name, str) or not isinstance(password, str):
            return {"ok": False, "error": "auth"}
        public = self.registry.probe(service_name, password)
        if public is None:
            import time

            time.sleep(AUTH_FAILURE_DELAY)
            return {"ok": False, "error": "auth"}
        return {"ok": True, "service": public}
