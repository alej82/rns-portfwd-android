## 1. Retry the pending datagrams

- [x] 1.1 Track Link rebuilds in `Session`: bump a monotonic `_rebuilds` counter inside `_refresh_idle_link()` next to the existing `link_refresh` publish, and read it under the lock
- [x] 1.2 Turn the setup prefix of `_open_udp_session` (obtain the mux, wait for a slot, allocate and reserve the stream id) into at most two attempts: the second only when the first ended with `mux is None`, a rebuild happened during that attempt, and the session is still wanted
- [x] 1.3 Keep the buffered datagrams alive across the reconnect the refresh itself triggers: `_cleanup_link()` no longer clears `_udp_pending` (only `_close_listeners()`, i.e. stop or terminal failure, does), and the `finally` of the opener clears its key once the outcome is final
- [x] 1.4 Cover the retry in `tests/unit/test_session.py`: a first attempt that fails with `mux is None` after a rebuild delivers the datagram on the replacement Link; a first attempt that fails without a rebuild does not retry; a rejected open does not retry

## 2. Make every discarded datagram visible

- [x] 2.1 On the final failure path of `_open_udp_session`, add the number of buffered datagrams to `stats["dropped"]` and call `_record_reject(stats, "link refresh")` (or a more specific reason when it applies) instead of returning silently
- [x] 2.2 Add a translated activity string for the new reason in `translations.json` and `es.json` (forward name and count only, never payload or peer address) and map it in `app/main.py`
- [x] 2.3 Unit-test the counters and the event: `dropped` grows once per discarded datagram, `stream_rejected` is published with the reason, and the payload never appears in the event
- [x] 2.4 Unit-test that stopping the session during the retry does not resurrect it: `_stop`/`wanted` win and nothing is published after `stop()`

## 3. Verify and document

- [x] 3.1 `pytest -q`, `ruff check app tools tests`, `openspec validate harden-udp-first-datagram --strict`
- [x] 3.2 Note in `AGENTS.md` that a UDP datagram may be retried once across a Link rebuild and that discards are counted with a reason
- [x] 3.3 Rebuild the APK and repeat the device test that exposed this: connect a session with a UDP forward, leave it idle past `idle_refresh`, send one datagram and confirm the round trip on the rebuilt Link, or the translated reason in the activity log when it cannot be delivered
- [x] 3.4 Record the device pass in `evidence/` and add it to the index
