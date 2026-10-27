import threading
import time

import RNS
from rns_portfwd.discovery import make_out_destination
from rns_portfwd.errors import ResolutionError


class Cancelled(RuntimeError):
    pass


def _check(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise Cancelled("operation cancelled")


def resolve_identity(destination_hash, timeout, stop_event=None):
    if not RNS.Transport.has_path(destination_hash):
        RNS.Transport.request_path(destination_hash)
    deadline = time.monotonic() + timeout
    requested_again = False
    while time.monotonic() < deadline:
        _check(stop_event)
        if RNS.Transport.has_path(destination_hash):
            identity = RNS.Identity.recall(destination_hash)
            if identity is not None:
                return identity
        if not requested_again and time.monotonic() > deadline - timeout + max(timeout / 3, 1.0):
            RNS.Transport.request_path(destination_hash)
            requested_again = True
        if stop_event is not None:
            stop_event.wait(0.1)
        else:
            time.sleep(0.1)
    raise ResolutionError(
        f"could not resolve server {destination_hash.hex()} within {timeout:g}s"
    )


def open_link(identity, timeout, stop_event=None, on_closed=None):
    destination = make_out_destination(identity)
    established = threading.Event()
    closed = threading.Event()
    holder = {}

    def _established(link):
        holder["link"] = link
        established.set()

    def _closed(link):
        closed.set()
        if on_closed is not None:
            try:
                on_closed(link)
            except Exception:
                pass

    try:
        link = RNS.Link(
            destination,
            established_callback=_established,
            closed_callback=_closed,
        )
    except Exception as error:
        raise ResolutionError(f"could not create a Link: {error}") from None

    deadline = time.monotonic() + timeout
    try:
        while not established.is_set():
            _check(stop_event)
            if closed.is_set():
                raise ResolutionError("the Link closed before it was established")
            if time.monotonic() >= deadline:
                raise ResolutionError(f"could not establish a Link within {timeout:g}s")
            established.wait(0.1)
    except Cancelled:
        try:
            link.teardown()
        except Exception:
            pass
        raise
    except ResolutionError:
        try:
            link.teardown()
        except Exception:
            pass
        raise
    holder["link"] = link
    return link


def link_request(link, path, data, timeout, stop_event=None):
    event = threading.Event()
    result = {}

    def on_response(receipt):
        result["response"] = receipt.response
        event.set()

    def on_failed(_receipt):
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
    except Exception as error:
        raise ResolutionError(f"request {path} failed ({type(error).__name__})") from None
    if receipt is False:
        raise ResolutionError(f"could not send request {path}")

    deadline = time.monotonic() + timeout + 5
    while not event.is_set():
        _check(stop_event)
        if time.monotonic() >= deadline:
            raise ResolutionError(f"request {path} timed out")
        event.wait(0.1)
    if result.get("failed"):
        raise ResolutionError(f"request {path} failed")
    return result.get("response")


def close_link(link):
    if link is None:
        return
    try:
        if getattr(link, "status", None) != RNS.Link.CLOSED:
            link.teardown()
    except Exception:
        pass
