import os


def is_android():
    return bool(os.environ.get("ANDROID_PRIVATE") or os.environ.get("ANDROID_APP_PATH"))


def android_home():
    for key in ("ANDROID_PRIVATE", "ANDROID_APP_PATH"):
        value = os.environ.get(key)
        if value:
            return os.path.abspath(value)
    raise RuntimeError("not running on Android")


def app_home():
    override = os.environ.get("PORTFWD_HOME")
    if override:
        return os.path.abspath(override)
    if is_android():
        return android_home()
    return os.path.join(os.path.expanduser("~"), ".portfwd-android")


def prepare_environment():
    home = app_home()
    os.makedirs(home, exist_ok=True)
    if is_android():
        os.environ["HOME"] = home
    os.environ.setdefault("PORTFWD_CONFIG_DIR", os.path.join(home, "portfwd"))
    os.environ.setdefault("PORTFWD_RNS_DIR", os.path.join(home, "rns"))
    return home


def config_dir():
    return os.environ.get("PORTFWD_CONFIG_DIR") or os.path.join(app_home(), "portfwd")


def rns_dir():
    return os.environ.get("PORTFWD_RNS_DIR") or os.path.join(app_home(), "rns")


def profiles_path():
    return os.path.join(config_dir(), "profiles.json")


def settings_path():
    return os.path.join(config_dir(), "settings.json")


def log_dir():
    return os.path.join(app_home(), "logs")
