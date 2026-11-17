## Context

See `proposal.md` — Why. The parts of the current code that shape the approach:

- `Session._handle_udp_datagram` buffers the first datagram of a new peer in
  `_udp_pending[key]` (keyed on `(forward, listener socket, peer)`, at most
  `_MAX_UDP_PENDING = 32` datagrams and `max_streams` keys) and starts
  `_open_udp_session` on a thread.
- `_open_udp_session` calls `_wait_link()`, which is also the place where an
  unused Link older than `idle_refresh` is torn down so the supervisor rebuilds
  it. The datagram therefore pays for the rebuild, and the `finally` of the
  same method pops `_udp_pending[key]` on every failure path, including
  `mux is None` — the datagram disappears without touching `stats["dropped"]`
  or publishing anything.
- `_record_reject(stats, reason)` already exists and publishes
  `stream_rejected` with a reason; TCP uses it, UDP does not.
- The UI renders `stream_rejected` reasons through `messages.error_text`-style
  keys; unknown reasons fall back to the raw value.

## Goals / Non-Goals

**Goals:**

- The datagram that triggers a Link rebuild is delivered on the replacement
  Link, not dropped.
- Every discarded datagram is countable and explicable from the UI.

**Non-Goals:**

- Retrying datagrams whose stream was rejected by the server, or whose service
  is misconfigured: those are reported once, as today.
- Retrying on any other failure (stream limit, exhausted ids): a retry would not
  help and the reason is already specific.
- Changing the buffer bounds or adding timers.

## Decisions

### Retry inside `_open_udp_session`, bounded to one extra attempt

The failure that matters is discovered at the very start of
`_open_udp_session` and is caused by the Link being replaced. The cleanest
place to handle it is a loop over at most two attempts around the "obtain a
Link and open the stream" prefix, with the second attempt only entered when the
first failed at `_wait_link()` **and** the session is still wanted.

Alternative rejected: re-enqueue the datagram from the `finally` and let a
fresh thread try again. It re-reads the peer key, races the pending buffer and
loses the ordering of the buffered datagrams; a loop keeps the same buffer, the
same key and the same thread.

Alternative rejected: make `_wait_link()` retry by itself. It is shared with
the TCP accept path and with UDP, and its contract ("return the mux or None
after `connect_timeout`") is what the TCP path and its tests rely on.

### The pending buffer outlives the reconnect

`_cleanup_link()` used to clear `_udp_pending` along with `_udp_sessions`. A
rebuild called it from the supervisor while the datagram's opener thread was
still waiting for the replacement Link, so the datagram was destroyed before
the stream that should carry it even existed — the retry had nothing left to
retry, and the device pass showed the stream opening with `0 bytes`.

The buffer is now cleared by `_close_listeners()`, which only runs on `stop()`
and on a terminal failure: a session that is still reconnecting keeps the
datagrams, because their opener is still alive and will flush them. Datagrams
belonging to streams that were already open are still discarded with the
session, as before.

### Detect "the Link was rebuilt" instead of guessing from timing

`_refresh_idle_link()` already publishes the `link_refresh` event; the session
can also record a monotonic counter of rebuilds. The retry condition is a
rebuild having happened since the attempt started, not a wall-clock window, so
a slow shared instance does not accidentally trigger the second attempt and a
fast one does not skip it.

### Counting and reporting through the existing path

`_record_reject(stats, "link refresh")` is called with the number of datagrams
that were buffered for that peer, and `stats["dropped"]` grows by that number
before the buffer is cleared. The activity log reuses the existing
`stream_rejected` rendering, adding one translated reason; no payload, peer
address or password is included — only the forward name and the count.

## Risks / Trade-offs

- **A retry can deliver a datagram late.** If the rebuild takes longer than the
  client's own timeout, the datagram still leaves after the second attempt and
  the remote answers a request nobody is waiting for any more. That is
  acceptable for UDP (the answer is dropped by the kernel) and strictly better
  than never sending it; the counter now makes the case visible.
- **Two attempts double the setup work for a lost Link.** Each attempt is
  bounded by `connect_timeout`; they share the same buffer and the second one
  usually finds the rebuilt Link already in place.
- **The retry must not resurrect a stopping session.** Both the loop and the
  reject path re-check `self._stop` and `self.wanted`, so `Detener` still wins.
