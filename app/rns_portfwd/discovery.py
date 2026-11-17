"""Reticulum attachment, identity persistence and hash resolution."""

from __future__ import annotations

import importlib.util
import math
import os
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path

import RNS

from .config import validate_link_stamp_cost
from .constants import (
    APP_NAME,
    DEFAULT_HALF_OPEN_TIMEOUT,
    DEFAULT_MAX_HALF_OPEN,
    DEFAULT_PATH_TIMEOUT,
    LINK_STAMP_CACHE_MAX,
    LINK_STAMP_CACHE_TTL,
    MAX_LINK_STAMP_COST,
    PROTOCOL_VERSION,
)
from .errors import ConfigError, IdentityError, ResolutionError, SharedInstanceError
from .logging import rns_loglevel


def init_reticulum(config_dir: str | None, verbosity: int = 0):
    try:
        instance = RNS.Reticulum(
            configdir=config_dir,
            loglevel=rns_loglevel(verbosity),
            require_shared_instance=True,
        )
    except SystemError:
        instance = RNS.Reticulum.get_instance()
        port = getattr(instance, "local_interface_port", 37428) if instance else 37428
        raise SharedInstanceError(
            "no Reticulum shared instance is available; start rnsd for configuration "
            f"{RNS.Reticulum.configdir} (shared instance port {port})"
        ) from None
    except OSError as exc:
        raise SharedInstanceError(
            f"could not initialise Reticulum from {RNS.Reticulum.configdir}: {exc}; "
            "start rnsd or check the configuration"
        ) from None

    if not getattr(instance, "is_connected_to_shared_instance", False):
        raise SharedInstanceError(
            "this process is not connected to a Reticulum shared instance; start rnsd for "
            f"configuration {RNS.Reticulum.configdir} (shared instance port "
            f"{getattr(instance, 'local_interface_port', 37428)})"
        )
    return instance


def load_or_create_identity(path: str) -> RNS.Identity:
    target = Path(path).expanduser()
    if target.exists():
        try:
            identity = RNS.Identity.from_file(str(target))
        except Exception as exc:
            raise IdentityError(f"could not load identity {target}: {exc}") from None
        if identity is None:
            raise IdentityError(f"identity file {target} is not valid; refusing to replace it")
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return identity

    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, stat.S_IRWXU)
        identity = RNS.Identity()
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        identity.to_file(str(target))
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        raise IdentityError(f"could not create identity {target}: {exc}") from None
    return identity


def parse_destination_hash(value: str) -> bytes:
    text = value.strip().lower()
    if len(text) != 32:
        raise ResolutionError(
            f"invalid server hash {value!r}: expected 32 hexadecimal characters"
        )
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise ResolutionError(
            f"invalid server hash {value!r}: expected 32 hexadecimal characters"
        ) from None


def resolve_identity(
    destination_hash: bytes,
    timeout: float = DEFAULT_PATH_TIMEOUT,
) -> RNS.Identity:
    if not RNS.Transport.has_path(destination_hash):
        RNS.Transport.request_path(destination_hash)
    deadline = time.monotonic() + timeout
    requested_again = False
    while time.monotonic() < deadline:
        if RNS.Transport.has_path(destination_hash):
            identity = RNS.Identity.recall(destination_hash)
            if identity is not None:
                return identity
        if not requested_again and time.monotonic() > deadline - timeout + max(timeout / 3, 1.0):
            RNS.Transport.request_path(destination_hash)
            requested_again = True
        time.sleep(0.1)
    raise ResolutionError(
        f"could not resolve server {destination_hash.hex()} within {timeout:g}s; "
        "check the hash and that the server is reachable"
    )


def _reticulum_module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def link_stamp_validation_supported() -> bool:
    """Whether this Reticulum can validate native link-request stamps."""
    return _reticulum_module_available("RNS.Stamps") and hasattr(
        RNS.Destination, "set_link_cost"
    )


def link_stamp_generation_supported() -> bool:
    """Whether this Reticulum can generate native link-request stamps."""
    return (
        _reticulum_module_available("RNS.Stamps")
        and hasattr(RNS.Transport, "ALWAYS_STAMP_LRS")
        and hasattr(RNS.Transport, "LR_COST")
    )


def _link_debug(message: str) -> None:
    try:
        if RNS.sl(RNS.LOG_DEBUG):
            RNS.log(message, RNS.LOG_DEBUG)
    except Exception:
        pass


