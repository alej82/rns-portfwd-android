import pytest
from portfwd_android.validation import (
    MAX_STAMP_COST,
    ValidationError,
    duplicate_forward_names,
    validate_config,
    validate_forward,
    validate_forwards,
    validate_hash,
    validate_password,
    validate_port,
    validate_stamp_config,
    validate_stamp_mode,
)
from rns_portfwd.constants import MAX_LINK_STAMP_COST


def test_valid_config_is_normalised():
    server_hash, forwards, list_password = validate_config(
        "A" * 32,
        [{"name": "ssh", "local_port": "2222", "password": "secret"}],
        "listpass",
    )
    assert server_hash == "a" * 32
    assert forwards == [
        {"name": "ssh", "local_port": 2222, "protocol": "tcp", "password": "secret"}
    ]
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
        validate_forward({"name": "ssh", "local_port": 2222, "password": "x" * 4097})
    with pytest.raises(ValidationError):
        validate_password(None, "password")
    with pytest.raises(ValidationError):
        validate_password("", "password")


def test_forward_protocol_defaults_to_tcp_and_accepts_udp():
    tcp = validate_forward({"name": "ssh", "local_port": 2222, "password": "x"})
    udp = validate_forward(
        {"name": "dns", "local_port": 5353, "password": "x", "protocol": "UDP"}
    )
    assert tcp["protocol"] == "tcp"
    assert udp["protocol"] == "udp"
    with pytest.raises(ValidationError) as error:
        validate_forward(
            {"name": "dns", "local_port": 5353, "password": "x", "protocol": "sctp"}
        )
    assert error.value.field == "protocol"


def test_missing_or_empty_password_means_public():
    for value in (None, ""):
        forward = validate_forward(
            {"name": "web", "local_port": 8080, "protocol": "udp", "password": value}
        )
        assert forward["password"] is None
        assert forward["protocol"] == "udp"


def test_tcp_and_udp_may_share_a_port_but_not_within_a_protocol():
    forwards = validate_forwards(
        [
            {"name": "a", "local_port": 8080, "password": None},
            {"name": "b", "local_port": 8080, "protocol": "udp", "password": "x"},
        ]
    )
    assert [forward["protocol"] for forward in forwards] == ["tcp", "udp"]
    with pytest.raises(ValidationError) as error:
        validate_forwards(
            [
                {"name": "a", "local_port": 8080, "protocol": "udp", "password": "x"},
                {"name": "b", "local_port": 8080, "protocol": "udp", "password": "y"},
            ]
        )
    assert error.value.code == "port_in_use"


def test_public_forward_reaches_the_vendor_dataclass_as_none():
    from portfwd_android.validation import to_vendor_forwards

    resolved = validate_forwards([{"name": "web", "local_port": 8080, "password": None}])
    (vendor_forward,) = to_vendor_forwards(resolved)
    assert vendor_forward.password is None
    assert vendor_forward.protocol == "tcp"
    assert vendor_forward.local_port == 8080


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


def test_duplicate_forward_names_are_reported():
    forwards = [
        {"name": "ssh", "local_port": 2222, "password": "x"},
        {"name": "ssh", "local_port": 5900, "password": "x"},
        {"name": "vnc", "local_port": 5901, "password": "x"},
    ]
    assert duplicate_forward_names(forwards) == ["ssh"]
    assert duplicate_forward_names([{"name": "ssh"}, {"name": "vnc"}]) == []
    assert duplicate_forward_names([{"name": ""}, {"name": ""}]) == []


@pytest.mark.parametrize(
    "value, expected",
    [
        ("auto", "auto"),
        ("AUTO", "auto"),
        ("  off  ", "off"),
        ("Cost", "cost"),
        (None, "auto"),
    ],
)
def test_stamp_mode_is_normalised(value, expected):
    assert validate_stamp_mode(value) == expected


@pytest.mark.parametrize("value", ["", "fast", "custom", "0", "a u t o"])
def test_invalid_stamp_mode_is_a_config_error(value):
    with pytest.raises(ValidationError) as error:
        validate_stamp_mode(value)
    assert error.value.code == "config"
    assert error.value.field == "stamp_mode"


@pytest.mark.parametrize("mode", ["auto", "off", "AUTO", " OFF "])
def test_non_cost_modes_ignore_the_cost(mode):
    assert validate_stamp_config(mode, 12) == (validate_stamp_mode(mode), None)
    assert validate_stamp_config(mode, None) == (validate_stamp_mode(mode), None)


@pytest.mark.parametrize(
    "cost, expected",
    [
        (12, 12.0),
        (12.5, 12.5),
        ("12.5", 12.5),
        (0, 0.0),
        ("0", 0.0),
        (16, 16.0),
        (16.0, 16.0),
        ("16", 16.0),
    ],
)
def test_custom_stamp_cost_accepts_the_valid_range(cost, expected):
    assert validate_stamp_config("cost", cost) == ("cost", expected)


@pytest.mark.parametrize(
    "cost",
    [
        None,
        True,
        False,
        "nope",
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        16.1,
        40,
        100,
    ],
)
def test_custom_stamp_cost_rejects_invalid_values(cost):
    with pytest.raises(ValidationError) as error:
        validate_stamp_config("cost", cost)
    assert error.value.code == "config"
    assert error.value.field == "stamp_cost"


def test_custom_stamp_cost_is_capped_below_the_vendor_bound():
    # Cost N needs about 2**N attempts, so the vendor bound of 40 would mean
    # days of work on a phone; the app stops offering it.
    assert validate_stamp_config("cost", MAX_STAMP_COST) == ("cost", MAX_STAMP_COST)
    with pytest.raises(ValidationError) as error:
        validate_stamp_config("cost", MAX_STAMP_COST + 1)
    assert "between 0 and" in str(error.value)
    assert MAX_STAMP_COST < MAX_LINK_STAMP_COST


def test_stamp_config_rejects_an_unknown_mode():
    with pytest.raises(ValidationError) as error:
        validate_stamp_config("sometimes", 5)
    assert error.value.field == "stamp_mode"


def test_stamp_config_requires_a_cost_only_for_cost_mode():
    with pytest.raises(ValidationError) as error:
        validate_stamp_config("cost", None)
    assert error.value.field == "stamp_cost"
    assert validate_stamp_config("auto", None) == ("auto", None)

