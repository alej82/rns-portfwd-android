from .i18n import t
from .validation import MAX_STAMP_COST


def error_text(code, field=None, message=None, **params):
    if code == "config" and field in (
        "hash",
        "name",
        "port",
        "protocol",
        "password",
        "duplicates",
        "stamp_mode",
        "stamp_cost",
    ):
        key = f"error.{field}"
        if field == "stamp_cost":
            return t(key, max=f"{MAX_STAMP_COST:g}")
        return t(key)
    mapping = {
        "config": "error.config",
        "busy": "error.busy",
        "shared_instance": "error.shared_instance",
        "no_path": "error.no_path",
        "link_failed": "error.link_failed",
        "auth": "error.auth",
        "protocol_mismatch": "error.protocol_mismatch",
        "stamp_unsupported": "error.stamp_unsupported",
        "identity_error": "error.identity_error",
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
    if key == "error.protocol_mismatch":
        # `actual` is the field name published by Session._fail; `protocol` is
        # accepted as an alias by callers that pass the wire field directly.
        actual = params.get("protocol") or params.get("actual")
        return t(
            key,
            service=params.get("service") or "?",
            protocol=actual or "?",
            expected=params.get("expected") or "?",
        )
    return t(key)


def state_text(state, /, **params):
    # `state` is positional-only so a caller can splat a session snapshot
    # (which also carries a "state" key) without a duplicate-keyword TypeError.
    if state == "stamping":
        cost = params.get("stamp_cost")
        return t("session.state.stamping", cost=f"{float(cost):g}" if cost else "?")
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
