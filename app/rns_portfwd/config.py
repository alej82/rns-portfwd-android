"""Parsing, validation and secret handling for the command line surfaces."""

from __future__ import annotations

import json
import math
import os
import re
import socket
import sys
from collections import defaultdict
from dataclasses import dataclass, replace

from .constants import MAX_LINK_STAMP_COST, NAME_PATTERN
from .errors import ConfigError

_NAME_RE = re.compile(rf"^{NAME_PATTERN}$")
_IDENTITY_HASH_RE = re.compile(r"[0-9A-Fa-f]{32}")
_SECRET_PLACEHOLDER = "-"
_MAX_SECRET_DOC = 1024 * 1024


@dataclass(frozen=True)
class Service:
    host: str
    port: int
    name: str
    password: str | None
    protocol: str = "tcp"
    allowed_identities: frozenset[bytes] = frozenset()

    @property
    def is_restricted(self) -> bool:
        return self.password is not None or bool(self.allowed_identities)

    def public(self) -> dict:
        return {
            "name": self.name,
            "protocol": self.protocol,
            "auth": self.is_restricted,
        }


@dataclass(frozen=True)
class Forward:
    name: str
    local_port: int
    password: str | None
    protocol: str = "tcp"


_MAX_SECRET_LEN = 4096


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(f"invalid service name {name!r}: use {NAME_PATTERN}")
    if len(name) > 255:
        raise ConfigError("service names must be at most 255 characters")
    return name


