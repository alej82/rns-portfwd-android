import socket

from .paths import is_android
from .rns_config import (
    INSTANCE_CONTROL_PORT,
    SHARED_INSTANCE_PORT,
    ensure_config,
)


class SharedInstanceUnavailable(RuntimeError):
    """The local Reticulum shared instance is missing or unusable."""


def preflight(port=SHARED_INSTANCE_PORT, host="127.0.0.1", timeout=1.5):
    try:
        sock = socket.create_connection((host, int(port)), timeout=timeout)
    except OSError as error:
        raise SharedInstanceUnavailable(
            f"no Reticulum shared instance is listening on {host}:{port} ({error})"
        ) from error
    try:
        sock.close()
    except OSError:
        pass
    return True


def start_reticulum(
    rns_dir,
    shared_port=SHARED_INSTANCE_PORT,
    control_port=INSTANCE_CONTROL_PORT,
    require_shared=None,
    check_preflight=True,
):
    import RNS

    if require_shared is None:
        require_shared = is_android()
    if require_shared:
        ensure_config(
            rns_dir,
            shared_port,
            control_port,
            force=bool(require_shared and is_android()),
        )
        if check_preflight:
            preflight(shared_port)

    existing = RNS.Reticulum.get_instance()
    if existing is not None:
        instance = existing
    else:
        try:
            instance = RNS.Reticulum(
                configdir=rns_dir,
                require_shared_instance=require_shared,
            )
        except SystemError as error:
            raise SharedInstanceUnavailable(
                "Reticulum could not attach to the shared instance; "
                "start the provider and restart the app if the port was already open"
            ) from error
        except OSError as error:
            raise SharedInstanceUnavailable(
                f"could not initialise Reticulum from {rns_dir}: {error}"
            ) from error

    if require_shared and not getattr(instance, "is_connected_to_shared_instance", False):
        raise SharedInstanceUnavailable(
            "Reticulum is not connected to the shared instance; "
            "start Columba, MeshChat or another provider"
        )
    return instance


def close_reticulum():
    """Deliberately a no-op.

    Reticulum is a process singleton and its global exit handler is one-shot.
    Sessions must never tear it down; RNS closes itself through atexit.
    """
    return None
