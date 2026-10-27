import json
import os

from .paths import settings_path

DEFAULTS = {"language": "system"}
LANGUAGES = ("system", "es", "en")


def _write_private(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class Settings(dict):
    def __init__(self, path=None):
        super().__init__(DEFAULTS)
        self.path = path or settings_path()
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        language = str(stored.get("language", "system")).strip().lower()
        self["language"] = language if language in LANGUAGES else "system"
        return self

    def save(self):
        _write_private(self.path, {"language": self["language"]})
        return self

    def set_language(self, value):
        language = str(value or "system").strip().lower()
        self["language"] = language if language in LANGUAGES else "system"
        return self.save()
