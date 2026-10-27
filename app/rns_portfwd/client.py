"""Client: local listeners, one Link per server and stream multiplexing."""

from __future__ import annotations

import random
import socket
import threading
import time

import RNS

from .config import Forward
from .constants import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_STREAMS,
    DEFAULT_OPEN_TIMEOUT,
    DEFAULT_PATH_TIMEOUT,
    DEFAULT_WINDOW,
    PROTOCOL_VERSION,
    ErrorCode,
)
from .discovery import (
    init_reticulum,
    parse_destination_hash,
    resolve_identity,
)
from .errors import AuthError, BindError, PortfwdError, ResolutionError
from .link_manager import LinkManager
from .mux import Mux
from .protocol import register_message_types
from .streams import StreamSession


def _format_endpoint(host: str, port: int) -> str:
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def link_request(link, path: str, data, timeout: float):
    event = threading.Event()
    result: dict = {}

    def on_response(receipt):
        result["response"] = receipt.response
        event.set()

    def on_failed(receipt):
        result["failed"] = True
        event.set()

    try:
        receipt = link.request(
            path,
            data=data,
            response_callback=on_response,
            failed_callback=on_failed,
            timeout=timeout,
        )
    except Exception as exc:
        raise ResolutionError(
            f"request {path} failed ({type(exc).__name__})"
        ) from None
    if receipt is False:
        raise ResolutionError(f"could not send request {path}")
    if not event.wait(timeout + 5):
        raise ResolutionError(f"request {path} timed out")
    if result.get("failed"):
        raise ResolutionError(f"request {path} failed")
    return result.get("response")


