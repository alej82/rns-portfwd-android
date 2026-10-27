import pytest
from portfwd_android import i18n
from portfwd_android.messages import error_text, state_text


@pytest.fixture(autouse=True)
def reset():
    i18n.reset_for_tests("es")
    yield
    i18n.reset_for_tests("es")


def test_known_codes_are_translated():
    assert error_text("auth") == "La credencial del servicio fue rechazada."
    assert error_text("shared_instance").startswith("Inicia Columba")
    assert error_text("list_disabled").startswith("El listado está deshabilitado")


def test_field_errors_are_specific():
    assert error_text("config", field="hash") == "El hash del servidor debe tener 32 caracteres hexadecimales."
    assert error_text("config", field="port") == "El puerto local debe ser 0 o estar entre 1024 y 65535."


def test_port_in_use_uses_parameter():
    assert "2222" in error_text("port_in_use", port=2222)


def test_unknown_code_keeps_diagnostic_code():
    text = error_text("something-new")
    assert "something-new" in text


def test_state_text_falls_back_to_raw_value():
    assert state_text("listening") == "Escuchando"
    assert state_text("custom") == "custom"
