import os
import stat

import pytest
from portfwd_android import identity


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFWD_CONFIG_DIR", str(tmp_path / "portfwd"))
    identity.reset_cache()
    yield
    identity.reset_cache()


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_identity_is_created_private_inside_the_config_dir(tmp_path):
    path = tmp_path / "portfwd" / "identity.id"
    created = identity.ensure_identity()

    assert path.is_file()
    assert _mode(path) == 0o600
    assert created.hash.hex() == identity.identity_hash()
    assert os.path.realpath(identity._resolve(None)) == os.path.realpath(str(path))


def test_identity_hash_is_32_hex_characters():
    value = identity.identity_hash()

    assert len(value) == 32
    assert value == value.lower()
    assert set(value) <= set("0123456789abcdef")


def test_repeated_calls_reuse_the_same_identity(tmp_path):
    first = identity.ensure_identity()
    second = identity.ensure_identity()

    assert second is first
    assert identity.identity_hash() == identity.identity_hash()


def test_identity_survives_a_cache_reset():
    before = identity.identity_hash()
    identity.reset_cache()
    after = identity.identity_hash()

    assert after == before
    # Only one file was created; the cache reset reloaded it from disk.
    files = os.listdir(os.environ["PORTFWD_CONFIG_DIR"])
    assert files == ["identity.id"]


def test_load_identity_returns_none_when_missing(tmp_path):
    assert identity.load_identity(str(tmp_path / "absent.id")) is None
    # A missing directory must not raise either.
    assert identity.load_identity(str(tmp_path / "nope" / "deep" / "identity.id")) is None


def test_load_identity_returns_none_for_a_corrupt_file(tmp_path):
    path = tmp_path / "identity.id"
    path.write_bytes(b"not a reticulum identity")

    assert identity.load_identity(str(path)) is None


def test_ensure_identity_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "a" / "b" / "identity.id"

    created = identity.ensure_identity(str(target))

    assert target.is_file()
    assert _mode(target) == 0o600
    assert created.hash.hex() == identity.identity_hash(str(target))


def test_ensure_identity_refuses_to_replace_a_corrupt_file(tmp_path):
    path = tmp_path / "identity.id"
    path.write_bytes(b"broken")

    with pytest.raises(identity.IdentityError):
        identity.ensure_identity(str(path))

    assert path.read_bytes() == b"broken"


def test_failed_write_raises_identity_error_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    class UnwritableIdentity:
        def to_file(self, _path):
            return False

    monkeypatch.setattr(identity.RNS, "Identity", lambda: UnwritableIdentity())
    target = tmp_path / "identity.id"

    with pytest.raises(identity.IdentityError):
        identity.ensure_identity(str(target))

    assert not target.exists()
    assert os.listdir(tmp_path) == []


def test_creation_error_is_wrapped_in_identity_error(tmp_path, monkeypatch):
    def boom():
        raise OSError("no key material")

    monkeypatch.setattr(identity.RNS, "Identity", boom)

    with pytest.raises(identity.IdentityError):
        identity.ensure_identity(str(tmp_path / "identity.id"))


def test_identity_error_is_a_runtime_error():
    assert issubclass(identity.IdentityError, RuntimeError)