class LinkRequestGuard:
    """Pre-Link policy: half-open cap, native stamp validation and replay cache."""

    def __init__(
        self,
        destination_hash: bytes,
        stamp_cost: float = 0.0,
        max_half_open: int = DEFAULT_MAX_HALF_OPEN,
        cache_ttl: float = LINK_STAMP_CACHE_TTL,
        cache_max: int = LINK_STAMP_CACHE_MAX,
    ):
        self.destination_hash = destination_hash
        self.stamp_cost = float(stamp_cost)
        self.max_half_open = int(max_half_open)
        self.cache_ttl = float(cache_ttl)
        self.cache_max = int(cache_max)
        self._cache: OrderedDict[tuple[bytes, bytes], float] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, data, links):
        """Return ``(allowed, reserved_pair, reason)`` for one link request."""
        key_size = getattr(RNS.Link, "ECPUBSIZE", 64)
        mtu_size = getattr(RNS.Link, "LINK_MTU_SIZE", 3)
        if not isinstance(data, (bytes, bytearray)) or len(data) not in (
            key_size,
            key_size + mtu_size,
        ):
            return False, None, "malformed link request"

        if self.max_half_open > 0:
            statuses = (RNS.Link.PENDING, RNS.Link.HANDSHAKE)
            try:
                snapshot = list(links)
            except Exception:
                snapshot = []
            half_open = sum(
                1 for link in snapshot if getattr(link, "status", None) in statuses
            )
            if half_open >= self.max_half_open:
                return False, None, "half-open link limit"

        if self.stamp_cost <= 0:
            return True, None, ""

        half = key_size // 2
        pub = bytes(data[:half])
        sig_pub = bytes(data[half:key_size])
        try:
            from RNS import Stamps
        except Exception:
            return False, None, "link stamps unsupported"

        material = self.destination_hash + sig_pub
        try:
            valid = Stamps.stamp_valid(pub, self.stamp_cost, material)
        except Exception:
            return False, None, "stamp validation failed"
        if not valid:
            return False, None, "insufficient link stamp"

        pair = (pub, sig_pub)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if pair in self._cache:
                return False, None, "replayed link stamp"
            self._cache[pair] = now
            while len(self._cache) > self.cache_max:
                self._cache.popitem(last=False)
        return True, pair, ""

    def release(self, pair: tuple[bytes, bytes]) -> None:
        """Forget a reservation whose Link was not accepted by Reticulum."""
        with self._lock:
            self._cache.pop(pair, None)

    def _prune(self, now: float) -> None:
        while self._cache:
            _, stamped_at = next(iter(self._cache.items()))
            if now - stamped_at <= self.cache_ttl:
                break
            self._cache.popitem(last=False)


class StampedDestination(RNS.Destination):
    """Destination that applies the pre-Link guard and clamps half-open timeouts."""

    def __init__(
        self,
        *args,
        stamp_cost: float = 0.0,
        max_half_open: int = DEFAULT_MAX_HALF_OPEN,
        half_open_timeout: float = DEFAULT_HALF_OPEN_TIMEOUT,
        cache_ttl: float = LINK_STAMP_CACHE_TTL,
        cache_max: int = LINK_STAMP_CACHE_MAX,
        **kwargs,
    ):
        self.half_open_timeout = float(half_open_timeout)
        self.link_guard: LinkRequestGuard | None = None
        super().__init__(*args, **kwargs)
        self.link_guard = LinkRequestGuard(
            self.hash,
            stamp_cost=stamp_cost,
            max_half_open=max_half_open,
            cache_ttl=cache_ttl,
            cache_max=cache_max,
        )

    def incoming_link_request(self, data, packet) -> None:
        if not getattr(self, "accept_link_requests", False):
            return
        guard = self.link_guard
        if guard is None:
            _link_debug("Dropping link request: destination is not ready")
            return
        links = getattr(self, "links", [])
        existing = {id(link) for link in links}
        allowed, pair, reason = guard.check(data, links)
        if not allowed:
            _link_debug(f"Dropping link request: {reason}")
            return
        try:
            super().incoming_link_request(data, packet)
        except Exception:
            if pair is not None:
                guard.release(pair)
            raise
        after = getattr(self, "links", [])
        new_links = [link for link in after if id(link) not in existing]
        if not new_links:
            if pair is not None:
                guard.release(pair)
            return
        for link in new_links:
            try:
                link.establishment_timeout = min(
                    link.establishment_timeout, self.half_open_timeout
                )
            except Exception:
                pass


