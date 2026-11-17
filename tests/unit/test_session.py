import threading

import pytest
from portfwd_android import session as session_module
from portfwd_android.listeners import BoundListener
from portfwd_android.session import Retryable, Session, Terminal, _jittered
from rns_portfwd.errors import AuthError

TCP_FORWARD = {
    "name": "ssh",
    "local_port": 2222,
    "protocol": "tcp",
    "password": "secret",
}
UDP_PUBLIC_FORWARD = {
    "name": "dns",
    "local_port": 5353,
    "protocol": "udp",
    "password": None,
}

CLIENT_IDENTITY = object()


class Engine:
    def __init__(self):
        self.events = []

    def _publish_session(self, *args, **kwargs):
        self.events.append((args, kwargs))


def make_session(forwards=None, **kwargs):
    return Session(
        Engine(),
        "a" * 32,
        [dict(forward) for forward in (forwards or [TCP_FORWARD])],
        "/tmp/rns",
        37428,
        37429,
        True,
        **kwargs,
    )


class FakeDatagramSocket:
    """Scripted ``recvmsg``: ``None`` means a timeout, exhaustion an OSError."""

    def __init__(self, items):
        self._items = list(items)

    def recvmsg(self, _size):
        if not self._items:
            raise OSError("socket gone")
        item = self._items.pop(0)
        if item is None:
            raise TimeoutError("timed out")
        data, flags, peer = item
        return data, [], flags, peer


def udp_bound(name, forward, sock):
    return BoundListener(
        name=name,
        actual_port=int(forward["local_port"]),
        forward=dict(forward),
        sockets=[sock],
        protocol="udp",
    )


def test_jitter_stays_within_expected_range():
    for attempt in range(1, 8):
        value = _jittered(attempt)
        base = min(60.0, float(2 ** (attempt - 1)))
        assert base * 0.8 <= value <= max(base * 1.2, 0.5)


def test_error_types_carry_codes():
    assert Retryable("no_path").code == "no_path"
    assert Terminal("auth").code == "auth"
    assert Terminal("protocol_mismatch", "mismatch", fields={"service": "dns"}).fields == {
        "service": "dns"
    }


def test_initial_snapshot_is_jsonable():
    session = Session(
        Engine(),
        "a" * 32,
        [{"name": "ssh", "local_port": 0, "password": "secret"}],
        "/tmp/rns",
        37428,
        37429,
        True,
    )
    snapshot = session.snapshot()
    assert snapshot["state"] == "idle"
    assert snapshot["forwards"][0]["name"] == "ssh"
    assert snapshot["forwards"][0]["actual_port"] is None
    assert session.active is False


def test_snapshot_reports_protocol_and_public_state():
    session = make_session([TCP_FORWARD, UDP_PUBLIC_FORWARD], udp_idle_timeout=5)
    snapshot = session.snapshot()
    first, second = snapshot["forwards"]
    assert (first["protocol"], first["public"]) == ("tcp", False)
    assert (second["protocol"], second["public"]) == ("udp", True)
    assert snapshot["error_params"] == {}
    assert session.udp_idle_timeout == 5.0


def test_error_params_carry_terminal_fields():
    session = make_session()
    session._fail(
        "protocol_mismatch",
        "service 'dns' is udp, not tcp",
        fields={"service": "dns", "expected": "tcp", "actual": "udp"},
    )
    snapshot = session.snapshot()
    assert snapshot["error_code"] == "protocol_mismatch"
    assert snapshot["error_params"] == {
        "service": "dns",
        "expected": "tcp",
        "actual": "udp",
    }
    error_events = [
        kwargs for args, kwargs in session.engine.events if args[1] == "error"
    ]
    assert error_events
    assert error_events[-1]["actual"] == "udp"
    assert error_events[-1]["code"] == "protocol_mismatch"
    session.stop()
    assert session.snapshot()["error_params"] == {}


def test_stop_without_start_is_safe():
    session = Session(
        Engine(),
        "a" * 32,
        [{"name": "ssh", "local_port": 2222, "password": "secret"}],
        "/tmp/rns",
        37428,
        37429,
        True,
    )
    session.stop()
    assert session.state == "idle"
    assert session.snapshot()["wanted"] is False


