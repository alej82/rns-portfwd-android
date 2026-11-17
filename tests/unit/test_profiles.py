import json
import os
import stat

from portfwd_android.profiles import ProfileStore


def _forwards():
    return [{"name": "ssh", "local_port": 2222, "password": "s3cret"}]


def _stored(forwards):
    return [
        {
            "name": forward["name"],
            "local_port": forward["local_port"],
            "protocol": forward.get("protocol") or "tcp",
            "password": forward.get("password"),
        }
        for forward in forwards
    ]


def test_upsert_orders_by_recent_and_deduplicates(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(str(path))
    first = store.upsert("a" * 32, "One", _forwards(), "list")
    second = store.upsert("b" * 32, "Two", _forwards())
    assert [entry["id"] for entry in store.ordered()] == [second["id"], first["id"]]

    updated = store.upsert("a" * 32, "One updated", _forwards(), "new-list")
    assert updated["id"] == first["id"]
    assert updated["label"] == "One updated"
    assert len(store.ordered()) == 2
    assert store.ordered()[0]["id"] == first["id"]


def test_file_is_private_and_reloads(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(str(path))
    entry = store.upsert("a" * 32, "", _forwards(), "list")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    assert json.loads(path.read_text())[0]["hash"] == "a" * 32

    reloaded = ProfileStore(str(path))
    assert reloaded.get(entry["id"])["forwards"] == _stored(_forwards())
    assert reloaded.get(entry["id"])["label"] == "a" * 8


def test_delete_and_touch(tmp_path):
    store = ProfileStore(str(tmp_path / "profiles.json"))
    first = store.upsert("a" * 32, "One", _forwards())
    second = store.upsert("b" * 32, "Two", _forwards())
    assert store.delete(first["id"]) is True
    assert store.get(first["id"]) is None
    assert store.delete("missing") is False
    before = store.get(second["id"])["last_used"]
    touched = store.touch(second["id"])
    assert touched["last_used"] >= before


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("not json")
    assert ProfileStore(str(path)).ordered() == []


def test_legacy_profile_without_protocol_loads_as_tcp(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "abc",
                    "hash": "a" * 32,
                    "label": "old",
                    "forwards": [{"name": "ssh", "local_port": 2222, "password": "s3cret"}],
                    "list_password": None,
                    "last_used": 1.0,
                }
            ]
        )
    )
    entry = ProfileStore(str(path)).get("abc")
    assert entry["forwards"] == _stored(_forwards())


def test_public_udp_forward_round_trips(tmp_path):
    path = tmp_path / "profiles.json"
    forwards = [
        {"name": "dns", "local_port": 5353, "protocol": "udp", "password": None},
        {"name": "ssh", "local_port": 2222, "protocol": "tcp", "password": "s3cret"},
    ]
    store = ProfileStore(str(path))
    entry = store.upsert("b" * 32, "mixed", forwards)
    stored = ProfileStore(str(path)).get(entry["id"])["forwards"]
    assert stored == _stored(forwards)
    assert stored[0]["password"] is None
    assert stored[0]["protocol"] == "udp"
    raw = json.loads(path.read_text())[0]["forwards"][0]
    assert raw["password"] is None
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_empty_password_in_legacy_file_is_loaded_as_public(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "abc",
                    "hash": "a" * 32,
                    "forwards": [
                        {"name": "web", "local_port": 8080, "password": ""},
                    ],
                }
            ]
        )
    )
    entry = ProfileStore(str(path)).get("abc")
    assert entry["forwards"][0]["password"] is None


def test_legacy_profile_defaults_to_anonymous_and_automatic(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "abc",
                    "hash": "a" * 32,
                    "label": "old",
                    "forwards": [{"name": "ssh", "local_port": 2222}],
                }
            ]
        )
    )
    entry = ProfileStore(str(path)).get("abc")
    assert entry["identify"] is False
    assert entry["stamp_mode"] == "auto"
    assert entry["stamp_cost"] is None