def make_in_destination(
    identity: RNS.Identity,
    stamp_cost: float = 0.0,
    max_half_open: int = DEFAULT_MAX_HALF_OPEN,
    half_open_timeout: float = DEFAULT_HALF_OPEN_TIMEOUT,
    cache_ttl: float = LINK_STAMP_CACHE_TTL,
    cache_max: int = LINK_STAMP_CACHE_MAX,
) -> RNS.Destination:
    if stamp_cost > 0 and not link_stamp_validation_supported():
        raise ConfigError(
            "link stamping requires native Reticulum support; install rns>=1.6"
        )
    destination = StampedDestination(
        identity,
        RNS.Destination.IN,
        RNS.Destination.SINGLE,
        APP_NAME,
        stamp_cost=stamp_cost,
        max_half_open=max_half_open,
        half_open_timeout=half_open_timeout,
        cache_ttl=cache_ttl,
        cache_max=cache_max,
    )
    # Explicitly pin the native cost, including 0, so a Reticulum-wide
    # enforce_stamps setting cannot fall back to Transport.LR_COST.
    set_link_cost = getattr(destination, "set_link_cost", None)
    if callable(set_link_cost):
        set_link_cost(max(0.0, float(stamp_cost)))
    return destination


def make_out_destination(identity: RNS.Identity) -> RNS.Destination:
    return RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE, APP_NAME)


def announce_data(name: str, auth: bool, stamp_cost: float | None = None) -> bytes:
    from RNS.vendor import umsgpack

    payload = {"v": PROTOCOL_VERSION, "name": name, "auth": bool(auth)}
    if stamp_cost is not None and stamp_cost > 0:
        payload["stamp_cost"] = float(stamp_cost)
    return umsgpack.packb(payload)


def set_default_app_data(
    destination, name: str, auth: bool, stamp_cost: float | None = None
) -> None:
    destination.set_default_app_data(announce_data(name, auth, stamp_cost))


def parse_announce_data(raw: bytes | None) -> dict | None:
    if not raw:
        return None
    try:
        from RNS.vendor import umsgpack

        data = umsgpack.unpackb(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    return data


def parse_stamp_cost(app_data) -> float:
    """Return a valid advertised link stamp cost, or 0 for missing/invalid data."""
    if not isinstance(app_data, dict):
        return 0.0
    value = app_data.get("stamp_cost")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    cost = float(value)
    if not math.isfinite(cost) or not 0.0 <= cost <= MAX_LINK_STAMP_COST:
        return 0.0
    return cost


def configure_link_stamping(
    server_hash: bytes,
    override: float | None = None,
    disabled: bool = False,
    logger=None,
) -> float:
    """Configure native link-request stamping before the first Link is created."""
    transport = RNS.Transport
    if disabled:
        if hasattr(transport, "ALWAYS_STAMP_LRS"):
            transport.ALWAYS_STAMP_LRS = False
        if hasattr(transport, "STAMP_LRS"):
            transport.STAMP_LRS = False
        return 0.0

    if override is not None:
        cost = validate_link_stamp_cost(override)
    else:
        app_data = None
        recall_app_data = getattr(RNS.Identity, "recall_app_data", None)
        if callable(recall_app_data):
            try:
                raw_app_data = recall_app_data(server_hash)
            except Exception:
                raw_app_data = None
            app_data = parse_announce_data(raw_app_data)
        cost = parse_stamp_cost(app_data)

    if cost <= 0:
        if hasattr(transport, "ALWAYS_STAMP_LRS"):
            transport.ALWAYS_STAMP_LRS = False
        if override is not None and hasattr(transport, "STAMP_LRS"):
            transport.STAMP_LRS = False
        return 0.0

    if not link_stamp_generation_supported():
        raise ResolutionError(
            "the server requires link-request stamps, but this Reticulum cannot "
            "generate them; install rns>=1.6 or use a server without "
            "--link-stamp-cost"
        )

    transport.ALWAYS_STAMP_LRS = True
    transport.LR_COST = cost
    if hasattr(transport, "STAMP_LRS"):
        transport.STAMP_LRS = True
    if logger is not None:
        logger.debug("link request stamping enabled at cost %s", cost)
    return cost