class PortfwdClient:
    def __init__(
        self,
        server_hash: bytes,
        forwards: list[Forward],
        bind: str,
        logger,
        config_dir: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        path_timeout: float = DEFAULT_PATH_TIMEOUT,
        window: int = DEFAULT_WINDOW,
        max_streams: int = DEFAULT_MAX_STREAMS,
        compress: bool = True,
        verbosity: int = 0,
    ):
        self.server_hash = server_hash
        self.forwards = forwards
        self.bind = bind
        self.logger = logger
        self.config_dir = config_dir
        self.connect_timeout = connect_timeout
        self.path_timeout = path_timeout
        self.window = window
        self.max_streams = max_streams
        self.compress = compress
        self.verbosity = verbosity

        self._stop = threading.Event()
        self._link_ready = threading.Event()
        self._lock = threading.Lock()
        self.link_manager: LinkManager | None = None
        self.link = None
        self.mux: Mux | None = None
        self.listeners: list[tuple[Forward, socket.socket, int]] = []
        self._accept_threads: list[threading.Thread] = []
        self._supervisor: threading.Thread | None = None
        self._identity = None
        self._started_rns = False

    def run(self) -> None:
        self._identity = self._resolve()
        self._connect_once()
        self._probe_all()
        self._create_listeners()
        self._supervisor = threading.Thread(
            target=self._reconnect_loop, name="reconnect", daemon=True
        )
        self._supervisor.start()
        for forward, listener, actual_port in self.listeners:
            print(
                f"{forward.name} -> {_format_endpoint(self.bind, actual_port)}",
                flush=True,
            )
            thread = threading.Thread(
                target=self._accept_loop,
                args=(forward, listener),
                name=f"accept-{forward.name}",
                daemon=True,
            )
            thread.start()
            self._accept_threads.append(thread)
        self.logger.info("client ready with %d forward(s)", len(self.forwards))
        self._stop.wait()
        self._shutdown()

    def _resolve(self):
        self._started_rns = True
        init_reticulum(self.config_dir, self.verbosity)
        return resolve_identity(self.server_hash, self.path_timeout)

    def _connect_once(self) -> None:
        if self._identity is None:
            raise ResolutionError("server identity is not resolved")
        manager = LinkManager(
            self._identity,
            timeout=self.connect_timeout,
            on_closed=self._handle_link_closed,
        )
        link = manager.open()
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
        with self._lock:
            self.link_manager = manager
            self.link = link
            self.mux = mux
        self._link_ready.set()
        self.logger.info(
            "connected to %s", self.server_hash.hex()
        )

    def _handle_link_closed(self, link) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            if self.link is not link and self.link is not None:
                return
            self._link_ready.clear()
            mux = self.mux
            self.mux = None
            self.link = None
        self.logger.warning("link closed; closing live streams and reconnecting")
        if mux is not None:
            mux.close_all("link lost", send_close=False)

    def _reconnect_loop(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            if self._link_ready.is_set():
                self._stop.wait(0.5)
                delay = 1.0
                continue
            jittered = delay * random.uniform(0.8, 1.2)
            if self._stop.wait(jittered):
                break
            try:
                self._connect_once()
                delay = 1.0
            except Exception as exc:
                self.logger.warning("reconnect failed: %s", exc)
                delay = min(60.0, delay * 2.0)

    def _probe_all(self) -> None:
        link = self.link
        if link is None:
            raise ResolutionError("no active link for probing")
        checked: set[str] = set()
        for forward in self.forwards:
            if forward.name in checked:
                continue
            checked.add(forward.name)
            response = link_request(
                link,
                "/probe",
                {"service": forward.name, "password": forward.password},
                self.connect_timeout,
            )
            if not isinstance(response, dict) or not response.get("ok"):
                raise AuthError(
                    f"authentication failed for service {forward.name!r}"
                )

    def _create_listeners(self) -> None:
        created: list[tuple[Forward, socket.socket, int]] = []
        try:
            for forward in self.forwards:
                family = socket.AF_INET6 if ":" in self.bind else socket.AF_INET
                sock = socket.create_server(
                    (self.bind, forward.local_port), family=family
                )
                sock.settimeout(0.5)
                actual_port = sock.getsockname()[1]
                created.append((forward, sock, actual_port))
        except OSError as exc:
            for _, sock, _ in created:
                try:
                    sock.close()
                except OSError:
                    pass
            raise BindError(f"could not bind {self.bind}: {exc}") from None
        self.listeners = created

    def _accept_loop(self, forward: Forward, listener: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(
                target=self._handle_connection,
                args=(forward, conn),
                name=f"conn-{forward.name}",
                daemon=True,
            ).start()

    def _handle_connection(self, forward: Forward, conn: socket.socket) -> None:
        if not self._link_ready.wait(self.connect_timeout):
            self.logger.warning(
                "closing connection for %s: no Link within %.0fs",
                forward.name,
                self.connect_timeout,
            )
            conn.close()
            return

        mux = self.mux
        if mux is None:
            conn.close()
            return
        if not mux.wait_for_slot(self.connect_timeout, self._stop):
            self.logger.warning(
                "closing connection for %s: stream limit reached", forward.name
            )
            conn.close()
            return

        stream_id = mux.allocate_id()
        if stream_id is None:
            self.logger.error(
                "stream identifiers exhausted; recycling the Link"
            )
            conn.close()
            self._recycle_link()
            return

        if not mux.reserve(stream_id):
            conn.close()
            return

        stream = StreamSession(
            stream_id=stream_id,
            sock=conn,
            transport=mux.transport,
            window=self.window,
            logger=self.logger,
            on_closed=lambda closed, mux=mux: self._stream_closed(mux, closed),
            label=f"{forward.name}/{stream_id}",
        )
        if not mux.install(stream_id, stream):
            mux.release_reservation(stream_id)
            conn.close()
            return

        try:
            mux.transport.send_open(
                stream_id, PROTOCOL_VERSION, forward.name, forward.password
            )
        except Exception as exc:
            mux.remove(stream_id)
            stream.on_closed = None
            stream.abort(f"could not send OPEN: {exc}", send_close=False)
            return

        if not stream.opened.wait(DEFAULT_OPEN_TIMEOUT):
            stream.abort("open timeout")
            return
        if stream.open_error is not None:
            self._log_open_error(forward.name, stream.open_error)
            return
        if stream.closed:
            return

        stream.send_initial_window()
        stream.start()
        self.logger.info("stream %s opened", stream.label)

    def _log_open_error(self, service: str, code: int) -> None:
        if code == ErrorCode.AUTH:
            self.logger.error("authentication failed for service %s", service)
        elif code == ErrorCode.BACKEND:
            self.logger.error("backend for service %s is unavailable", service)
        elif code == ErrorCode.LIMIT:
            self.logger.error("service %s is at its connection limit", service)
        elif code == ErrorCode.VERSION:
            self.logger.error("server rejected the protocol version")
        else:
            self.logger.error("server rejected the open for service %s", service)

    def _stream_closed(self, mux: Mux, stream: StreamSession) -> None:
        mux.remove(stream.stream_id)

    def _recycle_link(self) -> None:
        with self._lock:
            link = self.link
            self._link_ready.clear()
        if link is not None:
            try:
                link.teardown()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        self._shutdown()

    def _shutdown(self) -> None:
        for _, listener, _ in self.listeners:
            try:
                listener.close()
            except OSError:
                pass
        self.listeners = []
        with self._lock:
            mux = self.mux
            manager = self.link_manager
            self.mux = None
            self.link = None
            self.link_manager = None
        if mux is not None:
            mux.close_all("shutting down", send_close=True)
        if manager is not None:
            manager.close()
        if self._started_rns:
            try:
                RNS.Reticulum.exit_handler()
            except Exception:
                pass
            self._started_rns = False


def run_list(
    server_hash_text: str,
    list_password: str | None,
    logger,
    config_dir: str | None,
    verbosity: int = 0,
    path_timeout: float = DEFAULT_PATH_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    wait_seconds: float = 0.0,
    refresh: float = 0.0,
) -> None:
    destination_hash = parse_destination_hash(server_hash_text)
    init_reticulum(config_dir, verbosity)
    try:
        identity = resolve_identity(
            destination_hash, max(path_timeout, wait_seconds)
        )
        manager = LinkManager(identity, timeout=connect_timeout)
        link = manager.open()
        try:
            while True:
                response = link_request(
                    link,
                    "/list",
                    {"password": list_password or ""},
                    connect_timeout,
                )
                if not isinstance(response, dict) or not response.get("ok"):
                    error = response.get("error") if isinstance(response, dict) else None
                    if error == "disabled":
                        raise PortfwdError("listing is disabled on the server")
                    raise AuthError("enumeration authentication failed")
                _print_services(response.get("services", []))
                if refresh <= 0:
                    return
                time.sleep(refresh)
        finally:
            manager.close()
    finally:
        RNS.Reticulum.exit_handler()


def _print_services(services: list[dict]) -> None:
    print(f"{'SERVICE':<24} {'PROTOCOL':<10} AUTH")
    for service in services:
        print(
            f"{service.get('name', ''):<24} "
            f"{service.get('protocol', 'tcp'):<10} "
            f"{'yes' if service.get('auth') else 'no'}"
        )
    print(flush=True)


def run_probe(
    server_hash_text: str,
    service: str,
    password: str,
    logger,
    config_dir: str | None,
    verbosity: int = 0,
    path_timeout: float = DEFAULT_PATH_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> None:
    destination_hash = parse_destination_hash(server_hash_text)
    init_reticulum(config_dir, verbosity)
    try:
        identity = resolve_identity(destination_hash, path_timeout)
        manager = LinkManager(identity, timeout=connect_timeout)
        link = manager.open()
        try:
            response = link_request(
                link,
                "/probe",
                {"service": service, "password": password},
                connect_timeout,
            )
            if not isinstance(response, dict) or not response.get("ok"):
                raise AuthError(
                    f"authentication failed for service {service!r}"
                )
            public = response.get("service", {})
            print(
                f"{public.get('name', service)}: ok "
                f"({public.get('protocol', 'tcp')}, auth="
                f"{'yes' if public.get('auth') else 'no'})"
            )
        finally:
            manager.close()
    finally:
        RNS.Reticulum.exit_handler()
