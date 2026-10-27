import logging

from portfwd_android.events import EventBus
from portfwd_android.logbridge import LogBridge
from rns_portfwd.logging import register_secret


def test_bridge_redacts_registered_secrets():
    secret = "unique-secret-9f3a"
    register_secret(secret)
    bus = EventBus()
    bridge = LogBridge(bus)
    record = logging.LogRecord(
        "rns_portfwd", logging.INFO, __file__, 1, "password=%s", (secret,), None
    )
    bridge.emit(record)
    event = bus.drain()[0]
    assert event["type"] == "log"
    assert secret not in event["message"]
    assert "***" in event["message"]
