import socket

import pytest
from portfwd_android.listeners import BindFailure, open_all, open_listener
from portfwd_android.validation import ValidationError


def _forward(port):
    return {"name": "echo", "local_port": port, "password": "secret"}


def test_ephemeral_port_binds_v4_and_optional_v6():
    bound = open_listener(_forward(0))
    try:
        assert bound.actual_port > 0
        sock = socket.create_connection(("127.0.0.1", bound.actual_port), timeout=2)
        sock.close()
        if bound.ipv6:
            sock6 = socket.create_connection(("::1", bound.actual_port), timeout=2)
            sock6.close()
    finally:
        bound.close()


def test_explicit_port_and_duplicate_failure():
    first = open_listener(_forward(0))
    port = first.actual_port
    try:
        with pytest.raises(BindFailure) as error:
            open_listener(_forward(port))
        assert error.value.code == "port_in_use"
    finally:
        first.close()


def test_low_port_is_rejected_before_bind():
    with pytest.raises(ValidationError) as error:
        open_listener(_forward(22))
    assert error.value.code == "low_port"


def test_open_all_closes_previous_on_failure():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    first_port = probe.getsockname()[1]
    probe.close()

    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    blocker_port = blocker.getsockname()[1]
    try:
        with pytest.raises(BindFailure):
            open_all([_forward(first_port), _forward(blocker_port)])
        # The first listener from the failed batch must have been closed.
        check = socket.socket()
        try:
            check.bind(("127.0.0.1", first_port))
        finally:
            check.close()
    finally:
        blocker.close()
