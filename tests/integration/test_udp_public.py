from __future__ import annotations

import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

from . import harness

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def instance():
    root = Path(tempfile.mkdtemp(prefix="portfwd-udp-it-"))
    shared = harness.SharedInstance(root)
    shared.start()
    try:
        yield shared
    finally:
        shared.stop()


def _two_sockets(port):
    first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    first.settimeout(30)
    first.connect(("127.0.0.1", port))
    second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    second.settimeout(30)
    second.connect(("127.0.0.1", port))
    return first, second


def test_udp_forward_roundtrip(instance):
    echo = harness.UDPServer()
    server, server_hash = harness.start_server(instance, {}, udp_services={"dns": echo.port})
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, [], udp_forwards=["dns:0:secret"]
        )
        payload = b"\x00\x01udp-hello"
        assert harness.udp_roundtrip(engine.ports["dns"], payload) == payload
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_udp_forward_isolates_two_local_sources(instance):
    echo = harness.UDPServer()
    server, server_hash = harness.start_server(instance, {}, udp_services={"dns": echo.port})
    engine = None
    first = second = None
    try:
        engine = harness.start_engine(
            instance, server_hash, [], udp_forwards=["dns:0:secret"]
        )
        port = engine.ports["dns"]
        first, second = _two_sockets(port)
        first.send(b"from-first")
        second.send(b"from-second")
        assert first.recv(65536) == b"from-first"
        assert second.recv(65536) == b"from-second"
        first.send(b"again-first")
        assert first.recv(65536) == b"again-first"
    finally:
        for sock in (first, second):
            if sock is not None:
                sock.close()
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


@pytest.mark.skipif(not socket.has_ipv6, reason="no IPv6 loopback")
def test_udp_forward_accepts_ipv6_loopback(instance):
    echo = harness.UDPServer()
    server, server_hash = harness.start_server(instance, {}, udp_services={"dns": echo.port})
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, [], udp_forwards=["dns:0:secret"]
        )
        payload = b"over-v6"
        assert harness.udp_roundtrip_v6(engine.ports["dns"], payload) == payload
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_public_tcp_forward_needs_no_password(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance, {}, public_services={"web": echo.port}
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, [], public_forwards=["web:0"]
        )
        assert harness.echo_roundtrip(engine.ports["web"], b"public") == b"public"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_public_udp_forward_needs_no_password(instance):
    echo = harness.UDPServer()
    server, server_hash = harness.start_server(
        instance, {}, public_udp_services={"dns": echo.port}
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, [], public_udp_forwards=["dns:0"]
        )
        payload = b"public-udp"
        assert harness.udp_roundtrip(engine.ports["dns"], payload) == payload
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_public_and_private_forwards_share_one_link(instance):
    public_echo = harness.TCPServer(harness.EchoHandler)
    private_echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance,
        {"ssh": private_echo.port},
        public_services={"web": public_echo.port},
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            ["ssh:0:secret"],
            public_forwards=["web:0"],
        )
        assert harness.echo_roundtrip(engine.ports["web"], b"public") == b"public"
        assert harness.echo_roundtrip(engine.ports["ssh"], b"private") == b"private"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        public_echo.close()
        private_echo.close()


def test_list_reports_public_and_udp_services(instance):
    tcp_echo = harness.TCPServer(harness.EchoHandler)
    udp_echo = harness.UDPServer()
    server, server_hash = harness.start_server(
        instance,
        {"ssh": tcp_echo.port},
        udp_services={"dns": udp_echo.port},
        public_services={"web": tcp_echo.port},
    )
    try:
        events = harness.run_aux(
            instance, server_hash, ["--list", "--list-password", "listpass"]
        )
        results = [event for event in events if event.get("type") == "list_result"]
        assert results and results[-1]["ok"] is True
        services = {item["name"]: item for item in results[-1]["services"]}
        assert services["ssh"]["protocol"] == "tcp"
        assert services["ssh"]["auth"] is True
        assert services["dns"]["protocol"] == "udp"
        assert services["dns"]["auth"] is True
        assert services["web"]["auth"] is False
    finally:
        server.terminate()
        tcp_echo.close()
        udp_echo.close()


def test_probe_public_service_without_password(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance, {}, public_services={"web": echo.port}
    )
    try:
        events = harness.run_aux(instance, server_hash, ["--probe", "web"])
        results = [event for event in events if event.get("type") == "probe_result"]
        assert results and results[-1]["ok"] is True
        assert results[-1]["service"]["auth"] is False
    finally:
        server.terminate()
        echo.close()


def test_udp_forward_against_tcp_service_is_terminal(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(instance, {"echo": echo.port})
    engine = None
    try:
        engine = harness.EngineProcess(
            [
                sys.executable,
                str(harness.TOOL),
                server_hash,
                "--configdir",
                str(instance.root),
                "--statedir",
                str(instance.root / "state-mismatch"),
                "--shared-port",
                str(instance.port),
                "--control-port",
                str(instance.control_port),
                "--duration",
                "30",
                "--forward-udp",
                "echo:0:secret",
            ]
        )
        line = engine.wait_for("protocol_mismatch", timeout=45)
        assert "protocol_mismatch" in line
        assert engine.proc.wait(timeout=20) == 2
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_tcp_forward_against_udp_service_is_terminal(instance):
    echo = harness.UDPServer()
    server, server_hash = harness.start_server(instance, {}, udp_services={"dns": echo.port})
    engine = None
    try:
        engine = harness.EngineProcess(
            [
                sys.executable,
                str(harness.TOOL),
                server_hash,
                "--configdir",
                str(instance.root),
                "--statedir",
                str(instance.root / "state-mismatch-tcp"),
                "--shared-port",
                str(instance.port),
                "--control-port",
                str(instance.control_port),
                "--duration",
                "30",
                "--forward",
                "dns:0:secret",
            ]
        )
        line = engine.wait_for("protocol_mismatch", timeout=45)
        assert "protocol_mismatch" in line
        assert engine.proc.wait(timeout=20) == 2
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_udp_stream_survives_idle_and_reopens(instance):
    echo = harness.UDPServer()
    server, server_hash = harness.start_server(instance, {}, udp_services={"dns": echo.port})
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            [],
            udp_forwards=["dns:0:secret"],
            extra=["--udp-idle-timeout", "1"],
        )
        port = engine.ports["dns"]
        assert harness.udp_roundtrip(port, b"before-idle") == b"before-idle"
        time.sleep(2.5)
        assert harness.udp_roundtrip(port, b"after-idle") == b"after-idle"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()
