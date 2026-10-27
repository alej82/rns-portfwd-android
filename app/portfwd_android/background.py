"""Thin wrapper around the same-process Android foreground session service."""

import threading

from .paths import is_android

SERVICE_CLASS = "io.areche.portfwd.PortfwdService"
LOG_TAG = "PORTFWD_BACKGROUND"


class _AndroidBackend:
    def __init__(self):
        from jnius import autoclass

        self._service = autoclass(SERVICE_CLASS)
        self._activity = autoclass("org.kivy.android.PythonActivity").mActivity
        if self._activity is None:
            raise RuntimeError("activity unavailable")

    def start(self, title, text, stop_label):
        return bool(
            self._service.start(
                self._activity, str(title), str(text), str(stop_label)
            )
        )

    def update(self, title, text, stop_label):
        return bool(
            self._service.update(
                self._activity, str(title), str(text), str(stop_label)
            )
        )

    def stop(self):
        self._service.stop(self._activity)

    def running(self):
        return bool(self._service.isRunning())


class BackgroundSession:
    def __init__(self, backend=None, android=None):
        self._backend = backend
        self._backend_loaded = backend is not None
        self._android = android
        self._lock = threading.Lock()
        self._started = False
        self._error = None

    @property
    def last_error(self):
        with self._lock:
            return self._error

    def _load(self):
        if self._backend_loaded:
            return self._backend
        self._backend_loaded = True
        android = is_android() if self._android is None else bool(self._android)
        if not android:
            return None
        try:
            self._backend = _AndroidBackend()
        except Exception as error:
            self._error = f"{type(error).__name__}: {error}"
            print(f"{LOG_TAG} unavailable: {self._error}", flush=True)
            self._backend = None
        return self._backend

    def available(self):
        with self._lock:
            return self._load() is not None

    def start(self, title, text, stop_label="Detener"):
        with self._lock:
            backend = self._load()
            if backend is None:
                self._error = self._error or "background session unavailable"
                return False
            try:
                started = bool(backend.start(title, text, stop_label))
            except Exception as error:
                self._error = f"{type(error).__name__}: {error}"
                print(f"{LOG_TAG} start failed: {self._error}", flush=True)
                self._started = False
                return False
            self._started = started
            if not started:
                self._error = "the foreground service rejected the start"
            else:
                self._error = None
            return started

    def update(self, title, text, stop_label="Detener"):
        with self._lock:
            if not self._started:
                return False
            backend = self._load()
            if backend is None:
                return False
            try:
                updated = bool(backend.update(title, text, stop_label))
            except Exception as error:
                self._error = f"{type(error).__name__}: {error}"
                print(f"{LOG_TAG} update failed: {self._error}", flush=True)
                return False
            if not updated:
                self._started = False
                self._error = "the foreground service is no longer running"
            return updated

    def stop(self):
        with self._lock:
            was_started = self._started
            self._started = False
            backend = self._load()
            if backend is None or not was_started:
                return False
            try:
                backend.stop()
            except Exception as error:
                self._error = f"{type(error).__name__}: {error}"
                print(f"{LOG_TAG} stop failed: {self._error}", flush=True)
                return False
            self._error = None
            return True

    def running(self):
        with self._lock:
            backend = self._load()
            if backend is None:
                return False
            try:
                return bool(backend.running())
            except Exception:
                return self._started


_default = BackgroundSession()


def available():
    return _default.available()


def start(title, text, stop_label="Detener"):
    return _default.start(title, text, stop_label)


def update(title, text, stop_label="Detener"):
    return _default.update(title, text, stop_label)


def stop():
    return _default.stop()


def running():
    return _default.running()


def last_error():
    return _default.last_error
