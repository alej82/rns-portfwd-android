import collections
import threading
import time


class EventBus:
    """Bounded, ordered, non-blocking event queue for the shell."""

    def __init__(self, maxlen=500):
        self._events = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._sequence = 0

    @property
    def sequence(self):
        with self._lock:
            return self._sequence

    def publish(self, event_type, **fields):
        with self._lock:
            self._sequence += 1
            event = {
                "seq": self._sequence,
                "ts": time.time(),
                "type": str(event_type),
            }
            event.update(fields)
            self._events.append(event)
            return event

    def drain(self):
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def history(self):
        with self._lock:
            return list(self._events)

    def clear(self):
        with self._lock:
            self._events.clear()
