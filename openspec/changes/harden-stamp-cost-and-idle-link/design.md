## Context

Both problems were observed on the POCO F2 Pro against a real server
(`rns-portfwd-server --allow-identity … --link-stamp-cost 8`) on 2026-11-17:

- Typing 40 into "Cost (0-40)" left the connect screen on "Opening Link…"
  indefinitely; the server log showed no request at all until the attempt was
  cancelled and the cost reset to 8.
- After a successful connect, a forward attempted ~23 s later was refused: the
  server logged `link closed` and the app logged
  `stream closed: link closed (0 bytes in, 0 bytes out)` right before
  reconnecting. `server.py` starts a `threading.Timer(auth_timeout)` when the
  Link is established and cancels it only in `_complete_open` (the first
  successful stream `OPEN`); a `/probe` and RNS keepalives do not cancel it.

Constraints that shape the fix:

- `app/rns_portfwd/` is vendored and never edited, so `MAX_LINK_STAMP_COST`
  (40) stays as the vendor's wire-level bound; the app needs its own cap.
- Stamp generation happens inside `RNS.Link(...)` creation, in the session
  worker thread, with no progress callback. `configure_link_stamping()` runs
  immediately before it and already knows the effective cost, so that is the
  only honest place to report progress from.
- One session at a time; the supervisor thread owns reconnects
  (`_link_lost` → `_establish(initial=False)`), and `_wait_link()` is the single
  rendezvous where a local connection waits for a usable Link.

## Goals / Non-Goals

**Goals:**

- Make an unusable stamp cost unreachable from the UI and from a stored
  profile, and make the wait visible whenever a stamp is being generated.
- Guarantee that the first stream of a session lands on a Link the server has
  not expired, without adding a background keepalive or extra traffic.

**Non-Goals:**

- Keep-alive pings. RNS already keeps the Link alive at the transport level and
  the server timer only guards the *first* stream, so periodic traffic would
  buy nothing.
- Changing the vendor's 0..40 bound, the CLI or the wire protocol.
- Refreshing Links that already carried a stream.

## Decisions

### App cap of 16 for a custom cost

Cost N requires ≈2^N stamp attempts. On this device a single core computes
roughly 1.5 M attempts/s, so cost 16 ≈ 65k attempts (<0.1 s), cost 20 ≈ 1 M
(≈1 s), cost 24 ≈ 16 M (≈10 s), cost 30 ≈ 10^9 (≈10 min), cost 40 ≈ 10^12
(≈8 days). 16 keeps the worst case invisible; anyone needing more can still be
served by `auto`, which follows the server's advertised cost without any app
cap. `MAX_STAMP_COST = 16` lives in `portfwd_android.validation` next to the
validator, and the i18n label/hint take it as a `{max}` placeholder so the text
can never drift from the value.

A stored profile with a higher custom cost is not accepted verbatim:
`ProfileStore._normalise_stamp` already falls back to `("auto", None)` when
validation fails, so an out-of-range value degrades to automatic instead of
breaking the profile or resurrecting the hang.

### `stamping` session state fed by the stamp funnel

`rns_ops.open_link(..., on_stamp=…)` calls the callback with the resolved cost
right after `configure_stamping()` and before the destination/Link are created.
`Session` turns it into `_set_state("stamping", stamp_cost=cost)`, so the
existing event queue carries it to the UI; `messages.state_text()` renders it
with the cost. `auto` profiles benefit because the cost comes from
`configure_link_stamping` itself rather than from a duplicate parse.

Alternative rejected: deriving the state in the UI from `stamp_mode`. It cannot
know the advertised cost, and it would show generation when nothing is stamped
(`stamp_mode=auto` on a server that advertises none).

### Idle-link refresh at the Link handoff

`Session._wait_link()` — used by both the TCP accept path and the UDP opener —
already waits for `link_ready`. It now also checks whether the current Link has
ever been handed to a stream (`_link_used`) and whether it is older than
`idle_refresh` (default 20 s, 10 s of margin under the server default). When
both hold, the Link is torn down once and the waiter keeps waiting: the
supervisor rebuilds the Link, `_wait_link()` returns the new mux and the
connection is served normally.

Details that matter:

- `_link_used` is set when `_wait_link()` hands the mux over, not when a stream
  finally opens, because that is the last moment the session controls; after
  the first `OPEN` the server timer is gone for good.
- A `_refreshing` flag makes the refresh idempotent: concurrent connections see
  `link_ready == False` and simply wait instead of tearing down twice.
- The refresh reuses the existing reconnect path (`_link_lost` → `_supervisor`),
  so stamping, identification, re-resolution and retry backoff behave exactly
  as they already do.
- Making the threshold a constructor parameter keeps the production default
  while letting the integration harness use `--auth-timeout 3` on the server and
  `--idle-refresh 1` on the engine, so the test asserts the same code path in
  ~5 s instead of 40 s.

## Risks / Trade-offs

- **Extra Link churn for an idle session.** A session that connects and sits
  unused rebuilds its Link every ~20 s *only while nobody connects*; each
  rebuild happens when a connection arrives, not on a timer, so an untouched
  session is silent. After the first stream nothing is refreshed at all.
- **A refused forward during the refresh.** The connection waits up to
  `connect_timeout` for the new Link; if the shared instance is also down it is
  refused as "link unavailable", which is the pre-existing behaviour.
- **Cap too low for a hostile server.** A server advertising a cost above 16
  still works in `auto`; only the manual override is bounded.
