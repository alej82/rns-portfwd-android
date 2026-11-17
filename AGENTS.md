# RNS Portfwd Android — agent notes

## What this is

Kivy/python-for-android shell for the `rns-portfwd` client. The forwarding
engine is in `app/portfwd_android/`; the protocol is an unmodified vendor copy
in `app/rns_portfwd/` (commit `ee2068a`). The app targets API 33, min API 24,
`arm64-v8a`, and runs against the Reticulum shared instance on
`127.0.0.1:37428`. It pins `rns==1.6.0` so link-request stamping is available.

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
  `max_streams` pending keys) while the stream opens. A first datagram that
  arrives while the session is rebuilding an idle Link is retried once on the
  replacement Link; the session compares `_rebuilds` before and after an
  attempt to tell "the Link is being replaced" from "there is no Link at all".
  A datagram that is finally discarded is counted in `stats["dropped"]` and
  published as `datagrams_dropped` with a reason, never dropped silently.
  Oversized datagrams are dropped, never fragmented or compressed; a full queue
  drops instead of blocking. Idle streams close after 60 s and reopen on the
  next datagram.
- **`password is None` means a public service.** Public forwards send an empty
  credential, are never registered as secrets, and the probe must not require a
  password. `/probe` decides the service protocol: a mismatch is a terminal
  `protocol_mismatch` configuration error, never an auth failure or a retry.
- **Secrets** live in the app sandbox (`profiles.json`, `0600`), are registered
  with `rns_portfwd.logging.register_secret()` and must never appear in logs,
  notifications or UI errors.
- **The device identity is opt-in and per profile.** `profiles.json` carries
  `identify` (default `False`); only then does `rns_ops.open_link` call
  `Link.identify()` on an established Link, from that callback and before
  `established.set()`, so no probe precedes the proof. The key lives in
  `files/portfwd/identity.id` (`0600`, one per device) and only its 32-hex hash
  is ever displayed. An identification race is expected: `/probe` is not
  deferred by the server, so `rns_ops.probe_request` re-identifies and retries
  a bounded number of times before reporting `auth`.
- **Link stamping is configured in one place.** `rns_ops.open_link` calls the
  vendor `configure_link_stamping()` right before creating the destination and
  the `Link`, for the session, every reconnect, `list` and `probe`.
  `stamp_mode` is `auto` (follow the advertised `stamp_cost`), `off` or `cost`.
  The vendor helper mutates process-global `Transport.ALWAYS_STAMP_LRS` /
  `LR_COST`, so never assume it survives a profile change: reconfigure on every
  attempt. An incapable local Reticulum is a terminal `stamp_unsupported`
  error, never a retry loop. Stamp generation happens in the session worker
  thread; on Android it forks workers (`Stamps.job_android`).
  A custom cost is capped at 16 by `validation.MAX_STAMP_COST` (cost N needs
  about 2**N attempts, so the vendor bound of 40 is days of work on a phone);
  `auto` still follows any advertised cost. `open_link(on_stamp=…)` reports the
  resolved cost before the `Link` is built so the session can publish the
  `stamping` state instead of an indeterminate "Opening Link…".
- **A Link that never carried a stream is rebuilt before its first use.** The
  vendored server tears down any Link that has not opened a stream within
  `--auth-timeout` (30 s by default), and neither `/probe` nor a Reticulum
  keepalive cancels that timer. `Session._wait_link()` therefore refreshes an
  unused Link older than `idle_refresh` (20 s default, `--idle-refresh` in the
  harness) through the normal reconnect path, and marks the Link as used once
  it is handed to a connection; a used Link is never refreshed.
- **UI strings** go through `portfwd_android.i18n.t()`; server names, hashes,
  backend names and server error strings are never translated.
