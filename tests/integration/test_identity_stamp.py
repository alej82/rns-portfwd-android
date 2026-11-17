from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from . import harness

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def instance():
    root = Path(tempfile.mkdtemp(prefix="portfwd-access-"))
    shared = harness.SharedInstance(root)
    shared.start()
    try:
        yield shared
    finally:
        shared.stop()


def make_identity(path: Path) -> str:
    """Create a Reticulum identity file in a subprocess and return its hash."""
    code = (
        "import sys\n"
        "import RNS\n"
        "identity = RNS.Identity()\n"
        "identity.to_file(sys.argv[1])\n"
        "print(identity.hash.hex())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        env=harness.ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_allow_listed_identity_can_forward(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    identity_path = instance.root / "allowed.id"
    identity_hash = make_identity(identity_path)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--allow-identity", f"echo:{identity_hash}"],
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            ["echo:0:secret"],
            extra=["--identity", str(identity_path)],
        )
        port = engine.ports["echo"]
        assert harness.echo_roundtrip(port, b"identity-ok") == b"identity-ok"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_anonymous_client_is_rejected_by_allow_list(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    identity_path = instance.root / "listed-only.id"
    identity_hash = make_identity(identity_path)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--allow-identity", f"echo:{identity_hash}"],
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance, server_hash, ["echo:0:secret"], wait=False
        )
        line = engine.wait_for('"code": "auth"', timeout=90)
        assert "authentication failed" in line
        assert engine.proc.wait(timeout=15) == 2
    finally:
        if engine is not None:
            engine.kill()
        server.terminate()
        echo.close()


def test_probe_from_allowed_identity(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    identity_path = instance.root / "probe.id"
    identity_hash = make_identity(identity_path)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--allow-identity", f"echo:{identity_hash}"],
    )
    try:
        events = harness.run_aux(
            instance,
            server_hash,
            ["--probe", "echo:secret", "--identity", str(identity_path)],
        )
        results = [event for event in events if event["type"] == "probe_result"]
        assert results and results[-1]["ok"] is True
    finally:
        server.terminate()
        echo.close()


def test_stamping_server_accepts_stamped_client(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--link-stamp-cost", "6"],
    )
    engine = None
    try:
        engine = harness.start_engine(instance, server_hash, ["echo:0:secret"])
        port = engine.ports["echo"]
        assert harness.echo_roundtrip(port, b"stamped") == b"stamped"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()


def test_unstamped_client_fails_against_stamping_server(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    server, server_hash = harness.start_server(
        instance,
        {"echo": echo.port},
        extra=["--link-stamp-cost", "6"],
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            ["echo:0:secret"],
            extra=["--stamp-mode", "off"],
            wait=False,
        )
        line = engine.wait_for("session_retry", timeout=90)
        assert "link_failed" in line
    finally:
        if engine is not None:
            engine.kill()
        server.terminate()
        echo.close()


def test_public_service_with_identity_and_stamping(instance):
    echo = harness.TCPServer(harness.EchoHandler)
    identity_path = instance.root / "public.id"
    identity_hash = make_identity(identity_path)
    server, server_hash = harness.start_server(
        instance,
        {},
        public_services={"open": echo.port},
        extra=[
            "--allow-identity",
            f"open:{identity_hash}",
            "--link-stamp-cost",
            "6",
        ],
    )
    engine = None
    try:
        engine = harness.start_engine(
            instance,
            server_hash,
            [],
            public_forwards=["open:0"],
            extra=["--identity", str(identity_path)],
        )
        port = engine.ports["open"]
        assert harness.echo_roundtrip(port, b"public") == b"public"
    finally:
        if engine is not None:
            engine.terminate()
        server.terminate()
        echo.close()
