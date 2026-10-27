"""Reticulum attachment, identity persistence and hash resolution."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import RNS

from .constants import APP_NAME, DEFAULT_PATH_TIMEOUT, PROTOCOL_VERSION
from .errors import IdentityError, ResolutionError, SharedInstanceError
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
        return identity

    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, stat.S_IRWXU)
        identity = RNS.Identity()
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


def make_in_destination(identity: RNS.Identity) -> RNS.Destination:
    return RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE, APP_NAME)


def make_out_destination(identity: RNS.Identity) -> RNS.Destination:
    return RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE, APP_NAME)


def announce_data(name: str, auth: bool) -> bytes:
    from RNS.vendor import umsgpack

    return umsgpack.packb(
        {"v": PROTOCOL_VERSION, "name": name, "auth": bool(auth)}
    )


def set_default_app_data(destination, name: str, auth: bool) -> None:
    destination.set_default_app_data(announce_data(name, auth))


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
