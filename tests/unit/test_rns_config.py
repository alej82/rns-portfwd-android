import os
import stat

from portfwd_android import rns_config


def test_managed_config_has_ports_and_private_mode(tmp_path):
    path = rns_config.ensure_config(str(tmp_path), shared_port=1234, control_port=1235)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert text.startswith(rns_config.MARKER)
    assert "shared_instance_port = 1234" in text
    assert "instance_control_port = 1235" in text
    assert "[interfaces]\n" in text
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_existing_foreign_config_is_preserved_unless_forced(tmp_path):
    path = rns_config.config_path(str(tmp_path))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("[reticulum]\n  share_instance = Yes\n")
    rns_config.ensure_config(str(tmp_path), shared_port=1234, control_port=1235)
    with open(path, encoding="utf-8") as handle:
        assert "shared_instance_port" not in handle.read()
    rns_config.ensure_config(str(tmp_path), shared_port=1234, control_port=1235, force=True)
    with open(path, encoding="utf-8") as handle:
        assert "shared_instance_port = 1234" in handle.read()
