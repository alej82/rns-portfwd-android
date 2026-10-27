"""Constant-time password checks and per-link failure accounting."""

from __future__ import annotations

import hashlib
import hmac
import threading

from .constants import AUTH_FAILURE_DELAY, MAX_AUTH_FAILURES

_DUMMY_PASSWORD = "rns-portfwd-dummy-password"


def constant_time_equals(left: str | bytes | None, right: str | bytes | None) -> bool:
    def digest(value: str | bytes | None) -> bytes:
        if value is None:
            value = b"\x00rns-portfwd-none"
        if isinstance(value, str):
            value = value.encode("utf-8")
        return hashlib.sha256(value).digest()

    return hmac.compare_digest(digest(left), digest(right))


def dummy_compare(password: str | bytes | None) -> None:
    constant_time_equals(password, _DUMMY_PASSWORD)


class AuthTracker:
    def __init__(
        self,
        max_failures: int = MAX_AUTH_FAILURES,
        delay: float = AUTH_FAILURE_DELAY,
    ):
        self.max_failures = max_failures
        self.delay = delay
        self._failures = 0
        self._lock = threading.Lock()

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def record_failure(self) -> bool:
        """Record a failure and return True when the link must be closed."""
        with self._lock:
            self._failures += 1
            return self._failures >= self.max_failures

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