- **Icon assets are generated, never hand-edited.** `tools/make_icons.py`
  derives `assets/icon/{icon,icon-foreground,icon-background}.png` and
  `presplash.jpg` from `assets/icon/source/android_enchufe.png` (2x render of
  the SVG, plate centre 899.5,999.5 radius 860). `buildozer.spec` wires the
  legacy icon, the adaptive pair (`icon.adaptive_*`) and the presplash;
  `android.presplash_color` is `#FFFFFF` because p4a draws the presplash with
  `FIT_CENTER` and the artwork's margin is white. The splash canvas is 1080x1920
  (16:9) with the plate at 55 % of its width: because the view scales the image
  to fit the screen, a full-width artwork would fill the phone, while a wider
  canvas keeps the logo centred and smaller on every portrait aspect. The
  presplash must be a real JPEG: p4a copies it to `drawable/presplash.jpg`
  without converting.
  `tests/unit/test_assets.py` enforces sizes, formats and transparency.
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
  rns_ops.py                 resolve / open Link / identify / request helpers
  identity.py                device identity file (0600) + public hash
  listeners.py               dual IPv4/IPv6 loopback listeners (TCP and UDP)
  reticulum.py               preflight + start-once RNS handling
  rns_config.py              managed shared-instance config (0600)
  validation.py              hash/name/port/protocol/password/stamp validation
  profiles.py                profiles.json (0600): protocol, nullable password,
                             identify, stamp_mode, stamp_cost
  events.py                  bounded ordered event queue
  logbridge.py               RNS/portfwd logs -> redacted events
  i18n.py / messages.py      catalogs, fallback, error-code mapping
  settings.py                language preference
  background.py              foreground-service facade
app/rns_portfwd/             vendor (do not edit)
app/assets/i18n/             translations.json master + es.json
assets/icon/                 launcher icon + presplash (not bundled in the
                             payload; the APK build reads them from here)
assets/icon/source/          logo vector + 2x render
tools/make_icons.py          regenerates assets/icon from the artwork
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
openspec validate add-client-identity-and-stamping --strict
openspec validate --all --strict
```

Current verification: 229 tests pass (202 unit, 27 integration), ruff clean,
OpenSpec strict valid. The three changes are archived and `openspec/specs/`
holds the capability specs: `client-identity`, `link-stamping`,
`link-liveness`, `udp-forwarding` and `public-forwards`. Device passes cover
allow-list + stamping forwards, anonymous rejection, reconnect after a
shared-instance restart, the stamp-cost cap, the unused-Link refresh, UDP and
public forwards, and the launcher icon/splash.

## Build notes

- Changing the version of a pure-python requirement is not enough for p4a:
  `build-arm64-v8a/build/python-installs/<dist>/<arch>` is reused, so an older
  package survives next to the new `*.dist-info`. Before rebuilding after a
  pin change, delete the old package directory and both `*-dist-info` entries
  (or the whole `python-installs/<dist>` and `dists/<dist>`), then verify the
  APK bundle contains the new module.
- `scripts/build-apk.sh` picks `~/.local/jdk17` or a system JDK 17; set
  `JAVA_HOME` if the JDK lives elsewhere.
- The APK payload is `lib/arm64-v8a/libpybundle.so` (gzip tar). To check what
  was packaged:
  `unzip -p bin/*.apk lib/arm64-v8a/libpybundle.so | tar -tzf - | grep RNS/`.

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
- Identity tests use a temp `PORTFWD_CONFIG_DIR`; `identity.reset_cache()`
  between cases. `rns_ops` tests fake `RNS.Link`/`RNS.Destination`, never a
  `Reticulum` instance.
- Integration: `start_server(extra=["--allow-identity", "svc:<hash>",
  "--link-stamp-cost", "6"])` and `start_engine(extra=["--identity", path,
  "--stamp-mode", "auto"])` exercise both features without new harness
  helpers. The local `rns` must be 1.6.0 for stamping.
- Protocol-mismatch errors are terminal: assert the `protocol_mismatch` event
  and the exit code, not a retry.
- When changing UI strings, update `app/assets/i18n/translations.json` and
  `es.json` together; `i18n.validate_catalog()` enforces key and placeholder
  parity.
- When changing the vendor or the protocol, re-run the full suite and rebuild;
  never patch `app/rns_portfwd/`.
