import threading
import time

import RNS
from rns_portfwd.discovery import configure_link_stamping, make_out_destination
from rns_portfwd.errors import ResolutionError


class Cancelled(RuntimeError):
    pass


class StampUnsupported(RuntimeError):
    """The local Reticulum cannot generate the stamps the server requires."""


_IDENTITY_RETRY_DELAY = 1.0
_MAX_PROBE_ATTEMPTS = 3


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


def configure_stamping(server_hash, stamp_mode="auto", stamp_cost=None, logger=None):
    """Apply the profile's link-stamp policy before any Link is created.

    Returns the cost the next Link will ask for (0 when no stamp is used).
    ``stamp_mode`` must already be validated; an unknown mode is a programming
    error, not a silent fallback.
    """
    mode = str(stamp_mode or "auto").strip().lower()
    if mode not in ("auto", "off", "cost"):
        raise ValueError(f"invalid link stamp mode {stamp_mode!r}")
    try:
        if mode == "off":
            return configure_link_stamping(server_hash, None, True, logger)
        if mode == "cost":
            if stamp_cost is None:
                raise ValueError("a custom link stamp cost is required")
            return configure_link_stamping(server_hash, float(stamp_cost), False, logger)
        return configure_link_stamping(server_hash, None, False, logger)
    except ResolutionError as error:
        raise StampUnsupported(str(error)) from None


def open_link(
    identity,
    timeout,
    stop_event=None,
    on_closed=None,
    *,
    server_hash=None,
    client_identity=None,
    stamp_mode="auto",
    stamp_cost=None,
    on_stamp=None,
    logger=None,
):
    if server_hash is not None:
        cost = configure_stamping(server_hash, stamp_mode, stamp_cost, logger)
        # `configure_stamping` is the only place that knows the effective cost
        # (an `auto` profile follows whatever the server advertises), and stamp
        # generation happens inside the Link construction below, so report it
        # here, before the Link is created.
        if cost and on_stamp is not None:
            try:
                on_stamp(cost)
            except Exception as error:  # pragma: no cover - UI must not break the Link
                if logger is not None:
                    logger.debug("could not report the link stamp cost: %s", error)
    destination = make_out_destination(identity)
    established = threading.Event()
    closed = threading.Event()

    def _established(link):
        if client_identity is not None:
            try:
                link.identify(client_identity)
            except Exception:
                # An unidentifiable Link still works anonymously; the server
                # decides whether to reject it and the probe reports auth.
                if logger is not None:
                    logger.debug("could not identify the Link; continuing anonymously")
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


def probe_request(link, service, password, timeout, stop_event=None, *, client_identity=None):
    """Probe a service, tolerating an identification proof that raced the request."""
    payload = {"service": service, "password": password}
    response = link_request(link, "/probe", payload, timeout, stop_event)
    if client_identity is None:
        return response
    attempts = 1
    while attempts < _MAX_PROBE_ATTEMPTS:
        if not isinstance(response, dict) or response.get("error") != "auth":
            return response
        try:
            link.identify(client_identity)
        except Exception:
            pass
        if stop_event is not None:
            if stop_event.wait(_IDENTITY_RETRY_DELAY):
                raise Cancelled("operation cancelled")
            _check(stop_event)
        else:
            time.sleep(_IDENTITY_RETRY_DELAY)
        response = link_request(link, "/probe", payload, timeout, stop_event)
        attempts += 1
    return response


def close_link(link):
    if link is None:
        return
    try:
        if getattr(link, "status", None) != RNS.Link.CLOSED:
            link.teardown()
    except Exception:
        pass
