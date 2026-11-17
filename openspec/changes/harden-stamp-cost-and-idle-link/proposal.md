## Why

Two issues surfaced during the device pass of `add-client-identity-and-stamping`:

1. The custom stamp cost field advertises and accepts the vendor maximum of 40.
   At cost 40 the client must try ≈2^40 stamp values (days on a phone), so the
   session sits in "Opening Link…" forever, nothing reaches the server and the
   user gets no feedback at all. The field invites the mistake: it is labelled
   "Cost (0-40)".
2. The vendored server closes any Link that has not opened a stream within
   `--auth-timeout` (default 30 s): that timer is cancelled by the first
   successful `OPEN`, not by a keepalive, and a `/probe` does not count. So a
   session that connects and is used more than 30 s later loses its Link; the
   app reconnects automatically, but the local connection that arrived in that
   window is refused and the first forward fails.

## What Changes

- **Practical custom cost cap.** The app stops offering the vendor maximum and
  caps a custom stamp cost at 16 (≈65k stamp attempts, well under a second).
  The field label, the hint, the validation error and the profile store all use
  the app cap. A stored profile whose custom cost is above the cap loads as
  `auto` instead of failing.
- **Visible stamp generation.** While the Link request stamp is being computed
  the session reports a new `stamping` state that carries the effective cost,
  rendered as a translated "Generating link stamp (cost N)…" in the connect
  screen and in the session screen. The state is emitted from the single funnel
  (`rns_ops.open_link`) with the cost that `configure_link_stamping` actually
  resolved, so `auto` profiles report the server's advertised cost too.
- **Idle-link refresh.** Before a Link is used for its first stream the app
  checks its age. A Link that has never carried a stream and is older than 20 s
  is torn down and rebuilt first, so the first forward always lands on a Link
  the server has not expired. Links that already carried a stream are never
  refreshed: the server cancels its auth timer at the first `OPEN`.
- The refresh threshold is a `Session` parameter (default 20 s) so the
  integration harness can drive the behaviour with a short server
  `--auth-timeout` instead of a 30 s test.
- Docs and tests: `AGENTS.md`, `README.md` and the evidence checklist describe
  both behaviours; unit tests cover the cap, the `stamping` state and the
  refresh decision, and an integration test proves a forward still works after
  the server's auth timeout would have expired the Link.

## Non-goals

- Changing the vendor's 0..40 bound, the CLI or the protocol.
- Background keepalive traffic; the refresh happens when a connection arrives.
- Refreshing Links that already carried a stream.

## Impact

- Affected specs: `link-stamping` (modified), `link-liveness` (added).
- Affected code: `app/portfwd_android/validation.py`, `rns_ops.py`,
  `session.py`, `engine.py`, `messages.py`, `app/main.py`,
  `app/assets/i18n/{translations,es}.json`, `tools/headless_engine.py`,
  `tests/unit/*`, `tests/integration/*`.
- No vendor change, no new dependency, no change to the wire protocol.
