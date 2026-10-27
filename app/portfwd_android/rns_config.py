import os

MARKER = "# portfwd-android managed rns config v1"
SHARED_INSTANCE_PORT = 37428
INSTANCE_CONTROL_PORT = 37429


def template(shared_port=SHARED_INSTANCE_PORT, control_port=INSTANCE_CONTROL_PORT):
    return f"""{MARKER}
[reticulum]
  enable_transport = No
  share_instance = Yes
  shared_instance_type = tcp
  shared_instance_port = {int(shared_port)}
  instance_control_port = {int(control_port)}
  instance_name = default
  panic_on_interface_error = No

[logging]
  loglevel = 4

[interfaces]
"""


def config_path(config_dir):
    return os.path.join(config_dir, "config")


def ensure_config(
    config_dir,
    shared_port=SHARED_INSTANCE_PORT,
    control_port=INSTANCE_CONTROL_PORT,
    force=False,
):
    os.makedirs(config_dir, exist_ok=True)
    path = config_path(config_dir)
    wanted = template(shared_port, control_port)
    write = True
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                current = handle.read()
            write = current != wanted and current.startswith(MARKER)
            if not current.startswith(MARKER):
                write = False
        except OSError:
            write = True
    if force and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                current = handle.read()
            write = not current.startswith(MARKER) or current != wanted
        except OSError:
            write = True
    if write:
        temporary = path + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(wanted)
        os.replace(temporary, path)
    os.chmod(path, 0o600)
    return path