def test_udp_pending_buffer_is_bounded(monkeypatch):
    forward = dict(UDP_PUBLIC_FORWARD)
    peer = ("127.0.0.1", 40000)
    items = [(f"datagram-{index}".encode(), 0, peer) for index in range(40)]
    items.insert(20, None)  # exercise the timeout/reap branch
    sock = FakeDatagramSocket(items)
    session = make_session([forward])
    monkeypatch.setattr(session, "_open_udp_session", lambda *args, **kwargs: None)

    session._udp_loop(udp_bound(forward["name"], forward, sock), sock)

    key = (forward["name"], sock, peer)
    pending = session._udp_pending[key]
    assert len(pending) == 32
    assert pending[0] == b"datagram-0"


def test_udp_pending_keys_are_bounded_by_max_streams(monkeypatch):
    forward = dict(UDP_PUBLIC_FORWARD)
    peers = [("127.0.0.1", 41000 + index) for index in range(6)]
    sock = FakeDatagramSocket([(b"x", 0, peer) for peer in peers])
    session = make_session([forward], max_streams=3)
    monkeypatch.setattr(session, "_open_udp_session", lambda *args, **kwargs: None)

    session._udp_loop(udp_bound(forward["name"], forward, sock), sock)

    assert len(session._udp_pending) == 3


def test_truncated_datagram_is_dropped(monkeypatch):
    if not session_module._MSG_TRUNC:
        pytest.skip("platform does not expose MSG_TRUNC")
    forward = dict(UDP_PUBLIC_FORWARD)
    peer = ("127.0.0.1", 40000)
    sock = FakeDatagramSocket([(b"too-big", session_module._MSG_TRUNC, peer)])
    session = make_session([forward])
    monkeypatch.setattr(session, "_open_udp_session", lambda *args, **kwargs: None)

    session._udp_loop(udp_bound(forward["name"], forward, sock), sock)

    assert session._udp_pending == {}


class FakeIdleSession:
    label = "dns@5353/1"

    def __init__(self, idle):
        self.idle = idle
        self.aborted = []

    def idle_for(self, now=None):
        return self.idle

    def abort(self, reason, send_close=True):
        self.aborted.append(reason)


def test_idle_udp_session_is_reaped():
    session = make_session([UDP_PUBLIC_FORWARD], udp_idle_timeout=5)
    idle = FakeIdleSession(30.0)
    busy = FakeIdleSession(0.2)
    session._udp_sessions[("dns", object(), ("127.0.0.1", 1))] = idle
    session._udp_sessions[("dns", object(), ("127.0.0.1", 2))] = busy

    session._reap_udp_sessions()

    assert idle.aborted == ["idle timeout"]
    assert busy.aborted == []


def test_stream_closed_purges_udp_mapping():
    session = make_session([UDP_PUBLIC_FORWARD])
    fake = FakeIdleSession(0.0)
    fake.stream_id = 7
    fake.bytes_in = 0
    fake.bytes_out = 0
    fake.opened_at = 0.0
    fake.close_reason = "closed"
    key = ("dns", object(), ("127.0.0.1", 1))
    session._udp_sessions[key] = fake
    session._active[7] = {"stream": fake, "name": "dns", "stats": session._forward_stats[0]}

    session._stream_closed(session._forward_stats[0], "dns", fake)

    assert key not in session._udp_sessions
    assert session._active == {}


class FakeTransport:
    def __init__(self):
        self.opens = []
        self.windows = []

    def send_open(self, stream_id, version, service, password):
        self.opens.append((stream_id, version, service, password))

    def send_window(self, stream_id, credit):
        self.windows.append((stream_id, credit))


