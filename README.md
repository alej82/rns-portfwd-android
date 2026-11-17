# RNS Portfwd for Android

An Android client for [`rns-portfwd`](https://github.com/areche/rns-portfwd):
it forwards local TCP and UDP ports to named services exposed over Reticulum,
using the shared Reticulum instance already provided by Columba, MeshChat or
another Android app.

The app is a Kivy/python-for-android shell around an unmodified vendor copy of
`rns_portfwd` (commit `a76e162`). The forwarding engine is headless and the UI
never touches RNS or sockets directly.

Current version: `0.2.0` · License: GPL-3.0-or-later · Target: Android 7.0+,
`arm64-v8a`.

## Features

- One server at a time, one Reticulum `Link`, and many local forwards.
- TCP and UDP forwards, including passwordless public services; a TCP and a
  UDP forward may share the same local port.
- `forward`, `list` and `probe` from the phone; the service list shows each
  service's protocol and whether it asks for a password.
- Local listeners only on `127.0.0.1` and `[::1]`; the logical port is shared
  by both families when IPv6 is available.
- Ephemeral local port `0`.
- Saved profiles with label, hash, forwards, optional list password and
  last-used ordering.
- Foreground service, persistent notification, wake lock and a localized
  «Stop»/«Detener» action; the session survives screen-off and backgrounding.
- Automatic path re-resolution and Link recovery after the shared-instance
  provider restarts.
- Spanish and English UI with runtime language switching and no engine restart.
- Secrets redacted from logs, errors and the notification.
- Bounded event/log history, per-forward byte and stream counters.

## How it works

```
app/main.py                  Kivy screens and Android lifecycle
app/portfwd_android/
  engine.py                  facade: transport, snapshot, events, list/probe
  session.py                 one forwarding session, TCP streams, UDP datagram
                             sessions and the reconnect supervisor
  rns_ops.py                 resolve / open Link / request helpers
  listeners.py               dual IPv4/IPv6 loopback listeners (TCP and UDP)
  reticulum.py               TCP preflight + start-once singleton handling
  rns_config.py              managed shared-instance config (0600)
  validation.py              hash/name/port/password validation
  profiles.py                profiles.json in the private sandbox
  events.py                  bounded ordered event queue
  logbridge.py               RNS/portfwd logs -> redacted UI events
  i18n.py / messages.py      catalogs, fallback, error-code mapping
  background.py              foreground-service facade
app/rns_portfwd/             vendor copy (do not edit)
android-java/                PortfwdService, StopReceiver, StopSignal
tools/headless_engine.py     engine without Kivy
tools/p4a_hook.py            manifest service/receiver finalization
```

Sequence: validate → TCP preflight of `127.0.0.1:37428` → start or reuse the
RNS singleton → resolve the server identity by hash → open one `Link` → probe
each service (and confirm its protocol) → open loopback listeners → multiplex
each accepted TCP socket as a stream, or map each local UDP peer to its own
datagram stream. If the Link dies, live streams close, listeners stay open, and
the engine re-resolves the path and reopens the Link with 1–60 s backoff.

## Requirements

- Android 7.0 (API 24) or newer, `arm64-v8a`.
- A provider with a Reticulum shared instance listening on TCP
  `127.0.0.1:37428` (Columba is tested).
- A server running `rns-portfwd-server` reachable over the mesh.

The client is anonymous: it resolves the server's public identity by hash and
never stores a client identity. Server-side enumeration is private and needs
the server's `--list-password`.

## Build

The toolchain is pinned to python-for-android `v2024.01.21`, Python 3.11,
Android API 33, min API 24 and NDK 25b.

```bash
scripts/setup-toolchain.sh   # venv + .p4a; reuses ~/.buildozer SDK/NDK
scripts/build-apk.sh         # → bin/portfwd-0.2.0-arm64-v8a-debug.apk
scripts/install-apk.sh       # installs and launches on the connected device
```

`scripts/setup-toolchain.sh` needs host Python 3.10 or 3.11 (buildozer 1.5.0).
The first build downloads the Android SDK/NDK into `~/.buildozer` if needed.
`buildozer.spec` keeps `rns==1.5.7`, `libbz2` and `liblzma` pinned.

## Use

1. Start Columba (or another shared-instance provider) and wait until it is
   connected to the mesh.
2. Open **RNS Portfwd**, enter the server hash, add at least one forward
   (service name, local port, TCP or UDP, and either a service password or the
   public switch), and press **Conectar**.
3. Connect any app on the phone to `127.0.0.1:<local port>`.

`Listar servicios` needs the server's `--list-password`; each row shows the
protocol and whether the service asks for a password. `Probar` validates one
forward: with a public forward it sends an empty credential, and a service that
still wants a password is reported as an authentication failure. Tapping a
listed service pre-fills the forward editor, including the protocol and the
public switch. Profiles are saved on the first successful connection and can
also be saved explicitly.

A forward whose protocol does not match the server's service fails before any
local port is opened and is reported as a configuration error, so a UDP service
is never silently reached over TCP.

Service passwords are stored in clear text inside the app's private sandbox
(`files/portfwd/profiles.json`, mode `0600`). Public forwards store `null` and
register no secret. Android backup is disabled. Uninstalling the app removes
the profiles.

## Tests

```bash
python3 -m pytest -q                      # unit + integration
python3 -m pytest -q -m "not integration" # unit only
ruff check app tools tests
```

Integration tests start a private `rnsd`, a real `rns-portfwd-server` and the
headless engine; they cover `list`, `probe`, two concurrent forwards, a 4 MiB
binary transfer, half-close, authentication failure, port conflicts, disabled
enumeration, UDP round trips (IPv4 and IPv6), per-source UDP isolation, public
TCP/UDP forwards, mixed public/private links, protocol mismatches and UDP idle
expiry. Unit tests cover validation, profiles, events, i18n parity, redaction,
listeners (TCP and UDP), RNS startup, the notification manifest and the
background facade.

`tools/headless_engine.py` runs the engine without Kivy against a private
Reticulum instance:

```bash
python3 tools/headless_engine.py <hash> \
  --forward echo:2222:secret \
  --forward-udp dns:5353:secret \
  --forward-public web:8080 \
  --configdir /path/to/private-rns \
  --shared-port <port> --control-port <port>
```

`--udp-idle-timeout` shortens the datagram idle timeout for tests.

## Device verification

Verified on a POCO F2 Pro (Android 13 / HyperOS) with Columba as the shared
instance and a desktop `rns-portfwd-server` over the mesh:

- clean build and install;
- `list` and `probe`;
- TCP echo through the phone listener;
- screen-off/background forwarding;
- notification «Stop» and task removal;
- opening the app before Columba, then connecting without restarting;
- language switch during an active session;
- Columba restart with `Shared instance unavailable` followed by automatic
  recovery and forwarding;
- app-uid `bind()` to port 80 denied with `EACCES`.

Evidence, logs and the checklist are in [`evidence/README.md`](evidence/README.md).

## Known limitations

- The server, reverse forwarding, multiple simultaneous servers, TCP
  resumption after a link loss, VPN, LAN exposure and autostart are out of
  scope.
- UDP datagrams are never fragmented: anything larger than the negotiated
  channel maximum is dropped and logged, and a full queue drops instead of
  blocking. There is no per-forward UDP tuning.
- A UDP stream with no traffic for 60 s is closed; the next datagram from the
  same local source opens a fresh stream, so applications see a pause, not an
  error.
- Local ports below 1024 are rejected. On the reference device an app-uid
  `bind()` to port 80 fails with `EACCES`, so the conservative validation is
  correct.
- The shared instance may log `RPC failed ... digest sent was rejected` while
  setting destination data use. Portfwd does not use RPC; path resolution,
  forwarding and reconnection still work.
- HyperOS auto-grants `POST_NOTIFICATIONS` and does not expose it to
  `pm revoke`; the denied-permission fallback is implemented but was not
  triggerable in device testing.
- `rns` is pinned to `1.5.7`; the vendored protocol has been exercised against
  that version.

## License

GPL-3.0-or-later. The app includes the GPL-licensed `rns-portfwd` package;
see `LICENSE` and `vendor/VENDOR.md`.
