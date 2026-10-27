from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest

from . import harness

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def instance():
    root = Path(tempfile.mkdtemp(prefix="portfwd-it-"))
    shared = harness.SharedInstance(root)
    shared.start()
    try:
        yield shared
    finally:
        shared.stop()


def test_forward_echo_and_half_close(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(instance, {"echo": echo.port})
    engine = None
    try:
        engine = harness.start_engine(instance, server_hash, ["echo:0:secret"])
        port = engine.ports["echo"]
        payload = b"hello-portfwd"
        assert harness.echo_roundtrip(port, payload) == payload
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_two_forwards_share_one_link(instance):
    first = harness.TCPServer(harness.EchoHandler)
    second = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance, {"first": first.port, "second": second.port}
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, ["first:0:secret", "second:0:secret"]
        )
        assert harness.echo_roundtrip(engine.ports["first"], b"first") == b"first"
        assert harness.echo_roundtrip(engine.ports["second"], b"second") == b"second"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        first.close()
        second.close()


def test_large_binary_transfer(instance):
    bulk = harness.TCPServer(harness.BulkHandler)
    server, server_hash = harness.start_server(instance, {"bulk": bulk.port})
    engine = None
    try:
        engine = harness.start_engine(instance, server_hash, ["bulk:0:secret"])
        data = harness.read_all(engine.ports["bulk"])
        assert len(data) == 64 * 65536
        assert data == b"B" * len(data)
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        bulk.close()


def test_list_and_probe(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance, {"echo": echo.port}, password="secret", list_password="listpass"
    )
    try:
        events = harness.run_aux(
            instance, server_hash, ["--list", "--list-password", "listpass"]
        )
        results = [event for event in events if event.get("type") == "list_result"]
        assert results and results[-1]["ok"] is True
        names = [item["name"] for item in results[-1]["services"]]
        assert names == ["echo"]

        events = harness.run_aux(instance, server_hash, ["--probe", "echo:secret"])
        results = [event for event in events if event.get("type") == "probe_result"]
        assert results and results[-1]["ok"] is True
        assert results[-1]["service"]["name"] == "echo"
    finally:
        server.terminate()
        echo.close()


def test_list_disabled(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance, {"echo": echo.port}, list_password=None
    )
    try:
        events = harness.run_aux(instance, server_hash, ["--list"])
        results = [event for event in events if event.get("type") == "list_result"]
        assert results and results[-1]["ok"] is False
        assert results[-1]["error_code"] == "list_disabled"
    finally:
        server.terminate()
        echo.close()


def test_auth_failure_is_terminal(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(instance, {"echo": echo.port})
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, ["echo:0:wrong"], wait=False
        )
        assert engine.proc.wait(timeout=30) != 0
        assert any("auth" in line for line in engine.lines)
    finally:
        if engine is not None:
            engine.kill()
        server.terminate()
        echo.close()


def test_port_in_use_is_terminal(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(instance, {"echo": echo.port})
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, [f"echo:{port}:secret"], wait=False
        )
        assert engine.proc.wait(timeout=30) != 0
        assert any("port_in_use" in line for line in engine.lines)
    finally:
        if engine is not None:
            engine.kill()
        blocker.close()
        server.terminate()
        echo.close()
