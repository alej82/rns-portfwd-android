import errno
import socket
from dataclasses import dataclass, field

from .validation import ValidationError


class BindFailure(RuntimeError):
    def __init__(self, message, code="port_in_use", forward=None):
        super().__init__(message)
        self.code = code
        self.forward = forward


@dataclass
class BoundListener:
    name: str
    actual_port: int
    forward: dict = field(default_factory=dict)
    sockets: list = field(default_factory=list)
    ipv6: bool = False
    degraded: bool = False
    stats: dict | None = None

    def close(self):
        for sock in self.sockets:
            try:
                sock.close()
            except OSError:
                pass
        self.sockets = []

    def fileno(self):
        return [sock.fileno() for sock in self.sockets]


def _listen(sock):
    sock.listen(64)
    sock.settimeout(0.5)
    return sock


def open_listener(forward, host4="127.0.0.1", host6="::1"):
    """Bind one forward on IPv4 loopback and, when possible, IPv6 loopback.

    Port 0 is resolved on IPv4 first and the real port is reused for IPv6.
    IPv6 failure degrades to IPv4; IPv4 failure is terminal.
    """
    port = int(forward["local_port"])
    if port != 0 and port < 1024:
        raise ValidationError(
            "local ports below 1024 are not available to a normal Android app",
            code="low_port",
            field="port",
        )

    sock4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock4.bind((host4, port))
        _listen(sock4)
    except OSError as error:
        sock4.close()
        code = "port_in_use" if error.errno in (errno.EADDRINUSE, errno.EACCES) else "bind_failed"
        raise BindFailure(
            f"could not bind {host4}:{port} ({error})",
            code=code,
            forward=forward["name"],
        ) from error

    actual_port = sock4.getsockname()[1]
    bound = BoundListener(
        name=forward["name"],
        actual_port=actual_port,
        forward=forward,
        sockets=[sock4],
    )

    if socket.has_ipv6:
        sock6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock6.bind((host6, actual_port))
            _listen(sock6)
            bound.sockets.append(sock6)
            bound.ipv6 = True
        except OSError:
            try:
                sock6.close()
            except OSError:
                pass
            bound.degraded = True
    return bound


def open_all(forwards, host4="127.0.0.1", host6="::1"):
    created = []
    try:
        for forward in forwards:
            created.append(open_listener(forward, host4=host4, host6=host6))
    except Exception:
        for bound in created:
            bound.close()
        raise
    return created
