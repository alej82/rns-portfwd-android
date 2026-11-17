import pytest
from portfwd_android import i18n, messages
from portfwd_android.messages import error_text, state_text


@pytest.fixture(autouse=True)
def reset():
    i18n.reset_for_tests("es")
    yield
    i18n.reset_for_tests("es")


def test_known_codes_are_translated():
    auth = error_text("auth")
    assert auth.startswith("El servidor rechazó la credencial")
    assert "Identificar este dispositivo" in auth
    assert error_text("shared_instance").startswith("Inicia Columba")
    assert error_text("list_disabled").startswith("El listado está deshabilitado")


def test_field_errors_are_specific():
    assert error_text("config", field="hash") == "El hash del servidor debe tener 32 caracteres hexadecimales."
    assert error_text("config", field="port") == "El puerto local debe ser 0 o estar entre 1024 y 65535."


def test_protocol_field_error_uses_its_own_key(monkeypatch):
    calls = []

    def fake_t(key, lang=None, **params):
        calls.append((key, params))
        return "ok"

    monkeypatch.setattr(messages, "t", fake_t)
    error_text("config", field="protocol")
    assert calls == [("error.protocol", {})]


def test_protocol_mismatch_forwards_parameters(monkeypatch):
    calls = []

    def fake_t(key, lang=None, **params):
        calls.append((key, params))
        return "ok"

    monkeypatch.setattr(messages, "t", fake_t)
    error_text("protocol_mismatch", service="x", protocol="udp", expected="tcp")
    assert calls == [
        ("error.protocol_mismatch", {"service": "x", "protocol": "udp", "expected": "tcp"})
    ]


def test_protocol_mismatch_ignores_unknown_parameters(monkeypatch):
    def fake_t(key, lang=None, **params):
        unexpected = set(params) - {"service", "protocol", "expected"}
        assert not unexpected
        return f"{params['service']}/{params['protocol']}/{params['expected']}"

    monkeypatch.setattr(messages, "t", fake_t)
    text = error_text(
        "protocol_mismatch", service="x", protocol="udp", expected="tcp", extra=1
    )
    assert text == "x/udp/tcp"


def test_protocol_mismatch_renders_every_value():
    text = error_text("protocol_mismatch", service="x", protocol="udp", expected="tcp")
    assert "x" in text
    assert "udp" in text
    assert "tcp" in text


def test_protocol_mismatch_accepts_the_published_field_names():
    text = error_text("protocol_mismatch", service="x", actual="udp", expected="tcp")
    assert "x" in text
    assert "udp" in text
    assert "tcp" in text


def test_protocol_field_error_renders():
    assert error_text("config", field="protocol") == "Elige TCP o UDP."


def test_port_in_use_uses_parameter():
    assert "2222" in error_text("port_in_use", port=2222)


def test_stamp_and_identity_errors_are_translated():
    assert error_text("stamp_unsupported").startswith("Este servidor exige stamps de enlace")
    assert error_text("identity_error") == "No se pudo abrir la identidad del dispositivo."


def test_stamp_config_fields_have_their_own_messages():
    assert error_text("config", field="stamp_mode") == "Modo de stamping no válido."
    assert error_text("config", field="stamp_cost") == (
        "El coste de stamping debe ser un número entre 0 y 16."
    )


def test_stamp_config_fields_use_their_own_keys(monkeypatch):
    calls = []

    def fake_t(key, lang=None, **params):
        calls.append((key, params))
        return "ok"

    monkeypatch.setattr(messages, "t", fake_t)
    error_text("config", field="stamp_mode")
    error_text("config", field="stamp_cost")

    assert calls == [
        ("error.stamp_mode", {}),
        ("error.stamp_cost", {"max": "16"}),
    ]



def test_unknown_code_keeps_diagnostic_code():
    text = error_text("something-new")
    assert "something-new" in text


def test_state_text_falls_back_to_raw_value():
    assert state_text("listening") == "Escuchando"
    assert state_text("custom") == "custom"


def test_state_text_reports_the_stamp_cost_while_generating():
    assert state_text("stamping", stamp_cost=8) == "Generando stamp del enlace (coste 8)…"
    assert state_text("stamping", stamp_cost=12.5) == "Generando stamp del enlace (coste 12.5)…"
    assert state_text("stamping") == "Generando stamp del enlace (coste ?)…"


def test_state_text_accepts_a_session_snapshot_splat():
    snapshot = {"state": "stamping", "stamp_cost": 8.0, "retry_in": None}

    assert state_text(snapshot["state"], **snapshot) == (
        "Generando stamp del enlace (coste 8)…"
    )