def validate_secret(value: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must not be empty")
    if len(value.encode("utf-8")) > _MAX_SECRET_LEN:
        raise ConfigError(f"{field} is too long (max {_MAX_SECRET_LEN} bytes)")
    return value


def validate_link_stamp_cost(value) -> float:
    """Validate and normalize a link-request stamp cost."""
    if isinstance(value, bool):
        raise ConfigError("link stamp cost must be a number")
    try:
        cost = float(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"invalid link stamp cost {value!r}: must be a number"
        ) from None
    if math.isnan(cost) or math.isinf(cost):
        raise ConfigError(f"invalid link stamp cost {value!r}: must be finite")
    if not 0.0 <= cost <= MAX_LINK_STAMP_COST:
        raise ConfigError(
            f"link stamp cost must be between 0 and {MAX_LINK_STAMP_COST:g}"
        )
    return cost


def parse_identity_hash(value: str) -> bytes:
    """Parse a 32-hexadecimal-character Reticulum identity hash."""
    text = value.strip() if isinstance(value, str) else ""
    if not _IDENTITY_HASH_RE.fullmatch(text):
        raise ConfigError(
            f"invalid identity hash {value!r}: expected a 32-hexadecimal-character "
            "identity hash"
        )
    return bytes.fromhex(text)


def parse_allow_identity(value: str) -> tuple[str, bytes]:
    """Parse a ``SERVICE:HASH`` allow-list entry."""
    if not isinstance(value, str):
        raise ConfigError(f"invalid --allow-identity {value!r}: expected SERVICE:HASH")
    name, separator, hash_text = value.partition(":")
    if not separator:
        raise ConfigError(f"invalid --allow-identity {value!r}: expected SERVICE:HASH")
    validate_name(name)
    return name, parse_identity_hash(hash_text)


def _parse_port(value: str, *, allow_zero: bool, field: str) -> int:
    try:
        port = int(value, 10)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid {field} port {value!r}: must be numeric") from None
    low = 0 if allow_zero else 1
    if not low <= port <= 65535:
        raise ConfigError(f"invalid {field} port {port}: must be between {low} and 65535")
    return port


def _parse_expose(value: str, protocol: str) -> Service:
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ConfigError("unterminated bracketed IPv6 host")
        host = value[1:end]
        remainder = value[end + 1 :]
        if not remainder.startswith(":"):
            raise ConfigError("expected ':' after bracketed host")
        fields = remainder[1:].split(":", 2)
        if len(fields) != 3:
            raise ConfigError("expected [host]:port:name:password")
        port_text, name, password = fields
    else:
        fields = value.split(":", 3)
        if len(fields) != 4:
            raise ConfigError("expected host:port:name:password")
        host, port_text, name, password = fields
        if ":" in host:
            raise ConfigError("IPv6 hosts must be written in brackets")
    if not host:
        raise ConfigError("backend host must not be empty")
    port = _parse_port(port_text, allow_zero=False, field="backend")
    validate_name(name)
    validate_secret(password, "service password")
    return Service(host=host, port=port, name=name, password=password, protocol=protocol)


def parse_expose(value: str) -> Service:
    return _parse_expose(value, "tcp")


def parse_expose_udp(value: str) -> Service:
    return _parse_expose(value, "udp")


def _parse_expose_public(value: str, protocol: str) -> Service:
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ConfigError("unterminated bracketed IPv6 host")
        host = value[1:end]
        remainder = value[end + 1 :]
        if not remainder.startswith(":"):
            raise ConfigError("expected ':' after bracketed host")
        fields = remainder[1:].split(":", 1)
        if len(fields) != 2:
            raise ConfigError("expected [host]:port:name")
        port_text, name = fields
    else:
        fields = value.split(":", 2)
        if len(fields) != 3:
            raise ConfigError("expected host:port:name")
        host, port_text, name = fields
        if ":" in host:
            raise ConfigError("IPv6 hosts must be written in brackets")
    if not host:
        raise ConfigError("backend host must not be empty")
    port = _parse_port(port_text, allow_zero=False, field="backend")
    validate_name(name)
    return Service(host=host, port=port, name=name, password=None, protocol=protocol)


def parse_expose_public(value: str) -> Service:
    return _parse_expose_public(value, "tcp")


def parse_expose_udp_public(value: str) -> Service:
    return _parse_expose_public(value, "udp")


def _parse_forward(value: str, protocol: str) -> Forward:
    fields = value.split(":", 2)
    if len(fields) != 3:
        raise ConfigError("expected name:local_port:password")
    name, port_text, password = fields
    validate_name(name)
    port = _parse_port(port_text, allow_zero=True, field="local")
    validate_secret(password, "forward password")
    return Forward(name=name, local_port=port, password=password, protocol=protocol)


def parse_forward(value: str) -> Forward:
    return _parse_forward(value, "tcp")


def parse_forward_udp(value: str) -> Forward:
    return _parse_forward(value, "udp")


def _parse_forward_public(value: str, protocol: str) -> Forward:
    fields = value.split(":", 1)
    if len(fields) != 2:
        raise ConfigError("expected name:local_port")
    name, port_text = fields
    validate_name(name)
    port = _parse_port(port_text, allow_zero=True, field="local")
    return Forward(name=name, local_port=port, password=None, protocol=protocol)


def parse_forward_public(value: str) -> Forward:
    return _parse_forward_public(value, "tcp")


def parse_forward_udp_public(value: str) -> Forward:
    return _parse_forward_public(value, "udp")


def validate_allow_identities(
    services: list[Service], entries: list[tuple[str, bytes]]
) -> None:
    """Reject allow-list entries that name a service the server does not expose."""
    known = {service.name for service in services}
    for name, _ in entries:
        if name not in known:
            raise ConfigError(f"--allow-identity names unknown service {name!r}")


def apply_allow_identities(
    services: list[Service], entries: list[tuple[str, bytes]]
) -> list[Service]:
    """Add identity hashes to service allow-lists, rebuilding frozen services."""
    if not entries:
        return services
    validate_allow_identities(services, entries)
    additions: dict[str, set[bytes]] = defaultdict(set)
    for name, identity_hash in entries:
        additions[name].add(identity_hash)
    resolved: list[Service] = []
    for service in services:
        extra = additions.get(service.name)
        if not extra:
            resolved.append(service)
            continue
        resolved.append(
            replace(
                service,
                allowed_identities=service.allowed_identities | frozenset(extra),
            )
        )
    return resolved


def validate_services(services: list[Service]) -> None:
    if not services:
        raise ConfigError("at least one --expose service is required")
    seen: set[str] = set()
    for service in services:
        if service.name in seen:
            raise ConfigError(f"duplicate service name {service.name!r}")
        seen.add(service.name)


def validate_forwards(forwards: list[Forward]) -> None:
    if not forwards:
        raise ConfigError("at least one --forward is required")
    used: set[tuple[str, int]] = set()
    for forward in forwards:
        if forward.local_port == 0:
            continue
        key = (forward.protocol, forward.local_port)
        if key in used:
            raise ConfigError(
                f"duplicate local {forward.protocol} port {forward.local_port}"
            )
        used.add(key)


def read_secret_source(password_stdin: bool, secrets_fd: int | None) -> tuple[dict | None, bool]:
    if password_stdin and secrets_fd is not None:
        raise ConfigError("--password-stdin and --secrets-fd are mutually exclusive")
    if not password_stdin and secrets_fd is None:
        return None, False

    if password_stdin:
        try:
            raw = sys.stdin.read(_MAX_SECRET_DOC + 1)
        except OSError as exc:
            raise ConfigError(f"could not read secrets from stdin: {exc}") from None
    else:
        assert secrets_fd is not None
        fd = int(secrets_fd)
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_SECRET_DOC:
                    raise ConfigError("secret document is too large")
                chunks.append(chunk)
        except OSError as exc:
            raise ConfigError(f"could not read secrets from fd {secrets_fd}: {exc}") from None
        raw = b"".join(chunks).decode("utf-8", errors="strict")

    if len(raw) > _MAX_SECRET_DOC:
        raise ConfigError("secret document is too large")
    try:
        document = json.loads(raw)
    except (ValueError, TypeError):
        raise ConfigError("secret source must contain a JSON object") from None
    if not isinstance(document, dict):
        raise ConfigError("secret source must contain a JSON object")
    return document, True


def _doc_service(document: dict, name: str) -> str:
    services = document.get("services")
    if not isinstance(services, dict):
        raise ConfigError("secret document needs a 'services' object")
    value = services.get(name)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"secret document has no password for service {name!r}")
    return value