class FakeMux:
    def __init__(self):
        self.transport = FakeTransport()
        self.installed = {}
        self.released = []

    def wait_for_slot(self, timeout, stop_event):
        return True

    def allocate_id(self):
        return 1

    def reserve(self, stream_id):
        return True

    def release_reservation(self, stream_id):
        self.released.append(stream_id)

    def install(self, stream_id, stream):
        self.installed[stream_id] = stream
        return True

    def get(self, stream_id):
        return self.installed.get(stream_id)

    def remove(self, stream_id):
        self.installed.pop(stream_id, None)


class FakeDatagramSession:
    instances = []
    preset_closed = False

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.opened = threading.Event()
        self.opened.set()
        self.open_error = None
        self.closed = FakeDatagramSession.preset_closed
        self.bytes_in = 0
        self.bytes_out = 0
        self.opened_at = 0.0
        self.close_reason = None
        self.sent = []
        self.started = False
        FakeDatagramSession.instances.append(self)

    def send_initial_window(self):
        self.transport.send_window(self.stream_id, self.window)

    def start(self):
        self.started = True

    def send_local(self, data):
        self.sent.append(data)
        return True

    def abort(self, reason, send_close=True):
        self.closed = True
        self.close_reason = reason
        if self.on_closed is not None:
            self.on_closed(self)


def test_open_udp_session_flushes_pending_and_registers(monkeypatch):
    forward = dict(UDP_PUBLIC_FORWARD)
    peer = ("127.0.0.1", 40000)
    bound = udp_bound(forward["name"], forward, FakeDatagramSocket([]))
    session = make_session([forward], window=4096)
    mux = FakeMux()
    session.mux = mux
    session.link_ready = True
    bound.stats = session._forward_stats[0]
    key = (forward["name"], bound.sockets[0], peer)
    session._udp_pending[key] = [b"first", b"second"]
    FakeDatagramSession.instances = []
    monkeypatch.setattr(session_module, "DatagramSession", FakeDatagramSession)

    session._open_udp_session(bound, bound.sockets[0], peer, key)

    stream = FakeDatagramSession.instances[0]
    assert stream.transport is mux.transport
    assert stream.peer == peer
    assert stream.owns_socket is False
    assert stream.idle_timeout == session.udp_idle_timeout
    assert mux.transport.opens == [(1, 1, "dns", "")]
    assert mux.transport.windows == [(1, 4096)]
    assert stream.sent == [b"first", b"second"]
    assert stream.started is True
    assert session._udp_pending == {}
    assert session._udp_sessions[key] is stream
    assert session._active[1]["stream"] is stream
    assert session._forward_stats[0]["streams"] == 1


def _patch_probe(monkeypatch, responses):
    requests = []

    def fake_link_request(link, path, data, timeout, stop_event=None):
        requests.append((path, dict(data)))
        return responses.pop(0)

    monkeypatch.setattr(session_module.rns_ops, "link_request", fake_link_request)
    return requests


def test_probe_accepts_matching_tcp_protocol(monkeypatch):
    responses = [{"ok": True, "service": {"name": "ssh", "protocol": "tcp"}}]
    requests = _patch_probe(monkeypatch, responses)
    session = make_session([TCP_FORWARD])

    session._probe_all(object())

    assert requests == [("/probe", {"service": "ssh", "password": "secret"})]


def test_probe_sends_empty_password_for_public_forward(monkeypatch):
    responses = [{"ok": True, "service": {"name": "dns", "protocol": "udp"}}]
    requests = _patch_probe(monkeypatch, responses)
    session = make_session([UDP_PUBLIC_FORWARD])

    session._probe_all(object())

    assert requests == [("/probe", {"service": "dns", "password": ""})]


def test_probe_rejects_protocol_mismatch(monkeypatch):
    responses = [{"ok": True, "service": {"name": "dns", "protocol": "udp"}}]
    _patch_probe(monkeypatch, responses)
    session = make_session([TCP_FORWARD])

    with pytest.raises(Terminal) as caught:
        session._probe_all(object())

    assert caught.value.code == "protocol_mismatch"
    assert caught.value.fields == {"service": "ssh", "expected": "tcp", "actual": "udp"}


