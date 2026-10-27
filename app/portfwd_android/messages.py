from .i18n import t


def error_text(code, field=None, message=None, **params):
    if code == "config" and field in ("hash", "name", "port", "password", "duplicates"):
        return t(f"error.{field}")
    mapping = {
        "config": "error.config",
        "busy": "error.busy",
        "shared_instance": "error.shared_instance",
        "no_path": "error.no_path",
        "link_failed": "error.link_failed",
        "auth": "error.auth",
        "version": "error.version",
        "port_in_use": "error.port_in_use",
        "low_port": "error.low_port",
        "list_disabled": "error.list_disabled",
        "notification_permission": "error.notification_permission",
        "bind_failed": "error.bind_failed",
        "stream_limit": "error.stream_limit",
        "backend": "error.backend",
        "internal": "error.internal",
    }
    key = mapping.get(code)
    if key is None:
        return t("error.unknown", code=code)
    if key in ("error.config", "error.internal"):
        return t(key, message=message or code)
    if key == "error.port_in_use":
        return t(key, port=params.get("port", "?"))
    return t(key)


def state_text(state):
    key = {
        "idle": "session.state.idle",
        "resolving": "session.state.resolving",
        "linking": "session.state.linking",
        "probing": "session.state.probing",
        "listening": "session.state.listening",
        "reconnecting": "session.state.reconnecting",
        "error": "session.state.error",
    }.get(state)
    return t(key) if key else str(state)