def _doc_list_password(document: dict) -> str:
    value = document.get("list_password")
    if not isinstance(value, str) or not value:
        raise ConfigError("secret document has no 'list_password'")
    return value


def apply_server_secrets(
    services: list[Service],
    list_password: str | None,
    document: dict | None,
    source_used: bool,
) -> tuple[list[Service], str | None]:
    if not source_used:
        for service in services:
            if service.password == _SECRET_PLACEHOLDER:
                raise ConfigError("service password '-' requires --password-stdin or --secrets-fd")
        if list_password == _SECRET_PLACEHOLDER:
            raise ConfigError("list password '-' requires --password-stdin or --secrets-fd")
        for service in services:
            if service.password is not None:
                validate_secret(service.password, "service password")
        if list_password is not None:
            validate_secret(list_password, "list password")
        return services, list_password

    assert document is not None
    for service in services:
        if service.password is not None and service.password != _SECRET_PLACEHOLDER:
            raise ConfigError(
                "inline and external secrets are ambiguous: use '-' for every --expose password"
            )
    if list_password not in (None, _SECRET_PLACEHOLDER):
        raise ConfigError(
            "inline and external secrets are ambiguous: use '--list-password -' with a secret source"
        )

    resolved_services = [
        Service(
            host=s.host,
            port=s.port,
            name=s.name,
            password=(
                _doc_service(document, s.name)
                if s.password == _SECRET_PLACEHOLDER
                else None
            ),
            protocol=s.protocol,
            allowed_identities=s.allowed_identities,
        )
        for s in services
    ]
    resolved_list = _doc_list_password(document) if list_password == _SECRET_PLACEHOLDER else None
    for service in resolved_services:
        if service.password is not None:
            validate_secret(service.password, "service password")
    if resolved_list is not None:
        validate_secret(resolved_list, "list password")
    return resolved_services, resolved_list


def apply_forward_secrets(
    forwards: list[Forward],
    document: dict | None,
    source_used: bool,
) -> list[Forward]:
    if not source_used:
        for forward in forwards:
            if forward.password == _SECRET_PLACEHOLDER:
                raise ConfigError("forward password '-' requires --password-stdin or --secrets-fd")
        return forwards

    assert document is not None
    for forward in forwards:
        if forward.password is not None and forward.password != _SECRET_PLACEHOLDER:
            raise ConfigError(
                "inline and external secrets are ambiguous: use '-' for every --forward password"
            )
    resolved = [
        Forward(
            name=f.name,
            local_port=f.local_port,
            password=(
                _doc_service(document, f.name) if f.password == _SECRET_PLACEHOLDER else None
            ),
            protocol=f.protocol,
        )
        for f in forwards
    ]
    for forward in resolved:
        if forward.password is not None:
            validate_secret(forward.password, "forward password")
    return resolved


def resolve_list_password(
    inline: str | None,
    document: dict | None,
    source_used: bool,
) -> str | None:
    if not source_used:
        if inline == _SECRET_PLACEHOLDER:
            raise ConfigError("list password '-' requires --password-stdin or --secrets-fd")
        if inline is not None:
            validate_secret(inline, "list password")
        return inline
    assert document is not None
    if inline not in (None, _SECRET_PLACEHOLDER):
        raise ConfigError(
            "inline and external secrets are ambiguous: use '--list-password -' with a secret source"
        )
    value = _doc_list_password(document)
    validate_secret(value, "list password")
    return value


def resolve_probe_password(
    inline: str | None,
    document: dict | None,
    source_used: bool,
    service_name: str,
) -> str:
    if not source_used:
        if inline == _SECRET_PLACEHOLDER:
            raise ConfigError("probe password '-' requires --password-stdin or --secrets-fd")
        if inline is None:
            return ""
        return validate_secret(inline, "probe password")
    assert document is not None
    if inline is None:
        return ""
    if inline != _SECRET_PLACEHOLDER:
        raise ConfigError(
            "inline and external secrets are ambiguous: use '--service-password -' with a secret source"
        )
    return validate_secret(_doc_service(document, service_name), "probe password")


def default_server_name() -> str:
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return name or "rns-portfwd"
