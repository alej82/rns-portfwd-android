## 1. Custom stamp cost cap

- [x] 1.1 Add `MAX_STAMP_COST = 16.0` to `app/portfwd_android/validation.py` and use it in `validate_stamp_config()` instead of the vendor `MAX_LINK_STAMP_COST`, keeping the vendor constant untouched
- [x] 1.2 Turn `connect.stamp.cost` and `connect.stamp.hint` into `{max}`-parameterised strings in `translations.json` and `es.json`, and pass the cap from `app/main.py` for both the label and the input hint
- [x] 1.3 Update `error.stamp_cost` to mention 0..16 in both catalogs
- [x] 1.4 Confirm `ProfileStore._normalise_stamp` degrades a stored out-of-range cost to `auto` and cover it in `tests/unit/test_profiles.py`
- [x] 1.5 Cover the accepted and rejected custom costs in `tests/unit/test_validation.py`

## 2. Visible stamp generation

- [x] 2.1 Add an `on_stamp` callback to `rns_ops.open_link()` invoked with the resolved cost right after `configure_stamping()` and before the destination/Link are created, only when the cost is positive
- [x] 2.2 Publish `Session._set_state("stamping", stamp_cost=cost)` from that callback and document the state in `Session`
- [x] 2.3 Map `stamping` in `messages.state_text()` to a `session.state.stamping` string carrying `{cost}`, and add the key to both catalogs
- [x] 2.4 Render the new state on the connect screen (`transient` status) and in the session screen, passing the `stamp_cost` from the snapshot
- [x] 2.5 Unit-test that `open_link` reports the effective cost for `cost` and `auto`, and that no callback fires when the cost is zero (`tests/unit/test_rns_ops.py`)

## 3. Idle-link refresh

- [x] 3.1 Add `idle_refresh` to `Session.__init__` (default 20.0), track `_link_since`, `_link_used` and `_refreshing`, and reset them on every successful `_establish()`
- [x] 3.2 Trigger a single refresh from `_wait_link()` when an unused Link is older than the threshold, then keep waiting for the replacement instead of refusing the connection
- [x] 3.3 Reuse the existing reconnect path (`_link_lost` → `_supervise` → `_establish`) and log/publish the refresh so it is visible in the activity log
- [x] 3.4 Thread `idle_refresh` through `Engine.connect()` and `tools/headless_engine.py --idle-refresh`
- [x] 3.5 Unit-test the refresh decision: stale unused Link refreshes once, concurrent waiters share one refresh, a used Link is never refreshed
- [x] 3.6 Add `tests/integration/test_link_refresh.py` with `--auth-timeout 3` on the server and `--idle-refresh 1` on the engine: connect, wait past the server timeout, forward and assert the round trip

## 4. Verification and docs

- [x] 4.1 `pytest -q`, `ruff check app tools tests`, `openspec validate harden-stamp-cost-and-idle-link --strict`
- [x] 4.2 Document the cost cap, the `stamping` state and the idle refresh in `AGENTS.md` and `README.md`, including the server `--auth-timeout` interaction
- [x] 4.3 Rebuild the APK, reinstall and verify on the POCO: cost 17..40 is refused with the translated error, cost 8 connects showing the stamping state, and a forward after 40 s idle still succeeds
- [x] 4.4 Record the device pass in `evidence/2026-11-17-identity-stamping/README.md`
