import threading

import pytest
from portfwd_android import rns_ops
from rns_portfwd.errors import ResolutionError


class FakeLink:
    """A Link that establishes synchronously and records every interaction."""

    instances = []

    def __init__(self, destination, established_callback=None, closed_callback=None):
        self.destination = destination
        self.closed_callback = closed_callback
        self.identified = []
        self.identified_while_constructing = []
        self.torn_down = False
        self.constructed = False
        FakeLink.instances.append(self)
        if established_callback is not None:
            # A real Link calls this from the network thread; doing it inline is
            # the strictest possible ordering: anything the callback does must
            # happen before the wait loop below can observe the Event.
            established_callback(self)
        self.constructed = True

    def identify(self, *args, **kwargs):
        self.identified.append((args, kwargs))
        self.identified_while_constructing.append(not self.constructed)
        if self.identify_error is not None:
            raise self.identify_error

    identify_error = None

    def teardown(self):
        self.torn_down = True


class PendingLink:
    """A Link that never establishes; records teardown and instance creation."""

    instances = []

    def __init__(self, destination, established_callback=None, closed_callback=None):
        self.destination = destination
        self.closed_callback = closed_callback
        self.torn_down = False
        type(self).instances.append(self)

    def teardown(self):
        self.torn_down = True


@pytest.fixture
def fake_rns(monkeypatch):
    FakeLink.instances = []
    FakeLink.identify_error = None
    namespace = type("FakeRNS", (), {"Link": FakeLink})()
    monkeypatch.setattr(rns_ops, "RNS", namespace)
    return FakeLink


@pytest.fixture
def stamp_calls(monkeypatch):
    calls = []

    def configure(server_hash, override=None, disabled=False, logger=None):
        calls.append(
            {
                "server_hash": server_hash,
                "override": override,
                "disabled": disabled,
                "logger": logger,
            }
        )
        return 0.0

    monkeypatch.setattr(rns_ops, "configure_link_stamping", configure)
    return calls


def test_configure_stamping_off_disables_the_vendor_helper(stamp_calls):
    rns_ops.configure_stamping(b"h" * 32, "off", None)

    assert stamp_calls == [
        {"server_hash": b"h" * 32, "override": None, "disabled": True, "logger": None}
    ]


def test_configure_stamping_cost_passes_the_override(stamp_calls):
    logger = object()

    result = rns_ops.configure_stamping(b"h" * 32, "cost", "12.5", logger)

    assert result == 0.0
    assert stamp_calls == [
        {
            "server_hash": b"h" * 32,
            "override": 12.5,
            "disabled": False,
            "logger": logger,
        }
    ]


def test_configure_stamping_auto_passes_no_override(stamp_calls):
    rns_ops.configure_stamping(b"h" * 32, "auto", 9)

    assert stamp_calls[0]["override"] is None
    assert stamp_calls[0]["disabled"] is False


def test_configure_stamping_is_case_insensitive_with_whitespace(stamp_calls):
    rns_ops.configure_stamping(b"h" * 32, " OFF ")

    assert stamp_calls[0]["disabled"] is True


def test_configure_stamping_defaults_to_auto(stamp_calls):
    rns_ops.configure_stamping(b"h" * 32)

    assert stamp_calls[0]["override"] is None
    assert stamp_calls[0]["disabled"] is False


def test_configure_stamping_cost_mode_requires_a_cost(stamp_calls):
    with pytest.raises(ValueError):
        rns_ops.configure_stamping(b"h" * 32, "cost", None)

    assert stamp_calls == []


def test_configure_stamping_rejects_an_unknown_mode(stamp_calls):
    with pytest.raises(ValueError):
        rns_ops.configure_stamping(b"h" * 32, "sometimes")

    assert stamp_calls == []


def test_configure_stamping_maps_a_vendor_resolution_error(monkeypatch):
    def unsupported(*_args, **_kwargs):
        raise ResolutionError("no stamp support")

    monkeypatch.setattr(rns_ops, "configure_link_stamping", unsupported)

    with pytest.raises(rns_ops.StampUnsupported) as caught:
        rns_ops.configure_stamping(b"h" * 32, "auto")

    assert "no stamp support" in str(caught.value)
    assert not isinstance(caught.value, ResolutionError)


