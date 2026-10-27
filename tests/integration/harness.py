from __future__ import annotations

import json
import os
import queue
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
TOOL = ROOT / "tools" / "headless_engine.py"
ENV = dict(os.environ, PYTHONPATH=str(APP))


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class SharedInstance:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.control_port = free_port()
        self.config = self.root / "config"
        self.config.write_text(
            "[reticulum]\n"
            "enable_transport = no\n"
            "share_instance = yes\n"
            "shared_instance_type = tcp\n"
            f"shared_instance_port = {self.port}\n"
            f"instance_control_port = {self.control_port}\n"
            "\n[logging]\n"
            "loglevel = 0\n"
        )
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "RNS.Utilities.rnsd", "--config", str(self.root)],
            env=ENV,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
                sock.close()
                return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("rnsd exited during startup") from None
                time.sleep(0.1)
        raise RuntimeError("rnsd did not open its shared instance port")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                break
            self.request.sendall(data)
        try:
            self.request.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class BulkHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            for _ in range(64):
                self.request.sendall(b"B" * 65536)
        except OSError:
            pass


class TCPServer:
    def __init__(self, handler):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Process:
    def __init__(self, args: list[str]):
        self.proc = subprocess.Popen(
            args,
            env=ENV,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.lines: list[str] = []
        self.queue: queue.Queue = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            stripped = line.rstrip()
            self.lines.append(stripped)
            self.queue.put(stripped)
        self.queue.put(None)

    def wait_for(self, needle: str, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.queue.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise AssertionError(
                        f"process exited with {self.proc.returncode}: " + "\n".join(self.lines)
                    ) from None
                continue
            if line is None:
                raise AssertionError(
                    f"process exited while waiting for {needle!r}: " + "\n".join(self.lines)
                )
            if needle in line:
                return line
        raise AssertionError(f"timed out waiting for {needle!r}: " + "\n".join(self.lines))

    def terminate(self, timeout: float = 15.0) -> int:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=5)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)


class EngineProcess(Process):
    def __init__(self, args: list[str]):
        super().__init__(args)
        self.ports: dict[str, int] = {}

    def wait_ready(self, timeout: float = 60.0) -> dict[str, int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.queue.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise AssertionError(
                        f"engine exited with {self.proc.returncode}: " + "\n".join(self.lines)
                    ) from None
                continue
            if line is None:
                raise AssertionError("engine exited before READY: " + "\n".join(self.lines))
            if line.startswith("LISTEN "):
                _, name, port = line.split()
                self.ports[name] = int(port)
            if line == "READY":
                return self.ports
            if line.startswith("session error") or line.startswith("transport error"):
                raise AssertionError("engine failed: " + "\n".join(self.lines))
        raise AssertionError("timed out waiting for READY: " + "\n".join(self.lines))


def start_server(
    instance: SharedInstance,
    services: dict[str, int],
    extra: list[str] | None = None,
    password: str = "secret",
    list_password: str | None = "listpass",
) -> tuple[Process, str]:
    identity = instance.root / f"server-{uuid.uuid4().hex}.id"
    args = [
        sys.executable,
        "-m",
        "rns_portfwd.cli_server",
        "--config",
        str(instance.root),
        "--identity",
        str(identity),
        "--name",
        "integration",
    ]
    if list_password is not None:
        args += ["--list-password", list_password]
    for name, port in services.items():
        args += ["--expose", f"127.0.0.1:{port}:{name}:{password}"]
    args += extra or []
    process = Process(args)
    line = process.wait_for("destination:", timeout=30)
    return process, line.split(":", 1)[1].strip()


def start_engine(
    instance: SharedInstance,
    server_hash: str,
    forwards: list[str],
    extra: list[str] | None = None,
    wait: bool = True,
) -> EngineProcess:
    args = [
        sys.executable,
        str(TOOL),
        server_hash,
        "--configdir",
        str(instance.root),
        "--statedir",
        str(instance.root / f"state-{uuid.uuid4().hex}"),
        "--shared-port",
        str(instance.port),
        "--control-port",
        str(instance.control_port),
        "--duration",
        "60",
    ]
    for forward in forwards:
        args += ["--forward", forward]
    args += extra or []
    process = EngineProcess(args)
    if wait:
        process.wait_ready()
    return process


def run_aux(
    instance: SharedInstance,
    server_hash: str,
    args: list[str],
    timeout: float = 60,
) -> list[dict]:
    command = [
        sys.executable,
        str(TOOL),
        server_hash,
        "--configdir",
        str(instance.root),
        "--statedir",
        str(instance.root / f"state-{uuid.uuid4().hex}"),
        "--shared-port",
        str(instance.port),
        "--control-port",
        str(instance.control_port),
        "--json",
    ] + args
    result = subprocess.run(command, env=ENV, capture_output=True, text=True, timeout=timeout)
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def echo_roundtrip(port: int, payload: bytes, timeout: float = 30) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)
    finally:
        sock.close()


def read_all(port: int, timeout: float = 60) -> bytes:
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)
    finally:
        sock.close()
