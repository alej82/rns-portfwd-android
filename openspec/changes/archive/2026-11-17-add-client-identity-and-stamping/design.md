## Context

See `proposal.md` for motivation. Constraints that shape the approach:

- The app vendors upstream `a76e162` in `app/rns_portfwd/` and never edits it;
  the client orchestration the app actually runs lives in
  `app/portfwd_android/rns_ops.py` + `session.py` + `engine.py`. The vendor's
  `PortfwdClient`/`LinkManager` are unused, so their identity support does not
  reach the app by itself.
- One session at a time (`Engine.connect()` rejects a second one), and every
  Link the app creates goes through `rns_ops.open_link`.
- Reticulum is a process singleton started once after a TCP preflight of the
  shared instance on `127.0.0.1:37428`; the session worker is the only thread
  that connects.
- `buildozer.spec` pins `rns==1.5.7`; link stamping (`RNS.Stamps`,
  `Transport.ALWAYS_STAMP_LRS`, `Destination.set_link_cost`) only exists in
  `rns>=1.6`. Locally installed Reticulum is `1.6.0`, whose only dependencies
  are `cryptography>=3.4.7` and `pyserial>=3.5`, both already in the p4a
  requirements.
- On Android, `rns` generates keystamps through
  `Stamps.job_android` → `job_android_persistent`, which forks one worker per
  core via `multiprocessing.get_context("fork")` and a `ctx.Manager()`.
- The server does not defer `/probe` (it reads `remote_identity` from the
  request handler), but it does defer an `OPEN` for up to 5 s while the
  identification proof is in flight.

## Goals / Non-Goals

**Goals:**

- Re-vendor without touching a single vendored line, and keep the app's own
  session supervisor as the only place that opens Links.
- Make identity opt-in, per profile, defaulting to anonymous, with a hash the
  user can copy into a server allow-list.
- Make stamping automatic but overridable, applied to *every* Link the app
  creates, and fail terminally and clearly when it is impossible.
- Keep the change testable on the desktop (unit + integration) before the APK.

**Non-Goals:**

- Adopting the vendor `LinkManager`/`PortfwdClient` (no stop-event
  cancellation, different reconnect policy).
- Per-profile identity files, identity import/export, or key rotation UI.
- Any server-side capability, protocol version bump or wire change.
- Tuning the device stamp cost heuristic (auto = whatever the server says).

## Decisions

### Re-vendor verbatim, instrument in `rns_ops`

Copy `src/rns_portfwd/` from `ee2068a` into `app/rns_portfwd/` and record it in
`vendor/VENDOR.md`; `diff -r` must stay empty apart from `__pycache__`. The app
consumes `configure_link_stamping`, `link_stamp_generation_supported`,
`parse_stamp_cost` and `MAX_LINK_STAMP_COST` from the vendor and implements
identity/stamping wiring in its own modules. Alternative (patching the vendor)
violates the repo invariant and breaks the re-vendor procedure.

### One device identity, shared by all profiles

New `app/portfwd_android/identity.py` with `ensure_identity(path)`,
`load_identity(path)` and `identity_hash(path)`, using `RNS.Identity()` /
`from_file` / `to_file` on
`config_dir()/identity.id` (already app-private, `0600`). The identity is
loaded lazily in the session worker, never on the UI thread, and only the hash
is ever exposed. Alternative (per-profile identities) multiplies files and UI
for a feature whose allow-list is server-side anyway.

### Identify from the established callback, not after `open_link`

`rns_ops.open_link(..., client_identity=...)` calls `link.identify(identity)`
inside the `established_callback` before setting the event, so the proof is
queued before the session sends its first `/probe`, and it is repeated for free
on every reconnect because `open_link` is called again. Alternative
(`identify()` after `open_link` returns) races the probe deterministically.

### Bounded probe retry instead of a fixed sleep

