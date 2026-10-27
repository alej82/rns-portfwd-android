
import pytest
from portfwd_android import i18n


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "es")
    i18n.reset_for_tests("es")
    yield
    i18n.reset_for_tests("es")


def test_catalog_is_complete_and_placeholders_match():
    assert i18n.validate_catalog() == []


def test_translation_and_placeholders():
    text = i18n.t("session.notification.listening", count=3)
    assert "3" in text
    assert i18n.t("session.notification.listening", lang="en", count=3) == "Forwarding 3 port(s)"


def test_unsupported_language_falls_back_to_english(monkeypatch):
    monkeypatch.setenv("LANGUAGE", "fr")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "fr_FR.UTF-8")
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("fr_FR", "UTF-8"))
    assert i18n.resolve_language("fr") == "en"
    assert i18n.t("session.stop", lang="fr") == "Stop"


def test_explicit_language_does_not_change_preference():
    assert i18n.current_language() == "es"
    assert i18n.t("session.stop", lang="en") == "Stop"
    assert i18n.current_language() == "es"
    assert i18n.t("session.stop") == "Detener"


def test_server_data_is_not_translated():
    text = i18n.t(
        "discovery.metadata",
        name="db-prod",
        protocol="tcp",
        auth="yes",
    )
    assert "db-prod" in text


def test_missing_key_returns_key():
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_placeholder_names():
    assert i18n.placeholder_names("a {name} b {port}") == {"name", "port"}
