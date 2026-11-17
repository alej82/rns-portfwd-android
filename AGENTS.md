# RNS Portfwd Android — agent notes

## What this is

Kivy/python-for-android shell for the `rns-portfwd` client. The forwarding
engine is in `app/portfwd_android/`; the protocol is an unmodified vendor copy
in `app/rns_portfwd/` (commit `a76e162`). The app targets API 33, min API 24,
`arm64-v8a`, and runs against the Reticulum shared instance on
`127.0.0.1:37428`.

## Architecture rules

- **Never edit `app/rns_portfwd/`.** Re-vendor from upstream and follow
  `vendor/VENDOR.md`. Run `diff -r` against upstream after any vendor update.
- **RNS is a process singleton.** Do not call `RNS.Reticulum.exit_handler()`.
  Do not construct `RNS.Reticulum` before a successful TCP preflight of the
  shared instance. Start it at most once, from the Kivy main thread.
  `Reticulum` assigns its singleton before connecting; a failed handshake
  poisons the process.
- **RNS callbacks must not do socket I/O.** They may set an `Event`, update
  state under a lock and publish a bounded event. Session cleanup runs in the
  session worker.
- **Transport liveness is live, not cached.** `Engine._transport_alive()`
  checks the `Local shared instance` interface `online` flag so the UI can show
  `Shared instance unavailable` while Columba is down.
- **One session at a time.** `Engine.connect()` rejects a second session.
  `Session.stop()` closes listeners, streams and the Link but leaves RNS alive.
- **Re-resolve on every attempt.** After a shared-instance outage the cached
  identity is still valid, but the RNS path table is not; always call
  `rns_ops.resolve_identity` before reopening the Link.
- **Listeners are loopback only**: `127.0.0.1` plus `[::1]` on the same logical
  port when available. Port `0` binds IPv4 first and reuses the real port for
  IPv6. Ports `1..1023` are rejected. A TCP and a UDP forward may share a port;
  duplicates are keyed on `(protocol, port)`.
- **UDP is one stream per local source.** `Session` keys datagram sessions on
  `(forward, listener socket, peer)` so replies leave from the socket the peer
  reached. The first datagram of a new peer is buffered (max 32, at most
  `max_streams` pending keys) while the stream opens. Oversized datagrams are
  dropped, never fragmented or compressed; a full queue drops instead of
  blocking. Idle streams close after 60 s and reopen on the next datagram.
- **`password is None` means a public service.** Public forwards send an empty
  credential, are never registered as secrets, and the probe must not require a
  password. `/probe` decides the service protocol: a mismatch is a terminal
  `protocol_mismatch` configuration error, never an auth failure or a retry.
- **Secrets** live in the app sandbox (`profiles.json`, `0600`), are registered
  with `rns_portfwd.logging.register_secret()` and must never appear in logs,
  notifications or UI errors.
- **UI strings** go through `portfwd_android.i18n.t()`; server names, hashes,
  backend names and server error strings are never translated.
- **Manifest invariants**: `android:allowBackup="false"`, the
  `dataSync` foreground service type, and the non-exported `StopReceiver`.
  `tools/p4a_hook.py` adds/repairs both declarations after p4a packaging.
- **Notification stop is a broadcast**, not an activity extra:
  `PortfwdService` → `StopReceiver` → `StopSignal` → `Main._tick()` →
  `engine.stop()`. This survives the activity already being resumed.

## Layout

