import json
import os
import threading
import time
import uuid

from .paths import profiles_path


def _write_private(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class ProfileStore:
    def __init__(self, path=None):
        self.path = path or profiles_path()
        self._lock = threading.RLock()
        self._profiles = []
        self.load()

    def load(self):
        with self._lock:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                data = []
            if isinstance(data, dict):
                data = data.get("profiles", [])
            profiles = []
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict) and isinstance(entry.get("hash"), str):
                        profiles.append(self._normalise(entry))
            self._profiles = profiles
            return self

    @staticmethod
    def _normalise(entry):
        forwards = []
        for forward in entry.get("forwards") or []:
            if not isinstance(forward, dict):
                continue
            try:
                port = int(forward.get("local_port", forward.get("port", 0)))
            except (TypeError, ValueError):
                continue
            forwards.append(
                {
                    "name": str(forward.get("name", "")),
                    "local_port": port,
                    "password": str(forward.get("password", "")),
                }
            )
        try:
            last_used = float(entry.get("last_used") or 0)
        except (TypeError, ValueError):
            last_used = 0.0
        return {
            "id": str(entry.get("id") or uuid.uuid4().hex),
            "label": str(entry.get("label") or entry["hash"][:8]),
            "hash": str(entry["hash"]).strip().lower(),
            "forwards": forwards,
            "list_password": entry.get("list_password") or None,
            "last_used": last_used,
        }

    def save(self):
        with self._lock:
            _write_private(self.path, self._profiles)
            return self

    def ordered(self):
        with self._lock:
            return sorted(
                (dict(entry) for entry in self._profiles),
                key=lambda entry: entry.get("last_used") or 0,
                reverse=True,
            )

    def get(self, profile_id):
        with self._lock:
            for entry in self._profiles:
                if entry["id"] == profile_id:
                    return dict(entry)
        return None

    def find_hash(self, server_hash):
        text = str(server_hash or "").strip().lower()
        with self._lock:
            for entry in self._profiles:
                if entry["hash"] == text:
                    return dict(entry)
        return None

    def upsert(self, server_hash, label, forwards, list_password=None, last_used=None):
        profile_hash = str(server_hash or "").strip().lower()
        if len(profile_hash) != 32:
            raise ValueError("profile hash must have 32 characters")
        with self._lock:
            entry = None
            for candidate in self._profiles:
                if candidate["hash"] == profile_hash:
                    entry = candidate
                    break
            if entry is None:
                entry = {
                    "id": uuid.uuid4().hex,
                    "hash": profile_hash,
                    "label": "",
                    "forwards": [],
                    "list_password": None,
                    "last_used": 0.0,
                }
                self._profiles.append(entry)
            entry["label"] = str(label or "").strip() or profile_hash[:8]
            entry["forwards"] = [
                {
                    "name": str(forward["name"]),
                    "local_port": int(forward["local_port"]),
                    "password": str(forward["password"]),
                }
                for forward in forwards
            ]
            entry["list_password"] = list_password or None
            entry["last_used"] = float(last_used if last_used is not None else time.time())
            self.save()
            return dict(entry)

    def touch(self, profile_id):
        with self._lock:
            for entry in self._profiles:
                if entry["id"] == profile_id:
                    entry["last_used"] = time.time()
                    self.save()
                    return dict(entry)
        return None

    def delete(self, profile_id):
        with self._lock:
            before = len(self._profiles)
            self._profiles = [entry for entry in self._profiles if entry["id"] != profile_id]
            if len(self._profiles) != before:
                self.save()
                return True
            return False
