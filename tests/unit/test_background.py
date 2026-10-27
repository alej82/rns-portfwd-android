from portfwd_android.background import BackgroundSession


class FakeBackend:
    def __init__(self):
        self.started = []
        self.updated = []
        self.stopped = 0
        self.fail_start = False

    def start(self, title, text, stop_label="Detener"):
        if self.fail_start:
            return False
        self.started.append((title, text, stop_label))
        return True

    def update(self, title, text, stop_label="Detener"):
        self.updated.append((title, text, stop_label))
        return True

    def stop(self):
        self.stopped += 1

    def running(self):
        return bool(self.started)


def test_start_update_stop():
    backend = FakeBackend()
    session = BackgroundSession(backend=backend, android=True)
    assert session.start("title", "one") is True
    assert session.update("title", "two") is True
    assert session.running() is True
    assert session.stop() is True
    assert backend.stopped == 1
    assert backend.updated == [("title", "two", "Detener")]


def test_rejected_start_is_reported():
    backend = FakeBackend()
    backend.fail_start = True
    session = BackgroundSession(backend=backend, android=True)
    assert session.start("title", "text") is False
    assert "rejected" in session.last_error


def test_update_without_start_does_nothing():
    backend = FakeBackend()
    session = BackgroundSession(backend=backend, android=True)
    assert session.update("title", "text") is False
    assert backend.updated == []