`/probe` is not deferred server-side, so a proof that arrives after the request
is answered with `auth`. `rns_ops.probe_request(...)` re-identifies and retries
up to 3 attempts with short waits (~1 s apart, bounded overall) and then
reports the authentication failure. Alternative (upstream's single
`sleep(1.0)` + retry) is a fixed guess and doubles failure accounting for
genuinely wrong passwords; the bounded loop keeps the same failure semantics
but tolerates slower paths.

### Stamping configured inside the single `open_link` funnel

`open_link` receives `server_hash`, `stamp_mode` and `stamp_cost` and calls
`configure_link_stamping()` immediately before creating the destination and the
`Link`; a `ResolutionError` from it is wrapped in an app-level
`StampUnsupported` and becomes `Terminal("stamp_unsupported")` in the session.
This guarantees session, reconnect, list and probe coverage from one place.
Alternative (call sites configure stamping) duplicates the call and misses
new ones; string-matching the vendor error instead of a typed wrapper is
rejected as fragile.

### Profile data model: additive, default-preserving

`profiles.json` gains `identify` (bool, default `False`), `stamp_mode`
(`"auto"|"off"|"cost"`, default `"auto"`) and `stamp_cost`
(`float | None`, default `None`). `ProfileStore._normalise` applies the
defaults to old files, and `upsert()` gains keyword-only parameters so existing
callers keep working. Validation mirrors the vendor: cost finite and
`0 <= cost <= MAX_LINK_STAMP_COST`.

### UI: profile form, not global settings

`ConnectScreen` gains an «Identify this device» switch, a «View identity»
button opening a popup with the hash and a copy action (Kivy `Clipboard`), and
an «Advanced» block with a stamp-mode spinner (Automatic / Disabled / Custom)
plus a cost field that is only shown/enabled for Custom. Everything is
profile-scoped, matching the opt-in-per-profile decision. Alternative (global
switch in `SettingsScreen`) cannot mix anonymous and allow-listed servers
within one app.

### Acceptance gates

`pytest -q -m "not integration"`, `ruff check app tools tests`,
`openspec validate add-client-identity-and-stamping --strict`, then
`pytest -q` (integration with local rns 1.6 and a stamping/allow-listed
server), then `scripts/build-apk.sh` + device checks on `8ffbcdbf`.

## Risks / Trade-offs

- **p4a build with `rns==1.6.0` fails or the APK misbehaves** → build right
  after the pin bump, before feature work, so the fallback (report the blocker)
  costs little; keep the vendor re-vendor and the pin bump in separate commits.
- **Android stamp generation forks worker processes inside a Kivy app with a
  foreground service** → generate only from the session worker, never the UI
  thread; validate on the device with `--link-stamp-cost`; if forking
  misbehaves, surface `stamp_failed`, do not silently retry forever.
- **`ALWAYS_STAMP_LRS`/`LR_COST` are process globals** → the app runs one
  session and reconfigures before every attempt; the value is never trusted
  across profiles or sessions.
- **The advertised cost may be missing (path response without app data)** →
  the advanced custom cost is the escape hatch, and a repeated
  `link_failed` state keeps the translated hint to check stamps.
- **Identity disclosure** → disabled by default, only the hash is displayed,
  and the key file stays inside the sandbox with `0600`.
- **Probe retry doubles auth failure accounting** for genuinely wrong
  credentials → the loop is bounded to 3 attempts and still ends in the same
  terminal `auth` error.

## Migration Plan

1. Re-vendor + `VENDOR.md` + `rns==1.6.0` pin; run unit tests and build the APK.
2. Identity: storage, `rns_ops`, session/engine, validation, profiles.
3. Stamping: `open_link` wiring, error mapping, advanced profile fields.
4. UI + i18n catalogs, then tests, docs and device evidence.
5. Rollback is per commit: reverting the pin+vendor commit restores the 1.5.7
   build, and stale profile keys are ignored because normalisation supplies
   defaults.

## Open Questions

None blocking. Device observations (how long a cost-12.5 stamp takes on the
POCO, whether forking upsets the foreground service) are recorded as evidence
and do not change the specs or the task breakdown.
