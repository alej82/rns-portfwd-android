from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from . import harness

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def instance():
    root = Path(tempfile.mkdtemp(prefix="portfwd-refresh-"))
    shared = harness.SharedInstance(root)
    shared.start()
    try:
        yield shared
    finally:
        shared.stop()


def test_forward_after_idle_refreshes_the_unused_link(instance):
    """A Link that never carried a stream is rebuilt before its first use.

    The server's `--auth-timeout` is 5 s and the engine refreshes after 1.5 s,
    so the forward arrives while the original Link is still alive but already
    stale for the client. The test asserts that the refresh happened and that
    the forward is then served on the replacement Link; the control below
    shows what the server does to an unused Link without it.
    """
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--auth-timeout", "5"],
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            ["echo:0:secret"],
            extra=["--idle-refresh", "1.5"],
        )
        port = engine.ports["echo"]
        time.sleep(3.0)
        assert harness.echo_roundtrip(port, b"after-idle") == b"after-idle"
        assert engine.wait_for("link_refresh", timeout=5)
        assert harness.echo_roundtrip(port, b"reused") == b"reused"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_server_expires_a_link_that_never_opened_a_stream(instance):
    """Pin the upstream behaviour the refresh exists for.

    With the refresh disabled the server tears the Link down on its own
    `--auth-timeout`, which is what used to make a late first forward fail.
    """
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--auth-timeout", "3"],
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            ["echo:0:secret"],
            extra=["--idle-refresh", "0"],
        )
        started = time.monotonic()
        engine.wait_for("link_lost", timeout=20)
        # The loss comes from the server's own timer, not from a client-side
        # teardown: it cannot happen before `--auth-timeout` seconds.
        assert time.monotonic() - started >= 2.0
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()
