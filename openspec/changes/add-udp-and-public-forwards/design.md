## Context

See `proposal.md` for the motivation. Two constraints shape everything here:

- The vendor copy under `app/rns_portfwd/` is a byte-for-byte import of an
  upstream commit and the project rule is "never edit it". Upstream already
  implemented both features, so the app adds no protocol code of its own; it
  adapts the mobile engine to the new upstream API.
- The mobile engine (`app/portfwd_android/session.py`) owns its own lifecycle:
  a `Mux`, a `Thread`-per-connection model, a `wanted`/`_stop` control pair,
  snapshot-based stats and event publishing. It does not reuse upstream's
  `PortfwdClient`, so the UDP forwarding logic must be re-expressed in that
  lifecycle rather than imported.

Current state that matters:

- `Forward` in the app is a plain dict `{name, local_port, password}`;
  `validation.validate_forward` hard-requires a non-empty password and
  `validate_forwards` keys duplicate detection on the port alone.
- `listeners.open_listener` always creates a `SOCK_STREAM` socket, calls
  `listen()` and relies on `accept()` loops started in `Session`.
- Profiles store the same three keys per forward.
- `_probe_all` sends `forward["password"]` unconditionally and checks only
  `response["ok"]`.

## Goals / Non-Goals

**Goals:**

- Keep one Link, one `Mux` and one session worker for TCP and UDP forwards.
- Reuse the existing stats/snapshot/event surface so the UI needs no new
  plumbing beyond two extra fields per forward.
- Make the public/passwordless path a first-class value (`None`), not an empty
  string, so secrets and profiles stay unambiguous.

**Non-Goals:**

- Server-side behaviour, announces, or discovery of which services are public
  before probing/listing.
- Exposing local services from the phone.
- Binding outside loopback, or supporting multicast/broadcast UDP.
- A user-facing idle timeout or per-forward UDP tuning.

## Decisions

### Re-vendor the whole package instead of patching

Copy `src/rns_portfwd/` from upstream `a76e162` over `app/rns_portfwd/`,
including the new `datagrams.py`, and update `vendor/VENDOR.md`. Verify with
`diff -r` (the only expected difference is `__pycache__`). Rationale: a partial
port would leave the tree matching no commit and break the re-vendor procedure.
Note: `c2400eb` recorded in `VENDOR.md` no longer exists upstream (history was
rewritten); `b18a2b9` is its equivalent and `a76e162` is its successor.

Alternative rejected: copying only `datagrams.py` plus hand-editing `mux.py`,
`streams.py` and `protocol.py`.

### Forward model: dict with `protocol` and nullable password

`{name: str, local_port: int, protocol: "tcp"|"udp", password: str|None}`.
`password is None` means public. `validate_forward` normalises `None` and `""`
to `None` (a legacy profile could only contain `""` if hand-seeded, so this is
safe) and rejects any protocol outside `{"tcp", "udp"}` with the existing
`config`/`protocol` error field. `validate_forwards` keys duplicates on
`(protocol, port)`.

Alternative rejected: a separate `public: bool` flag, which would allow the
contradictory states "public with password" and "private without password".

### Listener opening branches on protocol

`open_listener` keeps the single entry point and selects `SOCK_DGRAM` for UDP;
`_listen()` (and therefore `accept()`) is TCP-only. UDP sockets get
`settimeout(0.5)` and reuse the same IPv4-first, IPv6-same-port, degrade-to-v4
logic and the same `<1024` rejection. `BoundListener` gains a `protocol`
property so `Session` can pick `accept()` vs datagram receive per socket.

`SO_REUSEADDR` is set for TCP only. On Linux it lets a second datagram socket
bind the same `addr:port`, which would silently defeat `port_in_use` detection
and let another app steal half the datagrams; UDP has no `TIME_WAIT`, so it does
not need the option. TCP and UDP on the same port still work because the
protocol differs.

Alternative rejected: a separate `open_udp_listener`, which would duplicate the
port-validation and dual-stack rules that the specs pin down.

### One datagram stream per (listener socket, local peer)

`Session` keeps `_udp_sessions[(bound.name, sock, peer)] = DatagramSession` and
`_udp_pending[key] = [bytes]`. The key includes the socket because a peer that
arrives on the IPv6 socket must be answered from the IPv6 socket. The stream is
opened lazily by a short-lived thread per new key; the first datagram is
buffered (bound 32 per key, and at most `max_streams` keys may be pending at
once) so a request/response protocol does not lose the first packet. `sendto`
with the recorded peer keeps the local process's source address stable.

`DatagramSession` is created with `owns_socket=False` on the phone: the
listener sockets are shared by every peer of a forward and are closed with the
`BoundListener`. Idle expiry is driven by the session supervisor
(`Session._supervise`, 0.2 s ticks) rather than a per-session receiver thread,
so a UDP stream costs two threads only while it is open.

Alternative rejected: upstream's `PortfwdClient` mapping, which owns a single
socket per forward and cannot express the dual-stack pair without extra code.

