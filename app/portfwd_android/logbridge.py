import logging
import threading

from rns_portfwd.logging import get_logger, redact


class LogBridge(logging.Handler):
    """Mirror RNS/portfwd records into the bounded UI event stream."""

    def __init__(self, events, level=logging.INFO):
        super().__init__(level=level)
        self.events = events
        self._lock = threading.Lock()
        self._installed = False

    def emit(self, record):
        try:
            message = redact(record.getMessage())
        except Exception:
            message = "<unformattable log record>"
        self.events.publish(
            "log",
            level=record.levelname.lower(),
            source=record.name,
            message=message,
        )

    def install(self):
        with self._lock:
            if self._installed:
                return self
            for name in ("rns_portfwd", "RNS"):
                logger = logging.getLogger(name)
                logger.addHandler(self)
                logger.setLevel(min(logger.level or logging.INFO, logging.INFO))
            self._installed = True
            return self

    def uninstall(self):
        with self._lock:
            if not self._installed:
                return
            for name in ("rns_portfwd", "RNS"):
                logging.getLogger(name).removeHandler(self)
            self._installed = False


def install(events):
    return LogBridge(events).install()


def logger():
    return get_logger()
