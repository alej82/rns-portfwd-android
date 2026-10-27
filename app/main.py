import collections

from portfwd_android.paths import prepare_environment

prepare_environment()

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from portfwd_android import background
from portfwd_android.engine import Engine, EngineError
from portfwd_android.i18n import (
    configure,
    current_language,
    resolve_language,
    t,
)
from portfwd_android.messages import error_text as _error_text
from portfwd_android.messages import state_text as _state_text
from portfwd_android.paths import is_android
from portfwd_android.profiles import ProfileStore
from portfwd_android.settings import Settings
from portfwd_android.validation import (
    ValidationError,
    validate_config,
    validate_name,
    validate_password,
    validate_port,
)

APP_VERSION = "0.1.0"
LOG_LIMIT = 200


def _title(text):
    return Label(text=text, size_hint_y=None, height=dp(32), bold=True, color=(0.92, 0.95, 1, 1))


def _label(text="", **kwargs):
    kwargs.setdefault("color", (0.85, 0.88, 0.92, 1))
    return Label(text=text, **kwargs)


def _input(hint, password=False):
    return TextInput(
        hint_text=hint,
        multiline=False,
        password=password,
        size_hint_y=None,
        height=dp(44),
        background_color=(0.14, 0.16, 0.2, 1),
        foreground_color=(0.95, 0.96, 1, 1),
        cursor_color=(0.5, 0.8, 1, 1),
        padding=[dp(8), dp(10)],
    )


def _button(text, callback, color=(0.18, 0.4, 0.62, 1), font_size=None):
    button = Button(
        text=text,
        background_normal="",
        background_color=color,
        size_hint_y=None,
        height=dp(46),
    )
    if font_size is not None:
        button.font_size = font_size
    button.bind(on_release=lambda *_: callback())
    return button


def _switch_screen(manager, name):
    target = manager.get_screen(name)
    if manager.current == name:
        return
    target.pos = manager.pos
    target.size = manager.size
    manager.current = name
    manager.transition.stop()


class ForwardEditor(Popup):
    def __init__(self, app, forwards, index, on_save, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.forwards = forwards
        self.index = index
        self.on_save = on_save
        existing = forwards[index] if index is not None else None
        self.title = t("editor.title.edit") if existing else t("editor.title.add")
        self.size_hint = (0.92, 0.7)

        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12))
        root.add_widget(_label(t("editor.service"), size_hint_y=None, height=dp(22)))
        self.name = _input(t("editor.service"))
        root.add_widget(self.name)
        root.add_widget(_label(t("editor.port"), size_hint_y=None, height=dp(22)))
        self.port = _input(t("editor.port.hint"))
        root.add_widget(self.port)
        root.add_widget(_label(t("editor.password"), size_hint_y=None, height=dp(22)))
        self.password = _input(t("editor.password"), password=True)
        root.add_widget(self.password)
        self.status = _label("", size_hint_y=None, height=dp(40), halign="left", valign="middle")
        self.status.bind(size=lambda *_: setattr(self.status, "text_size", (self.status.width, None)))
        root.add_widget(self.status)
        root.add_widget(BoxLayout())
        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        buttons.add_widget(_button(t("common.save"), self.save))
        buttons.add_widget(_button(t("common.cancel"), self.dismiss, color=(0.22, 0.24, 0.3, 1)))
        root.add_widget(buttons)
        self.content = root

        if existing:
            self.name.text = existing["name"]
            self.port.text = str(existing["local_port"])
            self.password.text = existing["password"]

    def save(self):
        try:
            name = validate_name(self.name.text)
            port = validate_port(self.port.text)
            password = validate_password(self.password.text)
        except ValidationError as error:
            self.status.text = _error_text(error.code, error.field)
            return
        for position, forward in enumerate(self.forwards):
            if position == self.index:
                continue
            if port != 0 and forward["local_port"] == port:
                self.status.text = t("editor.error.duplicate")
                return
        value = {"name": name, "local_port": port, "password": password}
        self.on_save(self.index, value)
        self.dismiss()


class ServiceListPopup(Popup):
    def __init__(self, app, services, on_pick, **kwargs):
        super().__init__(**kwargs)
        self.title = t("discovery.list.title")
        self.size_hint = (0.92, 0.8)
        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        root.add_widget(_label(t("discovery.list.tap"), size_hint_y=None, height=dp(24)))
        scroll = ScrollView()
        box = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        box.bind(minimum_height=lambda *_: setattr(box, "height", box.minimum_height))
        if not services:
            box.add_widget(_label(t("discovery.list.empty"), size_hint_y=None, height=dp(40)))
        for service in services:
            name = str(service.get("name", ""))
            protocol = str(service.get("protocol", "tcp"))
            auth = service.get("auth")
            auth_text = "yes" if auth else "no"
            text = t("discovery.metadata", name=name, protocol=protocol, auth=auth_text)
            button = _button(text, lambda value=name: self._pick(value), color=(0.18, 0.25, 0.35, 1))
            button.size_hint_y = None
            button.height = dp(44)
            box.add_widget(button)
        scroll.add_widget(box)
        root.add_widget(scroll)
        root.add_widget(_button(t("common.close"), self.dismiss, color=(0.22, 0.24, 0.3, 1)))
        self.content = root
        self._on_pick = on_pick

    def _pick(self, name):
        self.dismiss()
        self._on_pick(name)


class ConnectScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.forwards = []
        self._profile_entries = []
        self._profile_labels = []
        self._updating_profile = False
        self._selected_profile = None
        self._status_kind = None

        scroll = ScrollView()
        self.scroll = scroll
        Clock.schedule_once(lambda *_: setattr(scroll, "scroll_y", 1), 0.05)
        root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(8), size_hint_y=None)
        root.bind(minimum_height=lambda *_: setattr(root, "height", root.minimum_height))
        scroll.add_widget(root)
        self.add_widget(scroll)

        root.add_widget(_title(t("connect.title")))
        root.add_widget(_label(t("connect.subtitle"), size_hint_y=None, height=dp(24)))
        root.add_widget(_label(t("connect.profile.label"), size_hint_y=None, height=dp(22)))
        self.profile = Spinner(
            text=t("connect.profile.empty"),
            values=[],
            size_hint_y=None,
            height=dp(44),
            background_normal="",
            background_color=(0.16, 0.2, 0.27, 1),
        )
        self.profile.bind(text=self._profile_selected)
        root.add_widget(self.profile)
        self.label_input = _input(t("connect.profile.label_hint"))
        root.add_widget(self.label_input)
        profile_actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        profile_actions.add_widget(_button(t("connect.profile.save"), self.save_profile))
        profile_actions.add_widget(
            _button(t("connect.profile.delete"), self.delete_profile, color=(0.5, 0.2, 0.25, 1))
        )
        profile_actions.add_widget(
            _button(t("connect.profile.new"), self.new_profile, color=(0.22, 0.24, 0.3, 1))
        )
        root.add_widget(profile_actions)

        root.add_widget(_label(t("connect.hash.label"), size_hint_y=None, height=dp(22)))
        self.server = _input(t("connect.hash.hint"))
        root.add_widget(self.server)
        root.add_widget(_label(t("connect.list_password.label"), size_hint_y=None, height=dp(22)))
        self.list_password = _input(t("connect.list_password.hint"), password=True)
        root.add_widget(self.list_password)

        root.add_widget(_label(t("connect.forwards.title"), size_hint_y=None, height=dp(24)))
        self.forward_box = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.forward_box.bind(minimum_height=lambda *_: setattr(self.forward_box, "height", self.forward_box.minimum_height))
        root.add_widget(self.forward_box)
        root.add_widget(_button(t("connect.forwards.add"), self.add_forward, color=(0.2, 0.35, 0.3, 1)))

        self.status = _label("", size_hint_y=None, height=dp(52), halign="left", valign="middle")
        self.status.bind(size=lambda *_: setattr(self.status, "text_size", (self.status.width, None)))
        root.add_widget(self.status)
        notice = _label(
            t("connect.password_notice"),
            size_hint_y=None,
            height=dp(48),
            halign="left",
            valign="middle",
            color=(1, 0.8, 0.4, 1),
        )
        notice.bind(size=lambda *_: setattr(notice, "text_size", (notice.width, None)))
        root.add_widget(notice)

        actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        self.connect_button = _button(t("connect.actions.connect"), self.connect)
        self.cancel_button = _button(
            t("connect.actions.cancel"), self.app.cancel_connection, color=(0.5, 0.25, 0.25, 1)
        )
        actions.add_widget(self.connect_button)
        actions.add_widget(self.cancel_button)
        root.add_widget(actions)
        actions2 = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        actions2.add_widget(_button(t("connect.actions.list"), self.list_services, color=(0.22, 0.3, 0.4, 1)))
        actions2.add_widget(_button(t("connect.actions.settings"), self.app.show_settings, color=(0.22, 0.24, 0.3, 1)))
        root.add_widget(actions2)

        self.refresh_forward_rows()
        self.autofill_last()

    def form_state(self):
        return {
            "label": self.label_input.text,
            "hash": self.server.text,
            "forwards": [dict(forward) for forward in self.forwards],
            "list_password": self.list_password.text,
            "profile_id": self._selected_profile,
        }

    def restore(self, state):
        if not state:
            return
        self.label_input.text = state.get("label", "")
        self.server.text = state.get("hash", "")
        self.forwards = [dict(forward) for forward in state.get("forwards", [])]
        self.list_password.text = state.get("list_password", "")
        self._selected_profile = state.get("profile_id")
        self.refresh_forward_rows()
        self.refresh_profiles(select_id=self._selected_profile)

    def set_status(self, text, kind=None):
        self.status.text = text
        self._status_kind = kind

    def refresh_profiles(self, select_id=None):
        self._updating_profile = True
        entries = self.app.profiles.ordered()
        labels = []
        for entry in entries:
            label = entry["label"]
            if labels.count(label):
                label = f"{label} · {entry['hash'][:8]}"
            labels.append(label)
        self._profile_entries = entries
        self._profile_labels = labels
        self.profile.values = labels
        if select_id is not None:
            for entry, label in zip(entries, labels, strict=False):
                if entry["id"] == select_id:
                    self.profile.text = label
                    self._selected_profile = entry["id"]
                    break
        elif self.profile.text not in labels:
            self.profile.text = labels[0] if labels else t("connect.profile.empty")
            if entries:
                self._selected_profile = entries[0]["id"]
        self._updating_profile = False

    def autofill_last(self):
        entries = self.app.profiles.ordered()
        if entries:
            entry = entries[0]
            self.refresh_profiles(select_id=entry["id"])
            self._fill(entry)
        else:
            self.refresh_profiles()

    def _profile_selected(self, _spinner, _text):
        if self._updating_profile:
            return
        for entry, label in zip(self._profile_entries, self._profile_labels, strict=False):
            if label == _text:
                self._selected_profile = entry["id"]
                self._fill(entry)
                self.set_status("", None)
                return

    def _fill(self, entry):
        self.label_input.text = entry.get("label", "")
        self.server.text = entry.get("hash", "")
        self.forwards = [dict(forward) for forward in entry.get("forwards", [])]
        self.list_password.text = entry.get("list_password") or ""
        self.refresh_forward_rows()

    def new_profile(self):
        self._selected_profile = None
        self._updating_profile = True
        self.profile.text = t("connect.profile.empty")
        self._updating_profile = False
        self.label_input.text = ""
        self.server.text = ""
        self.list_password.text = ""
        self.forwards = []
        self.refresh_forward_rows()
        self.set_status("", None)

    def save_profile(self):
        try:
            server_hash, forwards, list_password = validate_config(
                self.server.text, self.forwards, self.list_password.text
            )
        except ValidationError as error:
            self.set_status(_error_text(error.code, error.field), "error")
            return
        entry = self.app.profiles.upsert(
            server_hash,
            self.label_input.text,
            forwards,
            list_password,
        )
        self._selected_profile = entry["id"]
        self.refresh_profiles(select_id=entry["id"])
        self.set_status(t("connect.status.saved"), "info")

    def delete_profile(self):
        if not self._selected_profile:
            self.set_status(t("connect.profile.empty"), "error")
            return
        self.app.profiles.delete(self._selected_profile)
        self._selected_profile = None
        self.new_profile()
        self.refresh_profiles()
        self.set_status(t("connect.status.deleted"), "info")

    def refresh_forward_rows(self):
        self.forward_box.clear_widgets()
        if not self.forwards:
            empty = _label(
                t("connect.forwards.empty"),
                size_hint_y=None,
                height=dp(44),
                halign="left",
                valign="middle",
                color=(0.6, 0.65, 0.72, 1),
            )
            empty.bind(size=lambda *_: setattr(empty, "text_size", (empty.width, None)))
            self.forward_box.add_widget(empty)
            return
        for index, forward in enumerate(self.forwards):
            row = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(74), spacing=dp(2))
            port = forward["local_port"] if forward["local_port"] else t("session.forward.ephemeral")
            summary = _label(
                f"{forward['name']} · {port}",
                size_hint_y=None,
                height=dp(28),
                halign="left",
                valign="middle",
            )
            summary.bind(size=lambda *_, label=summary: setattr(label, "text_size", (label.width, None)))
            row.add_widget(summary)
            actions = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))
            actions.add_widget(_button(t("connect.forwards.test"), lambda i=index: self.probe(i), font_size=sp(14)))
            actions.add_widget(
                _button(t("connect.forwards.edit"), lambda i=index: self.edit_forward(i), font_size=sp(14))
            )
            actions.add_widget(
                _button(
                    t("connect.forwards.remove"),
                    lambda i=index: self.remove_forward(i),
                    color=(0.5, 0.2, 0.25, 1),
                    font_size=sp(14),
                )
            )
            row.add_widget(actions)
            self.forward_box.add_widget(row)

    def add_forward(self):
        if self.app.session_active():
            self.set_status(t("connect.forwards.locked"), "error")
            return
        ForwardEditor(self.app, self.forwards, None, self._save_forward).open()

    def edit_forward(self, index):
        if self.app.session_active():
            self.set_status(t("connect.forwards.locked"), "error")
            return
        ForwardEditor(self.app, self.forwards, index, self._save_forward).open()

    def _save_forward(self, index, value):
        if index is None:
            self.forwards.append(value)
        else:
            self.forwards[index] = value
        self.refresh_forward_rows()

    def remove_forward(self, index):
        if self.app.session_active():
            self.set_status(t("connect.forwards.locked"), "error")
            return
        del self.forwards[index]
        self.refresh_forward_rows()

    def connect(self):
        if not self.forwards:
            self.set_status(t("connect.status.need_forward"), "error")
            return
        try:
            server_hash, forwards, list_password = validate_config(
                self.server.text, self.forwards, self.list_password.text
            )
        except ValidationError as error:
            self.set_status(_error_text(error.code, error.field), "error")
            return
        error = self.app.connect(
            server_hash,
            self.label_input.text,
            forwards,
            list_password,
            self._selected_profile,
        )
        if error:
            self.set_status(error, "error")
            return
        self.set_status(t("connect.status.connecting"), "transient")

    def list_services(self):
        error = self.app.list_services(self.server.text, self.list_password.text)
        if error:
            self.set_status(error, "error")
        else:
            self.set_status(t("connect.actions.list") + "…", "transient")

    def probe(self, index):
        forward = self.forwards[index]
        error = self.app.probe_forward(self.server.text, forward)
        if error:
            self.set_status(error, "error")


class SessionScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._log_lines = collections.deque(maxlen=LOG_LIMIT)
        self._forward_signature = None
        self._forward_rows = []

        root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(8))
        self.add_widget(root)

        self.state = _label("", size_hint_y=None, height=dp(30), bold=True)
        self.link = _label("", size_hint_y=None, height=dp(24))
        self.transport = _label("", size_hint_y=None, height=dp(24))
        self.retry = _label("", size_hint_y=None, height=dp(24), color=(1, 0.78, 0.4, 1))
        root.add_widget(self.state)
        root.add_widget(self.link)
        root.add_widget(self.transport)
        root.add_widget(self.retry)
        root.add_widget(_label(t("session.forwards.title"), size_hint_y=None, height=dp(24), bold=True))
        self.forward_box = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)
        self.forward_box.bind(minimum_height=lambda *_: setattr(self.forward_box, "height", self.forward_box.minimum_height))
        root.add_widget(self.forward_box)
        self.counters = _label("", size_hint_y=None, height=dp(24), color=(0.65, 0.72, 0.82, 1))
        root.add_widget(self.counters)
        root.add_widget(_label(t("session.log.title"), size_hint_y=None, height=dp(24), bold=True))
        self.log = TextInput(
            text="",
            readonly=True,
            multiline=True,
            size_hint_y=1,
            background_color=(0.1, 0.11, 0.14, 1),
            foreground_color=(0.82, 0.86, 0.92, 1),
            font_size=sp(12),
        )
        root.add_widget(self.log)
        actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        actions.add_widget(_button(t("session.stop"), self.app.stop_session, color=(0.5, 0.2, 0.25, 1)))
        actions.add_widget(_button(t("connect.actions.settings"), self.app.show_settings, color=(0.22, 0.24, 0.3, 1)))
        root.add_widget(actions)

    def append_log(self, text):
        if not text:
            return
        self._log_lines.append(str(text))
        self.log.text = "\n".join(self._log_lines)

    def clear_log(self):
        self._log_lines.clear()
        self.log.text = ""

    def render(self, snapshot):
        session = snapshot.get("session") or {}
        transport = snapshot.get("transport") or {}
        state = session.get("state", "idle")
        self.state.text = _state_text(state)
        self.link.text = t("session.link.active") if session.get("link") else t("session.link.inactive")
        if transport.get("shared_instance"):
            self.transport.text = t("session.transport", port=transport.get("port"))
            self.transport.color = (0.6, 0.85, 0.65, 1)
        else:
            self.transport.text = t("session.transport.unavailable")
            self.transport.color = (1, 0.65, 0.4, 1)
        retry_in = session.get("retry_in")
        if retry_in is not None:
            self.retry.text = t(
                "session.retry",
                attempt=session.get("retry_attempt", 0),
                seconds=int(retry_in),
            )
        else:
            self.retry.text = ""
        counters = snapshot.get("counters") or {}
        self.counters.text = t(
            "session.counters",
            streams=counters.get("streams", 0),
            connections=counters.get("connections", 0),
            bytes_in=counters.get("bytes_in", 0),
            bytes_out=counters.get("bytes_out", 0),
        )
        self._render_forwards(snapshot.get("forwards") or [])

    def _render_forwards(self, forwards):
        signature = tuple((item.get("name"), item.get("actual_port")) for item in forwards)
        if signature != self._forward_signature:
            self._forward_signature = signature
            self.forward_box.clear_widgets()
            self._forward_rows = []
            for _item in forwards:
                row = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(58), spacing=dp(2))
                title = _label("", size_hint_y=None, height=dp(26), halign="left", valign="middle")
                title.bind(size=lambda *_, label=title: setattr(label, "text_size", (label.width, None)))
                detail = _label("", size_hint_y=None, height=dp(24), halign="left", valign="middle", color=(0.7, 0.76, 0.85, 1))
                detail.bind(size=lambda *_, label=detail: setattr(label, "text_size", (label.width, None)))
                row.add_widget(title)
                row.add_widget(detail)
                self.forward_box.add_widget(row)
                self._forward_rows.append((title, detail))
        for item, (title, detail) in zip(forwards, self._forward_rows, strict=False):
            port = item.get("actual_port")
            port_text = str(port) if port else t("session.forward.ephemeral")
            title.text = f"{item.get('name')} · {port_text}"
            detail.text = (
                f"{t('session.forward.streams')}: {item.get('streams', 0)} · "
                f"{t('session.forward.bytes')}: {item.get('bytes_in', 0)} / {item.get('bytes_out', 0)}"
            )
            if item.get("degraded"):
                detail.text += f" · {t('session.forward.ipv6_degraded')}"


class SettingsScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self._code = "system"

        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        root.add_widget(_title(t("settings.title")))
        root.add_widget(_label(t("settings.language"), size_hint_y=None, height=dp(24)))
        self.buttons = {}
        for code in ("system", "es", "en"):
            button = _button(self._language_label(code), lambda value=code: self.select(value))
            button.size_hint_y = None
            button.height = dp(44)
            self.buttons[code] = button
            root.add_widget(button)
        root.add_widget(_label(t("settings.version", version=APP_VERSION), size_hint_y=None, height=dp(26)))
        root.add_widget(_label(t("settings.license"), size_hint_y=None, height=dp(26)))
        notice = _label(
            t("settings.password_notice"),
            size_hint_y=None,
            height=dp(70),
            halign="left",
            valign="middle",
            color=(1, 0.8, 0.4, 1),
        )
        notice.bind(size=lambda *_: setattr(notice, "text_size", (notice.width, None)))
        root.add_widget(notice)
        root.add_widget(BoxLayout())
        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        buttons.add_widget(_button(t("settings.back"), self.app.hide_settings, color=(0.22, 0.24, 0.3, 1)))
        root.add_widget(buttons)
        self.add_widget(root)

    def _language_label(self, code):
        return t(f"settings.language.{code}")

    def select(self, code):
        self._code = code
        self.app.save_language(code)

    def show(self):
        self._code = self.app.settings["language"]
        self._refresh()

    def _refresh(self):
        for code, button in self.buttons.items():
            button.background_color = (
                (0.2, 0.5, 0.35, 1) if code == self._code else (0.18, 0.2, 0.26, 1)
            )