### Stats and cleanup ride the existing TCP paths

UDP sessions are registered in the same `_active[stream_id]` map used by TCP,
so `snapshot()`, byte accounting and `_stream_closed()` work unchanged
(`SessionBase` exposes `bytes_in`, `bytes_out`, `opened_at`, `close_reason`).
`_stream_closed` additionally purges the `_udp_sessions` entry whose session
just closed, and folds the vendor's `dropped_oversized` / `dropped_queue_full`
counters into a per-forward `dropped` field so drops are observable in the
snapshot instead of only at debug level. A stream that aborts after
`mux.install()` keeps its `on_closed` callback: the callback is the only path
that removes it from `_active`, and dropping it would leave phantom streams and
an inflated counter until the next Link cleanup. Registration checks
`session.closed` under the lock to close the install/register race.
`_cleanup_link` and `_close_listeners` clear both UDP dicts after
`mux.close_all`, so a reconnect never reuses a stale peer mapping.

### Probe decides protocol, not the OPEN error

`_probe_all` now records the whole `/probe` payload per service name, sends
`forward["password"] or ""`, and raises a new terminal error
`protocol_mismatch` (with `service`, `expected`, `actual` fields) when the
service protocol differs from the forward's. Rationale: the mismatch is a
configuration error the user can fix and it must not surface as a generic
authentication failure or as an endless reconnect loop. A service that requires
a password while the forward has none keeps the existing `auth` code plus the
hint in `messages.py`.

### Error parameters through `Terminal` and `error_text`

`Terminal` gains an optional `fields` dict; `Session._fail` publishes those
fields in the `error` and `session_state` events, and `messages.error_text`
accepts and forwards keyword parameters to `t()`. One new key,
`error.protocol_mismatch`, plus a new `protocol` field error key.

### Profiles: implicit schema v2

`_normalise` reads `protocol`, defaulting a missing or empty value to `tcp`
while keeping an unrecognised value as-is so `validate_forwards` rejects it
instead of silently connecting over TCP. `password` becomes `None` when the
stored value is missing, `null` or empty. `upsert` writes both fields. No
file-level version marker: a v1 reader that ignores unknown keys still loads
the file, and a v2 reader upgrades v1 files losslessly.

### UI: editor owns the two new choices

`ForwardEditor` gets a `Spinner` (TCP/UDP) and a `Switch` ("public, no
password") that hides and disables the password field; validating a private
forward still runs `validate_password`. The duplicate-port check uses
`(protocol, port)`. `ConnectScreen` rows show `protocol` and a translated
public badge, `ServiceListPopup` and the probe popup stop hardcoding
`"yes"/"no"` and use `discovery.auth.yes` / `discovery.auth.no`.
`SessionScreen` needs no change: forward rows already come from the snapshot.

### Tests: engine-level fakes plus one real UDP path

Unit tests cover validation, profiles, listener binding (a UDP socket can be
bound on the port a TCP listener holds) and the probe/protocol comparison with
a fake link. Integration tests add a UDP echo backend and a public service to
the harness, then exercise: UDP round trip, two-source isolation, public TCP
and UDP forwards, mixed public/private, and a TCP forward against a UDP service
failing with `protocol_mismatch`.

## Risks / Trade-offs

- [UDP MTU is smaller than TCP payloads; an oversized datagram is dropped] →
  log at debug level with the limit, never fragment; document the behaviour in
  `README.md`.
- [Two pending dicts and one thread per new peer could grow with a chatty local
  process] → per-key bound of 32 datagrams plus a global bound of `max_streams`
  pending keys, and the socket's `max_datagram` cap.
- [An idle UDP stream (60 s) closes while a local process still holds the
  socket] → the next datagram from that source opens a fresh stream; the local
  socket is untouched, so applications see a pause, not an error.
- [IPv6 peer tuples carry scope ids, so the same client can produce two keys if
  it switches families] → acceptable: two streams, same behaviour.
- [Vendor bump changes `Service`/`Forward` password typing] →
  `validation.to_vendor_forwards` passes the nullable value through; a unit
  test asserts a public forward becomes `Forward(password=None)`.
- [Android doze may delay the supervisor tick that reaps idle streams] → the
  foreground service keeps the process running; stale streams are also removed
  on link loss.
- [Re-vendor may change behaviour the existing integration tests relied on] →
  run the full suite before and after the copy; the upstream commit only adds
  features and keeps `PROTOCOL_VERSION` at 1.

## Migration Plan

1. Re-vendor, update `VENDOR.md`, run `python3 -m pytest -q` to confirm the
   existing 60 tests still pass against the new vendor.
2. Land validation/profile/listener changes (backwards compatible).
3. Land the session UDP path and public probe handling.
4. Land UI and i18n keys.
5. Rebuild the APK and check on the POCO F2 Pro: UDP round trip against a
   server-exposed UDP echo, public TCP forward, and a protocol-mismatch error.
6. Rollback: `git revert` the change; profiles written by the new version stay
   readable by the old one because the extra keys are ignored.
