## 1. Vendor

- [x] 1.1 Copy upstream `src/rns_portfwd/` at `a76e162` over `app/rns_portfwd/` (including `datagrams.py`, excluding `__pycache__`)
- [x] 1.2 Update `vendor/VENDOR.md` with the new commit and the note that `c2400eb` was superseded by the rewritten history (`b18a2b9` → `a76e162`)
- [x] 1.3 Verify with `diff -r app/rns_portfwd <upstream>/src/rns_portfwd` and run `python3 -m pytest -q` to confirm the existing suite still passes

## 2. Forward model and validation

- [x] 2.1 Add `protocol` (`tcp`/`udp`) and nullable password to `validate_forward`, normalising `None`/`""` to `None` and rejecting unknown protocols
- [x] 2.2 Key duplicate-port detection on `(protocol, port)` in `validate_forwards`
- [x] 2.3 Pass `protocol` through `to_vendor_forwards` and keep `None` as `Forward(password=None)`
- [x] 2.4 Update unit tests in `tests/unit/test_validation.py` for protocol defaults, invalid protocol, public password, shared tcp/udp port and duplicate udp port

## 3. Profiles

- [x] 3.1 Normalise `protocol` (default `tcp`) and nullable `password` on load in `ProfileStore._normalise`
- [x] 3.2 Persist both fields in `ProfileStore.upsert` and keep file mode `0600`
- [x] 3.3 Extend `tests/unit/test_profiles.py` with a legacy profile migration, a public UDP round trip and a `0600` assertion

## 4. Listeners

- [x] 4.1 Branch `open_listener` on protocol: `SOCK_DGRAM` with `settimeout(0.5)` for UDP, `listen()` only for TCP
- [x] 4.2 Expose `protocol` on `BoundListener` and keep IPv4-first, IPv6-same-port, degrade-to-v4 and `<1024` rules for UDP
- [x] 4.3 Extend `tests/unit/test_listeners.py` with UDP dual-stack bind, ephemeral port reuse, low-port rejection and a UDP port sharing a TCP port

## 5. Engine and session: UDP path

- [x] 5.1 Add `udp_idle_timeout` to `Engine`/`Session` (default from vendor constants) and pass it to `DatagramSession`
- [x] 5.2 Start a datagram receive loop per UDP listener socket in `Session._start_accept_threads`, dropping truncated datagrams and logging the limit
- [x] 5.3 Implement `_open_udp_session` (link wait, stream slot, reserve, install, `OPEN` with `password or ""`, open error handling, pending flush) keyed by `(name, socket, peer)`
- [x] 5.4 Bound pending buffers (32 datagrams per source, `max_streams` pending keys) and drop instead of blocking
- [x] 5.5 Reap idle UDP sessions in `Session._supervise` and purge UDP state in `_stream_closed`, `_cleanup_link` and `_close_listeners`
- [x] 5.6 Add `protocol` and `public` to each forward entry in `Session.snapshot()`
- [x] 5.7 Add unit tests for the pending-buffer bound, idle reaping and snapshot fields using a fake mux/transport

## 6. Probing and errors

- [x] 6.1 Rewrite `_probe_all` to keep the per-service `/probe` payload, send `password or ""` and raise `protocol_mismatch` with `service`, `expected` and `actual`
- [x] 6.2 Accept public forwards in `Engine.probe` and register only non-empty secrets
- [x] 6.3 Add `fields` to `Terminal`, publish them from `Session._fail` and forward parameters in `messages.error_text`
- [x] 6.4 Add `error.protocol_mismatch` and `error.protocol` keys and the auth hint for missing passwords
- [x] 6.5 Unit-test the protocol comparison and the missing-password hint with a fake link

## 7. UI

- [x] 7.1 Add a protocol `Spinner` and a public `Switch` to `ForwardEditor`, hiding/ignoring the password when public
- [x] 7.2 Validate the duplicate local port on `(protocol, port)` in the editor and refuse a private forward without a password
- [x] 7.3 Show protocol and public state in `ConnectScreen` forward rows without ever rendering the password
- [x] 7.4 Localise the auth label in `ServiceListPopup` and the probe popup, and mark a picked public service as public
- [x] 7.5 Pass `None` for public forwards in `PortfwdApp.probe_forward` and render `protocol_mismatch` via `error_text`

## 8. Tooling and integration tests

- [x] 8.1 Add `--forward-udp`, `--forward-public` and `--forward-udp-public` to `tools/headless_engine.py`
- [x] 8.2 Extend `tests/integration/harness.py` with a UDP echo backend, UDP/public service exposure in `start_server` and a UDP round-trip helper
- [x] 8.3 Add integration tests: UDP round trip, two-source isolation, public TCP forward, public UDP forward, mixed public/private and protocol mismatch
- [x] 8.4 Run `python3 -m pytest -q` and `python3 -m pytest -q -m "not integration"`, then `ruff check app tools tests`

## 9. Docs and close-out

- [x] 9.1 Document UDP and public forwards in `README.md` (usage, limits, dropped datagrams, idle timeout)
- [x] 9.2 Update `AGENTS.md` with the new vendor commit, UDP/public invariants and the profile schema note
- [x] 9.3 Build the APK with `scripts/build-apk.sh` and smoke-test UDP forwarding plus a public service on the POCO F2 Pro (see `evidence/2026-11-17-udp-and-public/`)