def test_identity_and_stamp_policy_round_trip(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(str(path))
    entry = store.upsert(
        "a" * 32,
        "mail",
        _forwards(),
        identify=True,
        stamp_mode="cost",
        stamp_cost=12.5,
    )

    reloaded = ProfileStore(str(path)).get(entry["id"])
    assert reloaded["identify"] is True
    assert reloaded["stamp_mode"] == "cost"
    assert reloaded["stamp_cost"] == 12.5
    raw = json.loads(path.read_text())[0]
    assert raw["identify"] is True
    assert raw["stamp_mode"] == "cost"
    assert raw["stamp_cost"] == 12.5

    auto = store.upsert("b" * 32, "auto", _forwards(), identify=False)
    assert auto["identify"] is False
    assert auto["stamp_mode"] == "auto"
    assert auto["stamp_cost"] is None


def test_upsert_keeps_positional_compatibility(tmp_path):
    store = ProfileStore(str(tmp_path / "profiles.json"))
    entry = store.upsert("a" * 32, "One", _forwards(), "list", 42.0)
    assert entry["list_password"] == "list"
    assert entry["last_used"] == 42.0
    assert entry["identify"] is False
    assert entry["stamp_mode"] == "auto"
    assert entry["stamp_cost"] is None

    identified = store.upsert("b" * 32, "Two", _forwards(), None, None, True, "cost", 7)
    assert identified["identify"] is True
    assert identified["stamp_mode"] == "cost"
    assert identified["stamp_cost"] == 7.0


def test_upsert_normalises_an_invalid_stamp_mode(tmp_path):
    store = ProfileStore(str(tmp_path / "profiles.json"))

    entry = store.upsert("a" * 32, "One", _forwards(), stamp_mode="turbo", stamp_cost=5)
    assert entry["stamp_mode"] == "auto"
    assert entry["stamp_cost"] is None

    over_range = store.upsert(
        "b" * 32, "Two", _forwards(), stamp_mode="cost", stamp_cost=55
    )
    assert over_range["stamp_mode"] == "auto"
    assert over_range["stamp_cost"] is None

    # The app caps a custom cost far below the vendor bound of 40: at cost 40
    # the stamp would take days, so a stored value of 17 or 40 degrades to auto
    # instead of resurrecting the hang.
    for rejected in (17, 40):
        entry = store.upsert(
            "c" * 32, "Three", _forwards(), stamp_mode="cost", stamp_cost=rejected
        )
        assert entry["stamp_mode"] == "auto"
        assert entry["stamp_cost"] is None


def test_stored_invalid_stamp_config_is_normalised_to_auto(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "hash": "a" * 32, "stamp_mode": "turbo", "stamp_cost": 3},
                {"id": "b", "hash": "b" * 32, "stamp_mode": "cost"},
                {"id": "d", "hash": "d" * 32, "stamp_mode": "cost", "stamp_cost": 40},
                {"id": "c", "hash": "c" * 32, "stamp_mode": "cost", "stamp_cost": 3},
            ]
        )
    )
    store = ProfileStore(str(path))

    first, second, third, fourth = (
        store.get(profile_id) for profile_id in ("a", "b", "c", "d")
    )
    assert (first["stamp_mode"], first["stamp_cost"]) == ("auto", None)
    assert (second["stamp_mode"], second["stamp_cost"]) == ("auto", None)
    assert (third["stamp_mode"], third["stamp_cost"]) == ("cost", 3.0)
    assert (fourth["stamp_mode"], fourth["stamp_cost"]) == ("auto", None)


def test_identify_requires_a_literal_true(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {"id": "a", "hash": "a" * 32, "identify": "yes"},
                {"id": "b", "hash": "b" * 32, "identify": 1},
                {"id": "c", "hash": "c" * 32, "identify": True},
            ]
        )
    )
    store = ProfileStore(str(path))
    assert store.get("a")["identify"] is False
    assert store.get("b")["identify"] is False
    assert store.get("c")["identify"] is True

