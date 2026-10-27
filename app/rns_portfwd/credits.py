"""Per-stream byte credit accounting."""

from __future__ import annotations

import threading
import time

from .constants import MAX_CREDIT


class CreditWindow:
    """A blocking, bounded byte window.

    ``take`` waits until the requested amount is available. ``add`` is called
    by the peer's WINDOW messages or by the initial OPEN_OK credit.
    """

    def __init__(self, initial: int = 0, max_credit: int = MAX_CREDIT):
        if not 0 <= initial <= max_credit:
            raise ValueError("initial credit out of range")
        self._credit = initial
        self._max = max_credit
        self._closed = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    @property
    def available(self) -> int:
        with self._lock:
            return self._credit

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def add(self, amount: int) -> bool:
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("credit amount must be positive")
        with self._cond:
            if self._closed:
                return False
            self._credit = min(self._credit + amount, self._max)
            self._cond.notify_all()
            return True

    def take(self, amount: int, closed: threading.Event, timeout: float | None = None) -> bool:
        if amount <= 0:
            return True
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._credit < amount:
                if self._closed or closed.is_set():
                    return False
                if deadline is None:
                    self._cond.wait(0.2)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(min(remaining, 0.2))
            if self._closed or closed.is_set():
                return False
            self._credit -= amount
            return True


    def take_up_to(self, amount: int, closed: threading.Event, timeout: float | None = None) -> int:
        """Wait for at least one byte of credit and take up to ``amount``."""
        if amount <= 0:
            return 0
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._credit <= 0:
                if self._closed or closed.is_set():
                    return 0
                if deadline is None:
                    self._cond.wait(0.2)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return 0
                    self._cond.wait(min(remaining, 0.2))
            if self._closed or closed.is_set():
                return 0
            taken = min(amount, self._credit)
            self._credit -= taken
            return taken

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