def test_configure_stamping_returns_the_cost_the_vendor_resolved(monkeypatch):
    monkeypatch.setattr(rns_ops, "configure_link_stamping", lambda *a, **k: 12.5)

    assert rns_ops.configure_stamping(b"h" * 32, "auto") == 12.5


def test_open_link_does_not_create_a_link_when_stamping_is_unsupported(
    monkeypatch, fake_rns
):
    def unsupported(*_args, **_kwargs):
        raise rns_ops.StampUnsupported("cannot stamp")

    monkeypatch.setattr(rns_ops, "configure_link_stamping", unsupported)
    _patch_destination(monkeypatch, [])

    with pytest.raises(rns_ops.StampUnsupported):
        rns_ops.open_link(object(), 1.0, server_hash=b"h" * 32)

    assert FakeLink.instances == []



def _patch_destination(monkeypatch, order):
    destination = object()

    def make(identity):
        order.append("destination")
        return destination

    monkeypatch.setattr(rns_ops, "make_out_destination", make)
    return destination


def test_open_link_configures_stamping_before_creating_the_link(monkeypatch, fake_rns):
    order = []

    def configure(*_args, **_kwargs):
        order.append("stamp")
        return 6.0

    monkeypatch.setattr(rns_ops, "configure_link_stamping", configure)
    _patch_destination(monkeypatch, order)
    original_init = FakeLink.__init__

    def recording_init(self, destination, established_callback=None, closed_callback=None):
        order.append("link")
        original_init(self, destination, established_callback, closed_callback)

    monkeypatch.setattr(FakeLink, "__init__", recording_init)

    link = rns_ops.open_link(object(), 1.0, server_hash=b"h" * 32, stamp_mode="auto")

    assert order == ["stamp", "destination", "link"]
    assert link.destination is not None


def test_open_link_identifies_the_link_from_the_established_callback(monkeypatch, fake_rns):
    _patch_destination(monkeypatch, [])
    client_identity = object()

    link = rns_ops.open_link(object(), 1.0, client_identity=client_identity)

    assert link.identified == [((client_identity,), {})]
    # The proof is sent while the callback runs, i.e. before the Event is set
    # and before open_link can return to the caller.
    assert link.identified_while_constructing == [True]


def test_open_link_stays_anonymous_without_a_client_identity(monkeypatch, fake_rns):
    _patch_destination(monkeypatch, [])

    link = rns_ops.open_link(object(), 1.0)

    assert link.identified == []


def test_open_link_survives_a_failed_identification(monkeypatch, fake_rns):
    _patch_destination(monkeypatch, [])
    FakeLink.identify_error = RuntimeError("no proof")
    logger = type("Logger", (), {"messages": [], "debug": lambda self, *a: self.messages.append(a)})()

    link = rns_ops.open_link(object(), 1.0, client_identity=object(), logger=logger)

    assert link.identified  # it was attempted
    assert logger.messages  # and the failure was logged, not raised


def test_open_link_skips_stamping_when_no_server_hash_is_known(monkeypatch, fake_rns):
    calls = []
    monkeypatch.setattr(rns_ops, "configure_link_stamping", lambda *a, **k: calls.append(a))
    _patch_destination(monkeypatch, [])

    rns_ops.open_link(object(), 1.0, client_identity=None, stamp_mode="cost", stamp_cost=9)

    assert calls == []


def test_open_link_times_out_with_a_resolution_error(monkeypatch, fake_rns):
    _patch_destination(monkeypatch, [])
    PendingLink.instances = []
    monkeypatch.setattr(rns_ops.RNS, "Link", PendingLink)

    with pytest.raises(ResolutionError):
        rns_ops.open_link(object(), 0.05)

    assert PendingLink.instances[0].torn_down is True


def test_open_link_reports_a_link_closed_before_establishment(monkeypatch, fake_rns):
    _patch_destination(monkeypatch, [])
    PendingLink.instances = []
    closed_links = []

    class ClosedImmediately(PendingLink):
        def __init__(self, destination, established_callback=None, closed_callback=None):
            super().__init__(destination, established_callback, closed_callback)
            if closed_callback is not None:
                closed_callback(self)

    monkeypatch.setattr(rns_ops.RNS, "Link", ClosedImmediately)

    with pytest.raises(ResolutionError) as caught:
        rns_ops.open_link(object(), 0.5, on_closed=closed_links.append)

    assert "closed before it was established" in str(caught.value)
    assert closed_links  # on_closed still ran
    assert PendingLink.instances[0].torn_down is True


