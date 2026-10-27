"""Client Link lifecycle helper."""

from __future__ import annotations

import threading

import RNS

from .constants import DEFAULT_CONNECT_TIMEOUT
from .discovery import make_out_destination
from .errors import ResolutionError


class LinkManager:
    def __init__(
        self,
        identity: RNS.Identity,
        timeout: float = DEFAULT_CONNECT_TIMEOUT,
        link_factory=None,
        on_closed=None,
    ):
        self.destination = make_out_destination(identity)
        self.timeout = timeout
        self._link_factory = link_factory or RNS.Link
        self._on_closed_callback = on_closed
        self._established = threading.Event()
        self._closed = threading.Event()
        self.link = None

    def open(self):
        self._established.clear()
        self._closed.clear()
        try:
            link = self._link_factory(
                self.destination,
                established_callback=self._on_established,
                closed_callback=self._on_closed,
            )
        except Exception as exc:
            raise ResolutionError(f"could not create a Link: {exc}") from None

        if not self._established.wait(self.timeout):
            try:
                link.teardown()
            except Exception:
                pass
            raise ResolutionError(
                f"could not establish a Link with {getattr(self.destination, 'hexhash', '<unknown>')} "
                f"within {self.timeout:g}s"
            )
        self.link = link
        return link

    def _on_established(self, link) -> None:
        self.link = link
        self._established.set()

    def _on_closed(self, link) -> None:
        self._closed.set()
        if self._on_closed_callback is not None:
            try:
                self._on_closed_callback(link)
            except Exception:
                pass

    @property
    def established(self) -> bool:
        return self._established.is_set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def close(self) -> None:
        link = self.link
        if link is not None and getattr(link, "status", None) != RNS.Link.CLOSED:
            try:
                link.teardown()
            except Exception:
                pass
        self._closed.set()