class PortfwdApp(App):
    title = "RNS Portfwd"

    def build(self):
        Window.clearcolor = (0.07, 0.08, 0.1, 1)
        Window.softinput_mode = "resize"
        self.settings = Settings()
        configure(self.settings["language"])
        self.profiles = ProfileStore()
        self.engine = None
        self._connect_request = None
        self._session_saved = False
        self._background_active = False
        self._background_error = None
        self._notification_text = None
        self._notifications_requested = False
        self._notifications_denied = False
        self._language_rebuilding = False
        self._list_popup = None
        self._probe_popup = None
        self._popup_services = None

        self.screens = ScreenManager(transition=NoTransition())
        self.connect_screen = ConnectScreen(self, name="connect")
        self.session_screen = SessionScreen(self, name="session")
        self.settings_screen = SettingsScreen(self, name="settings")
        self.screens.add_widget(self.connect_screen)
        self.screens.add_widget(self.session_screen)
        self.screens.add_widget(self.settings_screen)
        return self.screens

    def on_start(self):
        Clock.schedule_interval(self._tick, 0.1)
        Clock.schedule_once(lambda *_: self._prestart(), 0.05)
        Clock.schedule_once(lambda *_: self.request_notifications(silent=True), 0.8)
        Clock.schedule_once(lambda *_: self._consume_stop_intent(), 0.4)

    def session_active(self):
        if self.engine is None:
            return False
        session = self.engine.snapshot()["session"]
        return bool(session.get("wanted")) or session.get("state") in (
            "resolving",
            "linking",
            "probing",
            "listening",
            "reconnecting",
        )

    def _prestart(self):
        if self.engine is not None:
            return
        try:
            engine = Engine()
            engine.start()
        except Exception as error:
            self.connect_screen.set_status(f"{type(error).__name__}: {error}", "error")
            return
        self.engine = engine
        snapshot = engine.snapshot()
        transport = snapshot["transport"]
        if transport.get("error"):
            self.connect_screen.set_status(t("error.shared_instance"), "error")
        else:
            self.connect_screen.set_status(t("connect.status.ready"), "info")

    def _tick(self, _dt):
        if self._consume_stop_signal():
            return
        if self.engine is None:
            return
        snapshot = self.engine.snapshot()
        for event in self.engine.drain_events():
            self._handle_event(event)
        self._sync_background(snapshot)
        session = snapshot.get("session") or {}
        state = session.get("state")

        if state == "listening" and not self._session_saved:
            self._remember_session(snapshot)
        if state == "listening" and self.screens.current not in ("session", "settings"):
            _switch_screen(self.screens, "session")
            self.session_screen.render(snapshot)
            self._sync_background(snapshot)
            return
        if state == "error" and self.screens.current == "session":
            self._return_to_connect_with_error(session)
            return
        if state == "idle" and self.screens.current == "session" and not session.get("wanted"):
            self._stop_background_session()
            self.connect_screen.set_status(t("connect.status.stopped"), "idle")
            _switch_screen(self.screens, "connect")
            return

        if self.screens.current == "session":
            self.session_screen.render(snapshot)
        elif self.screens.current == "connect":
            if state in ("resolving", "linking", "probing"):
                self.connect_screen.set_status(_state_text(state), "transient")
            elif state == "reconnecting":
                self.connect_screen.set_status(t("connect.status.connecting"), "transient")
            elif session.get("retry_in") is not None:
                self.connect_screen.set_status(
                    t(
                        "connect.status.retry",
                        attempt=session.get("retry_attempt", 0),
                        seconds=int(session["retry_in"]),
                    ),
                    "transient",
                )

    def _return_to_connect_with_error(self, session):
        message = _error_text(
            session.get("error_code") or "internal",
            message=session.get("error"),
        )
        self.connect_screen.set_status(message, "error")
        self._stop_background_session()
        if self.engine is not None:
            self.engine.stop()
        _switch_screen(self.screens, "connect")

    def _handle_event(self, event):
        if event.get("type") in ("list_result",):
            self._show_list_result(event)
            return
        if event.get("type") in ("probe_result",):
            self._show_probe_result(event)
            return
        if self.screens.current == "session":
            line = self._event_line(event)
            if line:
                self.session_screen.append_log(line)

    def _event_line(self, event):
        kind = event.get("type")
        if kind == "stream_open":
            return t("log.stream_open", name=event.get("name"), port=event.get("port"))
        if kind == "stream_close":
            return t(
                "log.stream_close",
                name=event.get("name"),
                reason=event.get("reason"),
                bytes_in=event.get("bytes_in", 0),
                bytes_out=event.get("bytes_out", 0),
                duration=event.get("duration", 0),
            )
        if kind == "stream_rejected":
            return t("log.stream_rejected", name=event.get("name"), reason=event.get("reason"))
        if kind == "session_retry":
            return t(
                "log.retry",
                attempt=event.get("attempt", 0),
                seconds=event.get("retry_in", event.get("delay", 0)),
                code=event.get("code"),
            )
        if kind == "link_lost":
            return t("log.link_lost")
        if kind == "transport":
            return t("log.transport", state=event.get("state"))
        if kind == "error":
            return t(
                "log.error",
                message=_error_text(event.get("code"), message=event.get("message")),
            )
        if kind == "log":
            return event.get("message")
        return None

    def _remember_session(self, snapshot):
        request = self._connect_request
        if not request:
            return
        self._session_saved = True
        label = request.get("label") or request["hash"][:8]
        entry = self.profiles.upsert(
            request["hash"],
            label,
            request["forwards"],
            request.get("list_password"),
        )
        self.connect_screen.refresh_profiles(select_id=entry["id"])
        self._connect_request["profile_id"] = entry["id"]

    def connect(self, server_hash, label, forwards, list_password, profile_id):
        if self.engine is None:
            return t("connect.status.no_engine")
        self.request_notifications(silent=True)
        self._session_saved = False
        self._connect_request = {
            "hash": server_hash,
            "label": label,
            "forwards": [dict(forward) for forward in forwards],
            "list_password": list_password,
            "profile_id": profile_id,
        }
        self._start_background_session(t("session.notification.connecting"))
        try:
            self.engine.connect(server_hash, forwards, list_password)
        except EngineError as error:
            self._connect_request = None
            self._stop_background_session()
            return _error_text(error.code, error.field, str(error))
        except Exception as error:
            self._connect_request = None
            self._stop_background_session()
            return f"{type(error).__name__}: {error}"
        return None

    def cancel_connection(self):
        if self.engine is not None:
            self.engine.stop()
        self._connect_request = None
        self._session_saved = False
        self._stop_background_session()
        self.connect_screen.set_status(t("connect.status.stopped"), "idle")

    def stop_session(self):
        if self.engine is not None:
            self.engine.stop()
        self._connect_request = None
        self._session_saved = False
        self._stop_background_session()
        self.connect_screen.set_status(t("connect.status.stopped"), "idle")
        _switch_screen(self.screens, "connect")

    def list_services(self, server_hash, list_password):
        if self.engine is None:
            return t("connect.status.no_engine")
        try:
            self.engine.list_services(server_hash, list_password)
        except EngineError as error:
            return _error_text(error.code, error.field, str(error))
        self._show_listing_popup()
        return None

    def probe_forward(self, server_hash, forward):
        if self.engine is None:
            return t("connect.status.no_engine")
        try:
            self.engine.probe(server_hash, forward["name"], forward["password"])
        except EngineError as error:
            return _error_text(error.code, error.field, str(error))
        self._show_probe_popup()
        return None

    def _show_listing_popup(self):
        self._close_popups()
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        content.add_widget(_label(t("connect.actions.list") + "…", size_hint_y=None, height=dp(30)))
        content.add_widget(_button(t("common.close"), lambda: self._list_popup.dismiss(), color=(0.22, 0.24, 0.3, 1)))
        self._list_popup = Popup(
            title=t("discovery.list.title"),
            content=content,
            size_hint=(0.9, 0.4),
            auto_dismiss=False,
        )
        self._list_popup.open()

    def _show_list_result(self, event):
        if not event.get("ok"):
            self._close_popups()
            code = event.get("error_code")
            message = _error_text(code)
            if code == "list_disabled":
                message = t("discovery.list.disabled")
            elif code == "auth":
                message = t("discovery.list.auth")
            self.connect_screen.set_status(message, "error")
            return
        services = event.get("services") or []
        self._close_popups()
        popup = ServiceListPopup(self, services, self._pick_service)
        self._list_popup = popup
        popup.open()

    def _pick_service(self, name):
        editor = ForwardEditor(self, self.connect_screen.forwards, None, self.connect_screen._save_forward)
        editor.name.text = name
        editor.open()

    def _show_probe_popup(self):
        self._close_popups()
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        content.add_widget(_label(t("connect.forwards.test") + "…", size_hint_y=None, height=dp(30)))
        content.add_widget(_button(t("common.close"), lambda: self._probe_popup.dismiss(), color=(0.22, 0.24, 0.3, 1)))
        self._probe_popup = Popup(
            title=t("discovery.probe.title"),
            content=content,
            size_hint=(0.9, 0.4),
            auto_dismiss=False,
        )
        self._probe_popup.open()

    def _show_probe_result(self, event):
        self._close_popups()
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        if event.get("ok"):
            service = event.get("service") or {}
            text = t(
                "discovery.probe.ok",
                name=service.get("name", ""),
                protocol=service.get("protocol", "tcp"),
                auth="yes" if service.get("auth") else "no",
            )
            color = (0.6, 0.9, 0.65, 1)
        else:
            code = event.get("error_code")
            if code == "auth":
                text = t("discovery.probe.fail")
            elif code == "no_path":
                text = t("discovery.probe.no_path")
            else:
                text = t("discovery.probe.link_failed")
            color = (1, 0.65, 0.4, 1)
        label = _label(text, halign="left", valign="middle", color=color)
        label.bind(size=lambda *_: setattr(label, "text_size", (label.width, None)))
        content.add_widget(label)
        content.add_widget(BoxLayout())
        content.add_widget(_button(t("common.close"), lambda: self._probe_popup.dismiss(), color=(0.22, 0.24, 0.3, 1)))
        self._probe_popup = Popup(
            title=t("discovery.probe.title"),
            content=content,
            size_hint=(0.9, 0.5),
            auto_dismiss=False,
        )
        self._probe_popup.open()

    def _close_popups(self):
        for popup in (self._list_popup, self._probe_popup):
            if popup is not None:
                try:
                    popup.dismiss()
                except Exception:
                    pass
        self._list_popup = None
        self._probe_popup = None

    def _start_background_session(self, text):
        if not is_android():
            self._background_active = False
            return False
        started = background.start(
            t("session.notification.title"),
            text,
            t("session.notification.stop"),
        )
        self._background_active = started
        if started:
            self._background_error = None
            self._notification_text = text
        else:
            self._background_error = background.last_error() or t(
                "session.background.unavailable", error="unknown"
            )
            print(f"PORTFWD_BACKGROUND fallback: {self._background_error}", flush=True)
        return started

    def _stop_background_session(self):
        background.stop()
        self._background_active = False
        self._background_error = None
        self._notification_text = None

    def _set_background_text(self, text):
        if not self._background_active or text == self._notification_text:
            return
        self._notification_text = text
        if background.update(
            t("session.notification.title"),
            text,
            t("session.notification.stop"),
        ):
            self._background_error = None
            return
        self._background_error = background.last_error() or t(
            "session.background.unavailable", error="unknown"
        )
        self._background_active = background.start(
            t("session.notification.title"),
            text,
            t("session.notification.stop"),
        )

    def _sync_background(self, snapshot):
        session = snapshot.get("session") or {}
        state = session.get("state")
        if not (session.get("wanted") or state in ("listening", "reconnecting")):
            if self._background_active:
                self._stop_background_session()
            return
        if state == "listening":
            text = t("session.notification.listening", count=len(snapshot.get("forwards") or []))
        elif state == "reconnecting":
            text = t("session.notification.reconnecting")
        else:
            text = t("session.notification.connecting")
        if not self._background_active:
            self._start_background_session(text)
        else:
            self._set_background_text(text)

    def show_settings(self):
        self.settings_screen.show()
        _switch_screen(self.screens, "settings")

    def hide_settings(self):
        if self.engine is not None:
            snapshot = self.engine.snapshot()
            if snapshot["session"].get("state") == "listening":
                _switch_screen(self.screens, "session")
                self.session_screen.render(snapshot)
                return
        _switch_screen(self.screens, "connect")

    def save_language(self, code):
        self.settings.set_language(code)
        previous = current_language()
        configure(code)
        if resolve_language(code) == previous and code in ("system", "es", "en"):
            self.settings_screen._refresh()
        self._rebuild_ui()

    def _rebuild_ui(self):
        if self._language_rebuilding:
            return
        self._language_rebuilding = True
        try:
            self._close_popups()
            state = None
            if self.screens.current == "connect":
                state = self.connect_screen.form_state()
            elif self._connect_request:
                state = {
                    "label": self._connect_request.get("label", ""),
                    "hash": self._connect_request.get("hash", ""),
                    "forwards": self._connect_request.get("forwards", []),
                    "list_password": self._connect_request.get("list_password", ""),
                    "profile_id": self._connect_request.get("profile_id"),
                }
            self.screens.clear_widgets()
            self.connect_screen = ConnectScreen(self, name="connect")
            self.session_screen = SessionScreen(self, name="session")
            self.settings_screen = SettingsScreen(self, name="settings")
            self.screens.add_widget(self.connect_screen)
            self.screens.add_widget(self.session_screen)
            self.screens.add_widget(self.settings_screen)
            if state:
                self.connect_screen.restore(state)
            else:
                self.connect_screen.autofill_last()
            if self.engine is not None and self.engine.snapshot()["session"].get("state") == "listening":
                snapshot = self.engine.snapshot()
                self.session_screen.render(snapshot)
                _switch_screen(self.screens, "session")
            else:
                _switch_screen(self.screens, "connect")
            print(f"PORTFWD_I18N language={current_language()}", flush=True)
        finally:
            self._language_rebuilding = False

    def _on_keyboard(self, _window, key, _scancode=None, _codepoint=None, _modifier=None):
        if key != 27:
            return False
        if self.screens.current == "settings":
            self.hide_settings()
        elif self.screens.current == "session":
            self.stop_session()
        else:
            self.stop()
        return True

    def on_pause(self):
        return True

    def on_resume(self):
        self._consume_stop_intent()
        if self._consume_stop_signal():
            return
        self.request_notifications(silent=True)
        if self.engine is None:
            return
        snapshot = self.engine.snapshot()
        if snapshot["session"].get("state") == "listening":
            self.session_screen.render(snapshot)
            _switch_screen(self.screens, "session")
        self._sync_background(snapshot)

    def on_stop(self):
        if self._background_active:
            return
        self._stop_background_session()
        if self.engine is not None:
            self.engine.stop()

    def _consume_stop_signal(self):
        if not is_android():
            return False
        try:
            from jnius import autoclass

            if not autoclass("io.areche.portfwd.StopSignal").consume():
                return False
        except Exception as error:
            print(f"PORTFWD_INTENT stop signal unavailable: {error}", flush=True)
            return False
        self.stop_session()
        return True

    def _consume_stop_intent(self):
        if not is_android():
            return False
        try:
            from jnius import autoclass

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            intent = activity.getIntent()
            if intent is None:
                return False
            action = intent.getStringExtra("portfwd_action")
            if not action:
                return False
            intent.removeExtra("portfwd_action")
            if action == "stop":
                self.stop_session()
            return True
        except Exception as error:
            print(f"PORTFWD_INTENT stop unavailable: {error}", flush=True)
            return False

    def request_notifications(self, silent=False):
        if not is_android() or self._notifications_requested:
            return
        self._notifications_requested = True
        try:
            from jnius import autoclass

            if autoclass("android.os.Build$VERSION").SDK_INT < 33:
                return
            from android.permissions import Permission, check_permission, request_permissions

            if check_permission(Permission.POST_NOTIFICATIONS):
                return
        except Exception as error:
            if not silent:
                self.connect_screen.set_status(
                    t("permissions.notifications_failed", error=error), "error"
                )
            return
        try:
            request_permissions([Permission.POST_NOTIFICATIONS], self._notification_permission_result)
        except Exception as error:
            if not silent:
                self.connect_screen.set_status(
                    t("permissions.notifications_failed", error=error), "error"
                )

    def _notification_permission_result(self, _permissions, grants):
        granted = bool(grants) and all(grants)
        self._notifications_denied = not granted
        print(f"PORTFWD_BACKGROUND notifications={'granted' if granted else 'denied'}", flush=True)
        if not granted:
            Clock.schedule_once(
                lambda *_: self.connect_screen.set_status(
                    t("session.background.notifications_denied"), "info"
                ),
                0,
            )


if __name__ == "__main__":
    PortfwdApp().run()