def test_open_link_is_cancelled_by_the_stop_event(monkeypatch, fake_rns):
    _patch_destination(monkeypatch, [])
    PendingLink.instances = []
    monkeypatch.setattr(rns_ops.RNS, "Link", PendingLink)
    stop_event = threading.Event()
    stop_event.set()

    with pytest.raises(rns_ops.Cancelled):
        rns_ops.open_link(object(), 1.0, stop_event)

    assert PendingLink.instances[0].torn_down is True


class FakeLinkRequests:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, link, path, data, timeout, stop_event=None):
        self.payloads.append((path, dict(data)))
        return self.responses.pop(0)


def _probe_link(identify_error=None):
    link = type("ProbeLink", (), {})()
    link.identifications = []

    def identify(identity):
        link.identifications.append(identity)
        if identify_error is not None:
            raise identify_error

    link.identify = identify
    return link


def test_probe_without_identity_sends_a_single_request(monkeypatch):
    requests = FakeLinkRequests([{"ok": True}])
    monkeypatch.setattr(rns_ops, "link_request", requests)
    link = _probe_link()

    response = rns_ops.probe_request(link, "ssh", "secret", 1.0)

    assert response == {"ok": True}
    assert requests.payloads == [("/probe", {"service": "ssh", "password": "secret"})]
    assert link.identifications == []


def test_probe_retries_an_identity_race_up_to_three_attempts(monkeypatch):
    monkeypatch.setattr(rns_ops, "_IDENTITY_RETRY_DELAY", 0)
    requests = FakeLinkRequests([{"ok": False, "error": "auth"} for _ in range(3)])
    monkeypatch.setattr(rns_ops, "link_request", requests)
    client_identity = object()
    link = _probe_link()

    response = rns_ops.probe_request(
        link, "ssh", "secret", 1.0, client_identity=client_identity
    )

    assert response == {"ok": False, "error": "auth"}
    assert len(requests.payloads) == 3
    assert link.identifications == [client_identity, client_identity]


def test_probe_stops_retrying_after_a_successful_reidentified_request(monkeypatch):
    monkeypatch.setattr(rns_ops, "_IDENTITY_RETRY_DELAY", 0)
    requests = FakeLinkRequests(
        [{"ok": False, "error": "auth"}, {"ok": True, "service": {"name": "ssh"}}]
    )
    monkeypatch.setattr(rns_ops, "link_request", requests)
    link = _probe_link()

    response = rns_ops.probe_request(link, "ssh", "secret", 1.0, client_identity=object())

    assert response == {"ok": True, "service": {"name": "ssh"}}
    assert len(requests.payloads) == 2
    assert len(link.identifications) == 1


def test_probe_does_not_retry_a_non_auth_failure(monkeypatch):
    requests = FakeLinkRequests([{"ok": False, "error": "disabled"}])
    monkeypatch.setattr(rns_ops, "link_request", requests)
    link = _probe_link()

    response = rns_ops.probe_request(link, "ssh", "secret", 1.0, client_identity=object())

    assert response == {"ok": False, "error": "disabled"}
    assert len(requests.payloads) == 1
    assert link.identifications == []


def test_probe_tolerates_a_failed_reidentification(monkeypatch):
    monkeypatch.setattr(rns_ops, "_IDENTITY_RETRY_DELAY", 0)
    requests = FakeLinkRequests(
        [{"ok": False, "error": "auth"}, {"ok": True, "service": {"name": "ssh"}}]
    )
    monkeypatch.setattr(rns_ops, "link_request", requests)
    link = _probe_link(identify_error=RuntimeError("no proof"))

    response = rns_ops.probe_request(link, "ssh", "secret", 1.0, client_identity=object())

    assert response == {"ok": True, "service": {"name": "ssh"}}


def test_probe_is_cancelled_while_waiting_to_retry(monkeypatch):
    monkeypatch.setattr(rns_ops, "_IDENTITY_RETRY_DELAY", 0)
    requests = FakeLinkRequests([{"ok": False, "error": "auth"}])
    monkeypatch.setattr(rns_ops, "link_request", requests)
    stop_event = threading.Event()
    stop_event.set()

    with pytest.raises(rns_ops.Cancelled):
        rns_ops.probe_request(
            _probe_link(), "ssh", "secret", 1.0, stop_event, client_identity=object()
        )

    assert len(requests.payloads) == 1
