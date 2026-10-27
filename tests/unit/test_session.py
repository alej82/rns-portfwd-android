
from portfwd_android.session import Retryable, Session, Terminal, _jittered


def test_jitter_stays_within_expected_range():
    for attempt in range(1, 8):
        value = _jittered(attempt)
        base = min(60.0, float(2 ** (attempt - 1)))
        assert base * 0.8 <= value <= max(base * 1.2, 0.5)


def test_error_types_carry_codes():
    assert Retryable("no_path").code == "no_path"
    assert Terminal("auth").code == "auth"


def test_initial_snapshot_is_jsonable():
    class Engine:
        def _publish_session(self, *_args, **_kwargs):
            pass

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


def test_stop_without_start_is_safe():
    class Engine:
        def _publish_session(self, *_args, **_kwargs):
            pass

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
