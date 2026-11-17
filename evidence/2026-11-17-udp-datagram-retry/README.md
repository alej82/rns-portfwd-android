# 2026-11-17 — a UDP datagram across a Link rebuild

Device: POCO F2 Pro (`8ffbcdbf`), Android 13 / HyperOS, `arm64-v8a`.
Build: `bin/portfwd-0.3.0-arm64-v8a-debug.apk` with
`harden-udp-first-datagram`.
Change: `openspec/changes/harden-udp-first-datagram/`.

## What is verified here

| # | Check | Result |
| --- | --- | --- |
| 1 | Before the fix: the datagram that triggered the rebuild was lost with `0 bytes` | reproduced — see `transcript.txt` §1 |
| 2 | After the fix: that same datagram round-trips on the rebuilt Link | pass — `datagram-after-refresh.png`, `server-log.txt` |
| 3 | A datagram that cannot be delivered is counted and explained | pass — `drop-reported.png` |
| 4 | The reason is translated and carries no payload | pass — «Descartados 1 datagrama(s) de upub: sin enlace disponible» |

## Why the retry alone was not enough

The first attempt at this change added a bounded retry for the case "no Link
after the rebuild", but the device still showed nothing arriving. The reason
was in the path the refresh takes: it closes the old Link, which makes the
supervisor run `_cleanup_link()`, and that method cleared `_udp_pending` along
with the datagram sessions — so by the time the replacement Link existed there
was no datagram left to send, and every step reported success.

`_cleanup_link()` now leaves `_udp_pending` alone (it belongs to an opener
thread that is still waiting) and only `_close_listeners()` clears it, which
runs on `stop()` and on a terminal failure. The retry stays as the safety net
for a rebuild that outlives the first attempt.

## Evidence files

- `datagram-after-refresh.png` — session screen after the round trip: `upub`
  23/23 and the activity log with the refresh, the rebuilt Link and the stream.
- `server-log.txt` — the server side of the same two attempts: `0 bytes` before
  the fix, `23 bytes in, 23 bytes out` after it.
- `drop-reported.png` — with the test server stopped, the translated line that
  names how many datagrams were discarded and why.
- `transcript.txt` — the `adb` commands, their output and the activity log of
  each case.
