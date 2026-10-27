import json
import os
import stat

from portfwd_android.profiles import ProfileStore


def _forwards():
    return [{"name": "ssh", "local_port": 2222, "password": "s3cret"}]


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
    assert reloaded.get(entry["id"])["forwards"] == _forwards()
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
