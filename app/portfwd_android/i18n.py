"""Translation lookup for RNS Portfwd."""

import json
import locale
import os
import string
import threading

from .paths import is_android

I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "i18n")
MASTER_FILE = "translations.json"
FALLBACK_LANGUAGE = "en"
DEFAULT_LANGUAGE = "es"

_lock = threading.RLock()
_master = None
_catalogs = {}
_current_language = DEFAULT_LANGUAGE
_warned = set()


def _warn(message):
    if message in _warned:
        return
    _warned.add(message)
    print(f"PORTFWD_I18N {message}", flush=True)


def _load_master():
    global _master
    with _lock:
        if _master is None:
            path = os.path.join(I18N_DIR, MASTER_FILE)
            try:
                with open(path, encoding="utf-8") as catalog_file:
                    data = json.load(catalog_file)
            except (OSError, ValueError) as error:
                _warn(f"cannot load {path}: {error}")
                data = {"meta": {"fallback": FALLBACK_LANGUAGE, "languages": {}}, "strings": {}}
            if not isinstance(data, dict):
                data = {"meta": {"fallback": FALLBACK_LANGUAGE, "languages": {}}, "strings": {}}
            data.setdefault("meta", {})
            data.setdefault("strings", {})
            _master = data
        return _master


def _language_file(language):
    return os.path.join(I18N_DIR, f"{language}.json")


def _load_catalog(language):
    with _lock:
        if language in _catalogs:
            return _catalogs[language]
        catalog = {}
        if language == "en":
            for key, entry in (_load_master().get("strings") or {}).items():
                if isinstance(entry, dict) and isinstance(entry.get("en"), str):
                    catalog[key] = entry["en"]
        else:
            path = _language_file(language)
            try:
                with open(path, encoding="utf-8") as catalog_file:
                    loaded = json.load(catalog_file)
            except (OSError, ValueError) as error:
                if language != FALLBACK_LANGUAGE:
                    _warn(f"cannot load {path}: {error}")
                loaded = {}
            if isinstance(loaded, dict):
                for key, value in loaded.items():
                    if isinstance(value, str):
                        catalog[key] = value
        _catalogs[language] = catalog
        return catalog


def _supported_languages():
    meta = _load_master().get("meta") or {}
    languages = meta.get("languages") or {}
    if not isinstance(languages, dict):
        return {}
    return {str(code): str(name) for code, name in languages.items()}


def _normalise_language(value):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.split(":")[0]
    for separator in ("_", "-", "."):
        text = text.split(separator)[0]
    return text


def _system_language():
    supported = _supported_languages()
    candidates = []
    if is_android():
        try:
            from jnius import autoclass

            candidates.append(autoclass("java.util.Locale").getDefault().getLanguage())
        except Exception as error:
            _warn(f"android locale lookup failed: {type(error).__name__}: {error}")
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable)
        if value:
            candidates.append(value)
    try:
        candidates.append(locale.getlocale()[0] or "")
    except (TypeError, ValueError):
        pass
    for candidate in candidates:
        code = _normalise_language(candidate)
        if code in supported:
            return code
    return FALLBACK_LANGUAGE


def resolve_language(value):
    text = str(value or "system").strip().lower()
    if text in ("", "system"):
        return _system_language()
    code = _normalise_language(text)
    if code in _supported_languages():
        return code
    return _system_language()


def set_language(value):
    global _current_language
    resolved = resolve_language(value)
    with _lock:
        _current_language = resolved
    _load_catalog(resolved)
    return resolved


def current_language():
    return _current_language


def available_languages():
    return _supported_languages()


def configure(value):
    return set_language(value)


class _SafeValues(dict):
    def __missing__(self, key):
        _warn(f"missing placeholder '{key}'")
        return "{" + key + "}"


def _format(text, params, key):
    if not params:
        return text
    try:
        return string.Formatter().vformat(text, (), _SafeValues(params))
    except (KeyError, IndexError, ValueError) as error:
        _warn(f"cannot format '{key}': {type(error).__name__}: {error}")
        return text


def t(key, lang=None, **params):
    master = _load_master()
    strings = master.get("strings") or {}
    requested = _normalise_language(lang) if lang else _current_language
    fallback = _normalise_language((master.get("meta") or {}).get("fallback") or FALLBACK_LANGUAGE)
    text = None
    if requested:
        text = _load_catalog(requested).get(key)
    if text is None and fallback and fallback != requested:
        text = _load_catalog(fallback).get(key)
    if text is None:
        entry = strings.get(key)
        if isinstance(entry, dict):
            text = entry.get("en")
    if not isinstance(text, str):
        _warn(f"missing key '{key}'")
        return key
    return _format(text, params, key)


def placeholder_names(text):
    names = set()
    try:
        for _literal, field_name, _spec, _conversion in string.Formatter().parse(text):
            if field_name:
                names.add(field_name.split(".", 1)[0].split("[", 1)[0])
    except ValueError:
        pass
    return names


def master_strings():
    return dict(_load_master().get("strings") or {})


def validate_catalog():
    errors = []
    strings = master_strings()
    english = {}
    for key, entry in strings.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("en"), str):
            errors.append(f"{key}: missing English text")
            continue
        english[key] = entry["en"]
        if not entry.get("context") or not entry.get("file"):
            errors.append(f"{key}: missing context/file")
    for language in _supported_languages():
        if language == "en":
            continue
        catalog = _load_catalog(language)
        for key, english_text in english.items():
            translated = catalog.get(key)
            if not isinstance(translated, str):
                errors.append(f"{key}: missing {language}")
                continue
            expected = placeholder_names(english_text)
            actual = placeholder_names(translated)
            if expected != actual:
                errors.append(
                    f"{key}: {language} placeholders {sorted(actual)} != {sorted(expected)}"
                )
    return errors


def reset_for_tests(language=DEFAULT_LANGUAGE):
    global _master, _catalogs, _current_language, _warned
    with _lock:
        _master = None
        _catalogs = {}
        _current_language = language
        _warned = set()
    _load_master()
    _load_catalog(language)