def test_probe_missing_protocol_defaults_to_tcp(monkeypatch):
    responses = [
        {"ok": True, "service": {"name": "ssh"}},
        {"ok": True, "service": {"name": "dns"}},
    ]
    _patch_probe(monkeypatch, responses)
    session = make_session([TCP_FORWARD, UDP_PUBLIC_FORWARD])

    with pytest.raises(Terminal) as caught:
        session._probe_all(object())

    assert caught.value.fields["actual"] == "tcp"
    assert caught.value.fields["service"] == "dns"


def test_probe_rejects_bad_credentials(monkeypatch):
    responses = [{"ok": False}]
    _patch_probe(monkeypatch, responses)
    session = make_session([TCP_FORWARD])

    with pytest.raises(AuthError):
        session._probe_all(object())


def test_probe_reuses_payload_for_duplicate_service_names(monkeypatch):
    responses = [{"ok": True, "service": {"name": "ssh", "protocol": "tcp"}}]
    requests = _patch_probe(monkeypatch, responses)
    second = dict(TCP_FORWARD, local_port=2223)
    session = make_session([TCP_FORWARD, second])

    session._probe_all(object())

    assert len(requests) == 1


def test_send_open_failure_keeps_stream_accounting_consistent(monkeypatch):
    forward = dict(UDP_PUBLIC_FORWARD)
    peer = ("127.0.0.1", 40001)
    bound = udp_bound(forward["name"], forward, FakeDatagramSocket([]))
    session = make_session([forward], window=4096)
    mux = FakeMux()
    session.mux = mux
    session.link_ready = True
    bound.stats = session._forward_stats[0]
    key = (forward["name"], bound.sockets[0], peer)
    session._udp_pending[key] = [b"first"]

    def boom(*_args):
        raise OSError("link not ready")

    mux.transport.send_open = boom
    monkeypatch.setattr(session_module, "DatagramSession", FakeDatagramSession)
    FakeDatagramSession.instances = []
    FakeDatagramSession.preset_closed = False

    session._open_udp_session(bound, bound.sockets[0], peer, key)

    assert session._active == {}
    assert session._forward_stats[0]["streams"] == 0
    assert session.snapshot()["counters"]["streams"] == 0
    assert session._udp_sessions == {}


def test_already_closed_stream_is_not_registered(monkeypatch):
    forward = dict(UDP_PUBLIC_FORWARD)
    peer = ("127.0.0.1", 40002)
    bound = udp_bound(forward["name"], forward, FakeDatagramSocket([]))
    session = make_session([forward], window=4096)
    mux = FakeMux()
    session.mux = mux
    session.link_ready = True
    bound.stats = session._forward_stats[0]
    key = (forward["name"], bound.sockets[0], peer)

    monkeypatch.setattr(session_module, "DatagramSession", FakeDatagramSession)
    FakeDatagramSession.instances = []
    FakeDatagramSession.preset_closed = True
    try:
        session._open_udp_session(bound, bound.sockets[0], peer, key)
    finally:
        FakeDatagramSession.preset_closed = False

    assert session._active == {}
    assert session._forward_stats[0]["streams"] == 0


def test_datagram_drops_are_counted_in_the_snapshot():
    forward = dict(UDP_PUBLIC_FORWARD)
    session = make_session([forward])
    stream = FakeDatagramSession(
        stream_id=3,
        transport=FakeTransport(),
        window=4096,
        dropped_oversized=2,
        dropped_queue_full=1,
    )
    stream.bytes_in = 10
    stream.bytes_out = 20
    session._active[3] = {
        "stream": stream,
        "name": forward["name"],
        "stats": session._forward_stats[0],
    }
    session._forward_stats[0]["streams"] = 1

    session._stream_closed(session._forward_stats[0], forward["name"], stream)

    item = session.snapshot()["forwards"][0]
    assert item["dropped"] == 3
    assert item["streams"] == 0


def test_session_defaults_to_anonymous_and_automatic_stamping():
    session = make_session()

    assert session.client_identity is None
    assert session.stamp_mode == "auto"
    assert session.stamp_cost is None