```
app/main.py                  Kivy screens and app lifecycle
app/portfwd_android/
  engine.py                  facade: transport, snapshot, events, list/probe
  session.py                 one session, probe, TCP streams, UDP datagram
                             sessions, reconnect supervisor
  rns_ops.py                 resolve / open Link / request helpers
  listeners.py               dual IPv4/IPv6 loopback listeners (TCP and UDP)
  reticulum.py               preflight + start-once RNS handling
  rns_config.py              managed shared-instance config (0600)
  validation.py              hash/name/port/protocol/password validation
  profiles.py                profiles.json (0600), protocol + nullable password
  events.py                  bounded ordered event queue
  logbridge.py               RNS/portfwd logs -> redacted events
  i18n.py / messages.py      catalogs, fallback, error-code mapping
  settings.py                language preference
  background.py              foreground-service facade
app/rns_portfwd/             vendor (do not edit)
app/assets/i18n/             translations.json master + es.json
android-java/io/areche/portfwd/
  PortfwdService.java        foreground service, notification, wake lock
  StopReceiver.java          notification action -> StopSignal -> activity
  StopSignal.java            one-shot stop flag shared with Python
tools/headless_engine.py     engine without Kivy
tools/p4a_hook.py            manifest service/receiver finalization
scripts/                     setup-toolchain, build-apk, install-apk
tests/unit, tests/integration
evidence/                    build/device evidence and checklist
vendor/VENDOR.md             upstream commit + re-vendor procedure
```

## Commands

```bash
python3 -m pytest -q
python3 -m pytest -q -m "not integration"
ruff check app tools tests
scripts/setup-toolchain.sh
scripts/build-apk.sh
scripts/install-apk.sh
openspec validate add-udp-and-public-forwards --strict
openspec validate --all --strict
```

Current verification: 108 tests pass (89 unit, 19 integration), ruff clean,
OpenSpec strict valid, 36/37 tasks complete (device smoke test pending).

## Device workflow (POCO F2 Pro, `8ffbcdbf`)

- Columba must be running for the shared instance on `127.0.0.1:37428`.
  Verify with `adb shell 'echo test | toybox nc -w 2 127.0.0.1 37428'`.
- `adb shell run-as io.areche.portfwd` can inspect the private sandbox. The
  profiles file is `files/portfwd/profiles.json`; seed it with a test profile
  to avoid typing on the device.
- Test a forward from the phone with
  `adb shell 'printf hello | toybox nc -w 5 127.0.0.1 <port>'`.
- Test a UDP forward with `adb shell 'printf hello | toybox nc -u -w 5 127.0.0.1 <port>'`
  against a server that exposes the service with `--expose-udp`.
- `adb shell input tap` coordinates include the status-bar offset; verify with
  a screenshot before automating. The offset changes while the IME is visible.
- A notification stop can be exercised from the shade; the action is
  localized and uses a broadcast receiver.
- Capture evidence in `evidence/` and update `evidence/README.md`.
- `pm clear io.areche.portfwd` removes profiles and settings; reseed afterward
  if needed.

## Known device facts

- App-uid `bind()` to port 80 fails with `EACCES`, so `<1024` stays invalid.
- Shared-instance RPC calls may log `digest sent was rejected`; portfwd does
  not use RPC and path resolution/forwarding/reconnection work.
- The foreground notification is `io.areche.portfwd` id 48, channel
  `portfwd_session`, with one localized «Stop»/«Detener» action.
- HyperOS auto-grants `POST_NOTIFICATIONS`; `pm revoke` reports an unknown
  permission, so the denied-permission branch is not reachable from adb on
  this ROM.
- Removing the task kills the process from `onTaskRemoved`; listeners and the
  notification disappear and the local port refuses connections.

## Testing notes

- Unit tests must not initialize `RNS.Reticulum` directly (singleton). Use
  fakes for `reticulum.start_reticulum` or run the engine in a subprocess.
- Integration tests launch a private `rnsd`, a real `rns-portfwd-server` and
  `tools/headless_engine.py`; they are marked `integration`. The harness can
  expose UDP and passwordless services (`udp_services`, `public_services`,
  `public_udp_services`) and drive UDP forwards (`udp_forwards`,
  `public_udp_forwards`); `tests/integration/test_udp_public.py` covers UDP
  round trips, per-source isolation, public forwards, protocol mismatches and
  UDP idle expiry.
- `tests/unit/test_messages.py` covers error-code mapping without importing
  Kivy.
- Protocol-mismatch errors are terminal: assert the `protocol_mismatch` event
  and the exit code, not a retry.
- When changing UI strings, update `app/assets/i18n/translations.json` and
  `es.json` together; `i18n.validate_catalog()` enforces key and placeholder
  parity.
- When changing the vendor or the protocol, re-run the full suite and rebuild;
  never patch `app/rns_portfwd/`.
