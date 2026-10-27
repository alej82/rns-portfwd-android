import pytest
from portfwd_android.validation import (
    ValidationError,
    validate_config,
    validate_forward,
    validate_forwards,
    validate_hash,
    validate_port,
)


def test_valid_config_is_normalised():
    server_hash, forwards, list_password = validate_config(
        "A" * 32,
        [{"name": "ssh", "local_port": "2222", "password": "secret"}],
        "listpass",
    )
    assert server_hash == "a" * 32
    assert forwards == [{"name": "ssh", "local_port": 2222, "password": "secret"}]
    assert list_password == "listpass"


@pytest.mark.parametrize("value", ["", "a" * 31, "g" * 32, "a" * 33, "zz" + "a" * 30])
def test_invalid_hash(value):
    with pytest.raises(ValidationError) as error:
        validate_hash(value)
    assert error.value.code == "config"
    assert error.value.field == "hash"


def test_ephemeral_zero_and_range_edges():
    assert validate_port(0) == 0
    assert validate_port(1024) == 1024
    assert validate_port(65535) == 65535
    with pytest.raises(ValidationError) as low:
        validate_port(1023)
    assert low.value.code == "low_port"
    with pytest.raises(ValidationError):
        validate_port(65536)
    with pytest.raises(ValidationError):
        validate_port("nope")


@pytest.mark.parametrize("name", ["", "with space", "a/b", "ñ", "x" * 256])
def test_invalid_names(name):
    with pytest.raises(ValidationError) as error:
        validate_forward({"name": name, "local_port": 2222, "password": "x"})
    assert error.value.field == "name"


def test_password_validation():
    with pytest.raises(ValidationError):
        validate_forward({"name": "ssh", "local_port": 2222, "password": ""})
    with pytest.raises(ValidationError):
        validate_forward({"name": "ssh", "local_port": 2222, "password": "x" * 4097})


def test_duplicate_ports_are_rejected_but_zero_is_repeatable():
    with pytest.raises(ValidationError) as error:
        validate_forwards(
            [
                {"name": "a", "local_port": 2222, "password": "x"},
                {"name": "b", "local_port": 2222, "password": "y"},
            ]
        )
    assert error.value.code == "port_in_use"
    value = validate_forwards(
        [
            {"name": "a", "local_port": 0, "password": "x"},
            {"name": "b", "local_port": 0, "password": "y"},
        ]
    )
    assert [forward["local_port"] for forward in value] == [0, 0]


def test_empty_forwards_are_rejected():
    with pytest.raises(ValidationError):
        validate_forwards([])
