"""The single device Reticulum identity, stored inside the app sandbox.

The identity file carries the private key and is created with ``0600``
permissions. Only its public hash is ever shown or logged; the key material
never leaves this module.
"""

import logging
import os
import threading

import RNS

from .paths import identity_path as default_identity_path

_LOGGER = logging.getLogger(__name__)
_LOCK = threading.RLock()
_CACHE = {}


class IdentityError(RuntimeError):
    """The device identity could not be loaded or created."""


def _resolve(path):
    return os.path.abspath(path or default_identity_path())


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def load_identity(path=None):
    """Return the stored identity, or ``None`` when it is missing or corrupt."""
    target = _resolve(path)
    if not os.path.isfile(target):
        return None
    try:
        identity = RNS.Identity.from_file(target)
    except Exception:
        return None
    return identity


def ensure_identity(path=None):
    """Return the device identity, creating the ``0600`` file on first use."""
    target = _resolve(path)
    with _LOCK:
        cached = _CACHE.get(target)
        if cached is not None:
            return cached
        if os.path.isfile(target):
            identity = load_identity(target)
            if identity is None:
                # Never replace a key we cannot read: the hash may already be
                # on a server allow-list, and a silent replacement would lose
                # the registration without telling anyone.
                raise IdentityError(
                    f"the device identity at {target} is unreadable; "
                    "restore it or clear the app data to create a new one"
                )
        else:
            identity = _create(target)
        _CACHE[target] = identity
        return identity


def identity_hash(path=None):
    """Return the public 32-hex identity hash, creating the identity if needed."""
    return ensure_identity(path).hash.hex()


def reset_cache():
    """Drop the cached identities; used by tests."""
    with _LOCK:
        _CACHE.clear()


def _create(target):
    directory = os.path.dirname(target) or "."
    temporary = f"{target}.{os.getpid()}.tmp"
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        identity = RNS.Identity()
        # Pre-create the file owner-only so the private key is never written
        # with the process umask; RNS truncates the existing file in place.
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        os.close(descriptor)
        os.chmod(temporary, 0o600)
        if not identity.to_file(temporary):
            raise IdentityError(f"could not write the device identity to {target}")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except IdentityError:
        _unlink(temporary)
        raise
    except Exception as error:
        _unlink(temporary)
        raise IdentityError(f"could not create the device identity: {error}") from None
    _LOGGER.debug("device identity ready at %s (%s)", target, identity.hash.hex())
    return identity