def test_identity_and_stamp_policy_reach_open_link(monkeypatch):
    session = make_session(client_identity=CLIENT_IDENTITY, stamp_mode="cost", stamp_cost=9.0)
    monkeypatch.setattr(session_module.rns_ops, "resolve_identity", lambda *a, **k: "server")
    calls = []

    def fake_open_link(identity, timeout, stop_event=None, on_closed=None, **kwargs):
        calls.append((identity, kwargs))
        raise session_module.rns_ops.Cancelled("stop")

    monkeypatch.setattr(session_module.rns_ops, "open_link", fake_open_link)

    with pytest.raises(session_module.rns_ops.Cancelled):
        session._establish()

    ((identity, kwargs),) = calls
    assert identity == "server"
    assert kwargs["client_identity"] is CLIENT_IDENTITY
    assert kwargs["stamp_mode"] == "cost"
    assert kwargs["stamp_cost"] == 9.0
    assert kwargs["server_hash"] == bytes.fromhex("a" * 32)


def test_anonymous_session_does_not_pass_a_client_identity(monkeypatch):
    session = make_session()
    monkeypatch.setattr(session_module.rns_ops, "resolve_identity", lambda *a, **k: "server")
    calls = []

    def fake_open_link(identity, timeout, stop_event=None, on_closed=None, **kwargs):
        calls.append(kwargs)
        raise session_module.rns_ops.Cancelled("stop")

    monkeypatch.setattr(session_module.rns_ops, "open_link", fake_open_link)

    with pytest.raises(session_module.rns_ops.Cancelled):
        session._establish()

    assert calls[0]["client_identity"] is None
    assert calls[0]["stamp_mode"] == "auto"
    assert calls[0]["stamp_cost"] is None


def test_probe_all_forwards_the_client_identity(monkeypatch):
    session = make_session(client_identity=CLIENT_IDENTITY)
    calls = []

    def fake_probe(link, service, password, timeout, stop_event=None, *, client_identity=None):
        calls.append((service, client_identity))
        return {"ok": True, "service": {"name": service, "protocol": "tcp"}}

    monkeypatch.setattr(session_module.rns_ops, "probe_request", fake_probe)

    session._probe_all(object())

    assert calls == [("ssh", CLIENT_IDENTITY)]


def test_stamp_unsupported_is_terminal_and_not_retried(monkeypatch):
    session = make_session(client_identity=CLIENT_IDENTITY, stamp_mode="cost", stamp_cost=9.0)
    monkeypatch.setattr(session_module.rns_ops, "resolve_identity", lambda *a, **k: "server")
    opens = []

    def fake_open_link(*args, **kwargs):
        opens.append(kwargs)
        raise session_module.rns_ops.StampUnsupported("this Reticulum cannot generate stamps")

    monkeypatch.setattr(session_module.rns_ops, "open_link", fake_open_link)

    session.start()
    session._thread.join(timeout=5)
    snapshot = session.snapshot()

    assert session._thread.is_alive() is False
    assert snapshot["state"] == "error"
    assert snapshot["wanted"] is False
    assert snapshot["error_code"] == "stamp_unsupported"
    assert "cannot generate stamps" in snapshot["error"]
    assert len(opens) == 1
    published = [args[1] for args, _kwargs in session.engine.events if len(args) > 1]
    assert "session_retry" not in published
    assert published[-1] == "session_state"



class StubMux:
    """Stand-in for a Mux: `_wait_link` only hands it over."""

    def __init__(self, label):
        self.label = label


def _install_link(session, mux, age=0.0, used=False):
    with session._lock:
        session.link = object()
        session.mux = mux
        session.link_ready = True
        session._link_since = session_module.time.monotonic() - age
        session._link_used = used
        session._refreshing = False
    return session.link


def test_a_recent_handoff_postpones_the_refresh():
    session = make_session(idle_refresh=20.0, connect_timeout=1.0)
    mux = StubMux("first")
    _install_link(session, mux, age=25.0)
    # Another connection was served on this Link a moment ago, so this one must
    # not tear the Link down underneath it.
    session._last_handoff = session_module.time.monotonic()

    assert session._wait_link() is mux


