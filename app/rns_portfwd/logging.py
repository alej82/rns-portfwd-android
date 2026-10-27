"""Logging helpers with mandatory secret redaction."""

from __future__ import annotations

import logging as _logging
import sys

_SECRETS: set[str] = set()
_REDACTED = "***"


def register_secret(value: str | None) -> None:
    if value:
        _SECRETS.add(value)
        _SECRETS.add(str(value))


def redact(text: str) -> str:
    if not text:
        return text
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret:
            text = text.replace(secret, _REDACTED)
    return text


class RedactionFilter(_logging.Filter):
    def filter(self, record: _logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        return True


def setup_logging(verbosity: int = 0) -> _logging.Logger:
    logger = _logging.getLogger("rns_portfwd")
    if logger.handlers:
        return logger
    level = _logging.DEBUG if verbosity >= 2 else _logging.INFO
    logger.setLevel(level)
    handler = _logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(_logging.Formatter("[%(levelname)s] %(message)s"))
    handler.addFilter(RedactionFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> _logging.Logger:
    return _logging.getLogger("rns_portfwd")


def rns_loglevel(verbosity: int) -> int:
    try:
        import RNS

        if verbosity <= 0:
            return RNS.LOG_CRITICAL
        if verbosity == 1:
            return RNS.LOG_NOTICE
        return min(RNS.LOG_DEBUG, RNS.LOG_NOTICE + verbosity)
    except Exception:
        return 0
