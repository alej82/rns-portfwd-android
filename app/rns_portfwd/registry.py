"""Validated service catalog with authentication and connection limits."""

from __future__ import annotations

import threading
from collections import defaultdict

from .auth import constant_time_equals, dummy_compare
from .config import Service


class ServiceRegistry:
    def __init__(
        self,
        services: list[Service],
        list_password: str | None,
        max_connections_per_service: int,
    ):
        self._services = {service.name: service for service in services}
        self._list_password = list_password
        self._max_connections = max_connections_per_service
        self._active: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def get(self, name: str) -> Service | None:
        return self._services.get(name)

    @property
    def list_enabled(self) -> bool:
        return self._list_password is not None

    def check_list_password(self, password: str | None) -> bool:
        if self._list_password is None:
            return False
        return constant_time_equals(password, self._list_password)

    def authenticate(self, name: str, password: str | None) -> Service | None:
        service = self._services.get(name)
        if service is None:
            dummy_compare(password)
            return None
        if constant_time_equals(password, service.password):
            return service
        return None

    def list_public(self) -> list[dict]:
        return [service.public() for service in self._services.values()]

    def probe(self, name: str, password: str | None) -> dict | None:
        service = self.authenticate(name, password)
        if service is None:
            return None
        return service.public()

    def acquire(self, name: str) -> bool:
        with self._lock:
            if self._active[name] >= self._max_connections:
                return False
            self._active[name] += 1
            return True

    def release(self, name: str) -> None:
        with self._lock:
            if self._active[name] > 0:
                self._active[name] -= 1

    def active_connections(self, name: str) -> int:
        with self._lock:
            return self._active[name]
