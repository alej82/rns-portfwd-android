import os
import threading

from rns_portfwd.constants import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_STREAMS,
    DEFAULT_PATH_TIMEOUT,
    DEFAULT_WINDOW,
)
from rns_portfwd.errors import ResolutionError
from rns_portfwd.logging import get_logger, register_secret

from . import logbridge, rns_ops
from .events import EventBus
from .paths import prepare_environment
from .reticulum import (
    INSTANCE_CONTROL_PORT,
    SHARED_INSTANCE_PORT,
    SharedInstanceUnavailable,
    preflight,
    start_reticulum,
)
from .session import Session
from .validation import (
    ValidationError,
    validate_config,
    validate_hash,
    validate_name,
    validate_password,
)


class EngineError(RuntimeError):
    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code = code
        self.field = field


class Engine:
    def __init__(
        self,
        rns_dir=None,
        config_dir=None,
        shared_port=SHARED_INSTANCE_PORT,
        control_port=INSTANCE_CONTROL_PORT,
        require_shared=True,
        path_timeout=DEFAULT_PATH_TIMEOUT,
        connect_timeout=DEFAULT_CONNECT_TIMEOUT,
        window=DEFAULT_WINDOW,
        max_streams=DEFAULT_MAX_STREAMS,
        compress=True,
    ):
        prepare_environment()
        self.rns_dir = rns_dir or os.environ.get("PORTFWD_RNS_DIR") or os.path.expanduser("~/.reticulum")
        self.config_dir = config_dir or os.environ.get("PORTFWD_CONFIG_DIR")
        self.shared_port = int(shared_port)
        self.control_port = int(control_port)
        self.require_shared = bool(require_shared)
        self.path_timeout = path_timeout
        self.connect_timeout = connect_timeout
        self.window = window
        self.max_streams = max_streams
        self.compress = compress
        self.logger = get_logger()

        self.events = EventBus(maxlen=500)
        self._logbridge = logbridge.install(self.events)
        self._lock = threading.RLock()
        self._session = None
        self._rns = None
        self._shared_instance = False
        self._transport_state = "starting"
        self._transport_error = None
        self._last_error = None
        self._aux_busy = False
        self._aux_kind = None
        self._last_aux = None
        self._started = False

    @property
    def started(self):
        return self._started

    def start(self):
        with self._lock:
            if self._started:
                return self
            self._started = True
        self._transport_state = "checking"
        self._transport_error = None
        if self.require_shared:
            try:
                preflight(self.shared_port)
            except SharedInstanceUnavailable as error:
                self._transport_state = "unavailable"
                self._transport_error = str(error)
                self._last_error = str(error)
            else:
                self._transport_state = "available"
        else:
            self._transport_state = "standalone"
        self.events.publish(
            "transport",
            shared_instance=self._shared_instance,
            state=self._transport_state,
            error=self._transport_error,
        )
        return self

    def _set_transport(self, state, error=None):
        with self._lock:
            changed = (self._transport_state, self._transport_error) != (state, error)
            self._transport_state = state
            self._transport_error = error
            if error:
                self._last_error = error
        if changed:
            self.events.publish(
                "transport",
                shared_instance=self._shared_instance,
                state=state,
                error=error,
            )

    def _transport_alive(self):
        instance = self._rns
        if instance is None:
            return False
        if self.require_shared and not getattr(
            instance, "is_connected_to_shared_instance", False
        ):
            return False
        if not self.require_shared:
            return True
        try:
            import RNS

            interfaces = list(
                getattr(RNS.Transport, "local_client_interfaces", None) or []
            )
            interfaces += list(getattr(RNS.Transport, "interfaces", None) or [])
            local = [
                interface
                for interface in interfaces
                if "Local shared instance" in str(getattr(interface, "name", ""))
            ]
            if local:
                return any(getattr(interface, "online", False) for interface in local)
        except Exception:
            pass
        return True

    def _refresh_transport(self):
        if self._rns is None or not self.require_shared:
            return
        alive = self._transport_alive()
        with self._lock:
            if alive == self._shared_instance:
                return
            self._shared_instance = alive
        if alive:
            self._set_transport("connected")
        else:
            self._set_transport(
                "unavailable",
                "the Reticulum shared instance is not available",
            )

    def _ensure_transport(self):
        with self._lock:
            if self._rns is not None and self._transport_alive():
                self._shared_instance = True
                return self._rns
        try:
            instance = start_reticulum(
                self.rns_dir,
                self.shared_port,
                self.control_port,
                require_shared=self.require_shared,
                check_preflight=True,
            )
        except SharedInstanceUnavailable as error:
            self._shared_instance = False
            self._set_transport("unavailable", str(error))
            raise EngineError("shared_instance", str(error)) from error
        except Exception as error:
            self._shared_instance = False
            message = f"{type(error).__name__}: {error}"
            self._set_transport("unavailable", message)
            raise EngineError("shared_instance", message) from error

        self._rns = instance
        self._shared_instance = bool(
            getattr(instance, "is_connected_to_shared_instance", False)
        )
        if self.require_shared and not self._transport_alive():
            message = "Reticulum is not connected to the shared instance"
            self._set_transport("unavailable", message)
            raise EngineError("shared_instance", message)
        self._set_transport("connected")
        return instance

    def _register_secrets(self, forwards, list_password=None):
        for forward in forwards:
            register_secret(forward.get("password"))
        register_secret(list_password)

    def connect(self, server_hash, forwards, list_password=None):
        try:
            resolved_hash, resolved_forwards, resolved_list = validate_config(
                server_hash, forwards, list_password
            )
        except ValidationError as error:
            raise EngineError(error.code, str(error), getattr(error, "field", None)) from None

        with self._lock:
            if self._session is not None and self._session.active:
                raise EngineError("busy", "a session is already active")

        self._ensure_transport()
        self._register_secrets(resolved_forwards, resolved_list)
        session = Session(
            engine=self,
            server_hash=resolved_hash,
            forwards=resolved_forwards,
            rns_dir=self.rns_dir,
            shared_port=self.shared_port,
            control_port=self.control_port,
            require_shared=self.require_shared,
            path_timeout=self.path_timeout,
            connect_timeout=self.connect_timeout,
            window=self.window,
            max_streams=self.max_streams,
            compress=self.compress,
            logger=self.logger,
        )
        with self._lock:
            self._session = session
            self._last_error = None
        session.start()
        return session

    def stop(self):
        with self._lock:
            session = self._session
            self._session = None
        if session is not None:
            session.stop()
        else:
            self.events.publish("session_state", state="idle", retry_attempt=0, retry_in=None)
        return True

    def _session_busy(self):
        with self._lock:
            return self._session is not None and self._session.active

    def list_services(self, server_hash, list_password=None):
        try:
            resolved_hash = validate_hash(server_hash)
        except ValidationError as error:
            raise EngineError(error.code, str(error), error.field) from None
        if self._session_busy():
            raise EngineError("busy", "a session is already active")
        self._ensure_transport()
        register_secret(list_password)
        self._start_aux("list", lambda: self._list_worker(resolved_hash, list_password or ""))

    def probe(self, server_hash, service, password):
        try:
            resolved_hash = validate_hash(server_hash)
            resolved_service = validate_name(service)
            resolved_password = validate_password(password)
        except ValidationError as error:
            raise EngineError(error.code, str(error), error.field) from None
        if self._session_busy():
            raise EngineError("busy", "a session is already active")
        self._ensure_transport()
        register_secret(resolved_password)
        self._start_aux(
            "probe",
            lambda: self._probe_worker(resolved_hash, resolved_service, resolved_password),
        )

    def _start_aux(self, kind, operation):
        with self._lock:
            if self._aux_busy:
                raise EngineError("busy", "another discovery operation is running")
            self._aux_busy = True
            self._aux_kind = kind
        thread = threading.Thread(target=self._run_aux, args=(operation,), name=f"portfwd-{kind}", daemon=True)
        thread.start()

    def _run_aux(self, operation):
        try:
            operation()
        except Exception as error:
            self.events.publish(
                "error",
                code="internal",
                message=f"{type(error).__name__}: {error}",
            )
        finally:
            with self._lock:
                self._aux_busy = False
                self._aux_kind = None

    def _list_worker(self, server_hash, password):
        link = None
        try:
            try:
                identity = rns_ops.resolve_identity(
                    bytes.fromhex(server_hash), self.path_timeout
                )
            except ResolutionError:
                self._publish_aux("list", {"ok": False, "error_code": "no_path", "services": []})
                return
            try:
                link = rns_ops.open_link(identity, self.connect_timeout)
                response = rns_ops.link_request(
                    link, "/list", {"password": password}, self.connect_timeout
                )
            except Exception:
                self._publish_aux("list", {"ok": False, "error_code": "link_failed", "services": []})
                return
            if not isinstance(response, dict) or not response.get("ok"):
                code = "list_disabled" if response and response.get("error") == "disabled" else "auth"
                self._publish_aux("list", {"ok": False, "error_code": code, "services": []})
                return
            services = response.get("services") or []
            self._publish_aux("list", {"ok": True, "error_code": None, "services": services})
        finally:
            rns_ops.close_link(link)

    def _probe_worker(self, server_hash, service, password):
        link = None
        try:
            try:
                identity = rns_ops.resolve_identity(
                    bytes.fromhex(server_hash), self.path_timeout
                )
            except ResolutionError:
                self._publish_aux("probe", {"ok": False, "error_code": "no_path", "service": service})
                return
            try:
                link = rns_ops.open_link(identity, self.connect_timeout)
                response = rns_ops.link_request(
                    link,
                    "/probe",
                    {"service": service, "password": password},
                    self.connect_timeout,
                )
            except Exception:
                self._publish_aux("probe", {"ok": False, "error_code": "link_failed", "service": service})
                return
            if not isinstance(response, dict) or not response.get("ok"):
                self._publish_aux("probe", {"ok": False, "error_code": "auth", "service": service})
                return
            public = response.get("service") or {}
            self._publish_aux("probe", {"ok": True, "error_code": None, "service": public})
        finally:
            rns_ops.close_link(link)

    def _publish_aux(self, kind, value):
        self._store_aux(value)
        if kind == "list":
            self.events.publish(
                "list_result",
                ok=value.get("ok"),
                error_code=value.get("error_code"),
                services=value.get("services") or [],
            )
        else:
            self.events.publish(
                "probe_result",
                ok=value.get("ok"),
                error_code=value.get("error_code"),
                service=value.get("service"),
            )

    def _store_aux(self, value):
        with self._lock:
            self._last_aux = value

    def _publish_session(self, session, event_type, **fields):
        self.events.publish(event_type, **fields)

    def snapshot(self):
        self._refresh_transport()
        with self._lock:
            session = self._session
        if session is not None:
            session_snapshot = session.snapshot()
        else:
            session_snapshot = {
                "state": "idle",
                "wanted": False,
                "retry_attempt": 0,
                "retry_in": None,
                "error_code": None,
                "error": None,
                "link": False,
                "server_hash": None,
                "forwards": [],
                "counters": {
                    "streams": 0,
                    "connections": 0,
                    "bytes_in": 0,
                    "bytes_out": 0,
                },
            }
        with self._lock:
            return {
                "transport": {
                    "shared_instance": self._shared_instance,
                    "state": self._transport_state,
                    "error": self._transport_error,
                    "port": self.shared_port,
                },
                "session": session_snapshot,
                "forwards": session_snapshot.get("forwards") or [],
                "counters": session_snapshot.get("counters")
                or {"streams": 0, "connections": 0, "bytes_in": 0, "bytes_out": 0},
                "last_error": self._last_error or session_snapshot.get("error"),
                "aux": {
                    "busy": self._aux_busy,
                    "kind": self._aux_kind,
                    "last": self._last_aux,
                },
            }

    def drain_events(self):
        return self.events.drain()

    def shutdown(self):
        self.stop()
        try:
            self._logbridge.uninstall()
        except Exception:
            pass
        return True
