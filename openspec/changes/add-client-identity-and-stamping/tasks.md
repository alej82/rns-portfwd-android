## 1. Vendor and build baseline

- [x] 1.1 Re-vendor `app/rns_portfwd/` from upstream `ee2068a` (copy `src/rns_portfwd`, drop `__pycache__`) and record commit, version, new modules and the stamping/allow-list notes in `vendor/VENDOR.md`
- [x] 1.2 Verify the copy is byte-identical apart from `__pycache__` with `diff -r` against the upstream worktree
- [x] 1.3 Bump `buildozer.spec` to `rns==1.6.0` and confirm no other pin mentions `1.5.7` (`README.md`, `VENDOR.md`)
- [x] 1.4 Run `pytest -q` and `ruff check app tools tests` with the new vendor and confirm the app still starts on the desktop harness

## 2. Device identity storage

- [x] 2.1 Add `identity_path()` to `app/portfwd_android/paths.py` pointing at `config_dir()/identity.id`
- [x] 2.2 Add `app/portfwd_android/identity.py` with lazy `ensure_identity()`, `load_identity()` and `identity_hash()`; create the file with `0600`, reuse it afterwards, never expose private key material
- [x] 2.3 Cover creation, reuse and hash format in `tests/unit/test_identity.py` with a temp `PORTFWD_CONFIG_DIR`

## 3. Identity session wiring

- [x] 3.1 Extend `rns_ops.open_link()` with `client_identity` and call `link.identify()` inside the established callback before the event is set
- [x] 3.2 Add `rns_ops.probe_request()` that re-identifies and retries a bounded number of times when the probe answers `auth`
- [x] 3.3 Pass `client_identity` through `Session` (`_probe_all`, reconnect path) and `Engine.connect`/`_list_worker`/`_probe_worker`
- [x] 3.4 Map an exhausted probe retry to the existing terminal `auth` code and keep anonymous sessions unchanged
- [x] 3.5 Unit-test identify-before-probe, retry-once-then-fail and anonymous-by-default with an RNS fake in `tests/unit/test_rns_ops.py`

## 4. Link stamping wiring

- [x] 4.1 Extend `rns_ops.open_link()` with `server_hash`, `stamp_mode` and `stamp_cost` and call `configure_link_stamping()` before creating the destination and Link
- [x] 4.2 Wrap the vendor `ResolutionError` from stamping configuration into an app-level `StampUnsupported` and raise `Terminal("stamp_unsupported")` from `Session`
- [x] 4.3 Reconfigure stamping on every reconnect, list and probe attempt
- [x] 4.4 Add `stamp_unsupported` to `messages.error_text()` and both i18n catalogs
- [x] 4.5 Unit-test automatic, disabled, custom cost and incapable-RNS paths with the vendor helpers stubbed; assert no retry loop

## 5. Profile model and validation

- [x] 5.1 Add `identify`, `stamp_mode` and `stamp_cost` to `ProfileStore._normalise` and `upsert()` with anonymous/automatic defaults for old files
- [x] 5.2 Add `validate_stamp_mode()` and `validate_stamp_cost()` to `validation.py` (0..`MAX_LINK_STAMP_COST`, finite) and a translated config error for invalid values
- [x] 5.3 Extend `tests/unit/test_profiles.py` (defaults, round-trip, legacy file) and `tests/unit/test_validation.py` (ranges, non-numeric, conflicting mode)

## 6. Interface and catalogs

- [x] 6.1 Add the «Identify this device» switch and the «View identity» popup with hash display and clipboard copy to `ConnectScreen`
- [x] 6.2 Add the advanced stamp mode spinner (Automatic / Disabled / Custom) with a cost field enabled only for Custom
- [x] 6.3 Persist and restore all three fields in `form_state`, `restore`, `_fill`, `new_profile`, `save_profile`, `_remember_session` and `App.connect`
- [x] 6.4 Add the new keys to `app/assets/i18n/translations.json` and `es.json` with placeholders in parity and pass `i18n.validate_catalog()`
- [x] 6.5 Confirm every new string goes through `t()` and that hashes, service names and server errors stay untranslated

## 7. Integration and device verification

- [x] 7.1 Add `--identity`, `--stamp-mode` and `--stamp-cost` to `tools/headless_engine.py`
- [x] 7.2 Extend `tests/integration/harness.py` to pass `--allow-identity` and `--link-stamp-cost` to the server and the new flags to the engine (no harness change was needed: the existing `extra=[...]` arguments cover both)
- [x] 7.3 Add `tests/integration/test_identity_stamp.py`: allow-listed identity succeeds, anonymous fails with `auth`, stamping server connects with automatic cost, `--stamp-mode off` fails cleanly
- [x] 7.4 Run `pytest -q` (full suite including integration) and `ruff check app tools tests`
- [x] 7.5 Build the APK with `scripts/build-apk.sh` and confirm `rns` 1.6.0 is packaged
- [x] 7.6 Device test on `8ffbcdbf`: connect to a server with `--allow-identity` (hash copied from the app) and to a server with `--link-stamp-cost`, then reconnect after stopping Columba; record timing and any forking issue
- [x] 7.7 Capture the evidence under `evidence/` and update `evidence/README.md`
- [x] 7.8 Update `README.md` and `AGENTS.md` with the new profile options, the RNS pin and the Android stamping caveats
- [x] 7.9 Run `openspec validate add-client-identity-and-stamping --strict` and `openspec validate --all --strict`
