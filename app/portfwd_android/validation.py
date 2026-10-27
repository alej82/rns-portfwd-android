import re

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HEX = set("0123456789abcdef")
_MAX_NAME = 255
_MAX_SECRET = 4096
_MIN_PORT = 1024
_MAX_PORT = 65535


class ValidationError(ValueError):
    def __init__(self, message, code="config", field=None):
        super().__init__(message)
        self.code = code
        self.field = field


def validate_hash(value):
    text = str(value or "").strip().lower()
    if len(text) != 32 or any(character not in _HEX for character in text):
        raise ValidationError(
            "invalid server hash: expected 32 hexadecimal characters",
            code="config",
            field="hash",
        )
    return text


def validate_name(value):
    name = str(value or "").strip()
    if not name or not _NAME_RE.match(name) or len(name) > _MAX_NAME:
        raise ValidationError(
            "invalid service name: use letters, digits, '_' or '-' (max 255)",
            code="config",
            field="name",
        )
    return name


def validate_password(value, field="password"):
    if not isinstance(value, str) or not value:
        raise ValidationError("password must not be empty", code="config", field=field)
    if len(value.encode("utf-8")) > _MAX_SECRET:
        raise ValidationError(
            f"password is too long (max {_MAX_SECRET} bytes)",
            code="config",
            field=field,
        )
    return value


def validate_port(value, allow_zero=True):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValidationError("local port must be numeric", code="config", field="port") from None
    if port == 0 and allow_zero:
        return 0
    if port < _MIN_PORT or port > _MAX_PORT:
        code = "low_port" if 0 < port < _MIN_PORT else "config"
        raise ValidationError(
            f"local port must be 0 (ephemeral) or between {_MIN_PORT} and {_MAX_PORT}",
            code=code,
            field="port",
        )
    return port


def validate_forward(value):
    if isinstance(value, dict):
        name = value.get("name")
        port = value.get("local_port", value.get("port"))
        password = value.get("password")
    else:
        name = getattr(value, "name", None)
        port = getattr(value, "local_port", None)
        password = getattr(value, "password", None)
    return {
        "name": validate_name(name),
        "local_port": validate_port(port),
        "password": validate_password(password),
    }


def validate_forwards(values):
    if not isinstance(values, (list, tuple)) or not values:
        raise ValidationError("at least one forward is required", code="config", field="forwards")
    forwards = []
    used = set()
    for value in values:
        forward = validate_forward(value)
        port = forward["local_port"]
        if port != 0 and port in used:
            raise ValidationError(
                f"duplicate local port {port}",
                code="port_in_use",
                field="port",
            )
        if port != 0:
            used.add(port)
        forwards.append(forward)
    return forwards


def validate_config(server_hash, forwards, list_password=None):
    resolved_hash = validate_hash(server_hash)
    resolved_forwards = validate_forwards(forwards)
    if list_password is not None and not isinstance(list_password, str):
        raise ValidationError("list password must be text", code="config", field="list_password")
    return resolved_hash, resolved_forwards, list_password or None


def to_vendor_forwards(forwards):
    from rns_portfwd.config import Forward

    return [
        Forward(
            name=forward["name"],
            local_port=int(forward["local_port"]),
            password=forward["password"],
        )
        for forward in forwards
    ]
