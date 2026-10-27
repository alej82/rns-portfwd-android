import socket
import sys
import types

import pytest
from portfwd_android import reticulum


class FakeReticulumClass:
    called = 0
    instance = None

    def __init__(self, *args, **kwargs):
        type(self).called += 1
        self.kwargs = kwargs
        self.is_connected_to_shared_instance = True

    @classmethod
    def get_instance(cls):
        return cls.instance


def _fake_rns(reticulum_class):
    module = types.SimpleNamespace(Reticulum=reticulum_class)
    return module


def test_preflight_success_and_failure():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)
    try:
        assert reticulum.preflight(port) is True
    finally:
        server.close()
    with pytest.raises(reticulum.SharedInstanceUnavailable):
        reticulum.preflight(port, timeout=0.1)


def test_preflight_failure_does_not_construct_reticulum(monkeypatch, tmp_path):
    class NeverConstructed(FakeReticulumClass):
        def __init__(self, *args, **kwargs):
            raise AssertionError("Reticulum must not be constructed")

    monkeypatch.setitem(sys.modules, "RNS", _fake_rns(NeverConstructed))
    with pytest.raises(reticulum.SharedInstanceUnavailable):
        reticulum.start_reticulum(
            str(tmp_path),
            shared_port=1,
            require_shared=True,
            check_preflight=True,
        )


def test_existing_instance_is_reused(monkeypatch, tmp_path):
    class Existing(FakeReticulumClass):
        instance = types.SimpleNamespace(is_connected_to_shared_instance=True)

    monkeypatch.setitem(sys.modules, "RNS", _fake_rns(Existing))
    Existing.called = 0
    instance = reticulum.start_reticulum(str(tmp_path), shared_port=37428, check_preflight=False)
    assert instance is Existing.instance
    assert Existing.called == 0


def test_system_error_becomes_actionable(monkeypatch, tmp_path):
    class Broken(FakeReticulumClass):
        instance = None

        def __init__(self, *args, **kwargs):
            raise SystemError("not connected")

    monkeypatch.setitem(sys.modules, "RNS", _fake_rns(Broken))
    with pytest.raises(reticulum.SharedInstanceUnavailable) as error:
        reticulum.start_reticulum(str(tmp_path), shared_port=37428, check_preflight=False)
    assert "shared instance" in str(error.value)


def test_close_reticulum_never_calls_exit_handler(monkeypatch):
    called = []
    module = types.SimpleNamespace(
        Reticulum=types.SimpleNamespace(exit_handler=lambda: called.append(True))
    )
    monkeypatch.setitem(sys.modules, "RNS", module)
    reticulum.close_reticulum()
    assert called == []