def test_a_handed_off_link_still_refreshes_when_it_never_opened_a_stream():
    session = make_session(idle_refresh=20.0)
    _install_link(session, StubMux("first"), age=25.0)
    session._last_handoff = session_module.time.monotonic() - 21.0

    # A connection that was handed the Link but never sent an OPEN must not
    # stop the next one from refreshing it.
    assert session._link_used is False
    assert session._link_age_locked() >= 20.0


def test_stamp_state_carries_the_effective_cost():
    session = make_session()

    session._stamp_started(8)

    assert session.snapshot()["state"] == "stamping"
    assert session.snapshot()["stamp_cost"] == 8.0
    published = [kwargs for args, kwargs in session.engine.events if args and args[1] == "session_state"]
    assert published[-1]["state"] == "stamping"
    assert published[-1]["stamp_cost"] == 8.0
    assert session.active is True


def test_stamp_state_is_skipped_after_stop():
    session = make_session()
    session._stop.set()

    session._stamp_started(8)

    assert session.snapshot()["state"] == "idle"
    assert session.engine.events == []


def test_wait_link_returns_a_fresh_link_without_refreshing(monkeypatch):
    session = make_session(idle_refresh=20.0)
    mux = StubMux("first")
    link = _install_link(session, mux, age=5.0)
    closes = []
    monkeypatch.setattr(session_module.rns_ops, "close_link", closes.append)

    assert session._wait_link() is mux
    assert closes == []
    assert link is not None
    # The hand-off alone does not mark the Link as used: only an OPEN does.
    assert session._link_used is False
    assert session._last_handoff is not None


def test_wait_link_refreshes_an_unused_stale_link_once(monkeypatch):
    session = make_session(idle_refresh=1.0, connect_timeout=5.0)
    stale_mux = StubMux("stale")
    stale_link = _install_link(session, stale_mux, age=5.0)
    closes = []
    monkeypatch.setattr(session_module.rns_ops, "close_link", closes.append)
    result = {}

    worker = threading.Thread(target=lambda: result.setdefault("mux", session._wait_link()))
    worker.start()
    deadline = session_module.time.monotonic() + 3.0
    while not session._link_lost.is_set() and session_module.time.monotonic() < deadline:
        session_module.time.sleep(0.01)

    assert session._link_lost.is_set() is True
    assert closes == [stale_link]
    fresh_mux = StubMux("fresh")
    with session._lock:
        session.link = object()
        session.mux = fresh_mux
        session.link_ready = True
        session._link_since = session_module.time.monotonic()
        session._link_used = False
        session._refreshing = False
    worker.join(timeout=5)

    assert result["mux"] is fresh_mux
    assert closes == [stale_link]


def test_concurrent_waiters_share_one_refresh(monkeypatch):
    session = make_session(idle_refresh=1.0, connect_timeout=5.0)
    stale_link = _install_link(session, StubMux("stale"), age=5.0)
    closes = []
    monkeypatch.setattr(session_module.rns_ops, "close_link", closes.append)
    results = []
    workers = [
        threading.Thread(target=lambda: results.append(session._wait_link())) for _ in range(3)
    ]
    for worker in workers:
        worker.start()
    deadline = session_module.time.monotonic() + 3.0
    while not session._link_lost.is_set() and session_module.time.monotonic() < deadline:
        session_module.time.sleep(0.01)

    fresh_mux = StubMux("fresh")
    with session._lock:
        session.link = object()
        session.mux = fresh_mux
        session.link_ready = True
        session._link_since = session_module.time.monotonic()
        session._link_used = False
        session._refreshing = False
    for worker in workers:
        worker.join(timeout=5)

    assert closes == [stale_link]
    assert results == [fresh_mux] * 3


def test_wait_link_never_refreshes_a_used_link(monkeypatch):
    session = make_session(idle_refresh=1.0, connect_timeout=1.0)
    mux = StubMux("used")
    _install_link(session, mux, age=3600.0, used=True)
    closes = []
    monkeypatch.setattr(session_module.rns_ops, "close_link", closes.append)

    assert session._wait_link() is mux
    assert closes == []
