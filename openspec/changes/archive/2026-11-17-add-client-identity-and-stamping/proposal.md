## Why

Upstream `rns-portfwd` now ships two client-facing access controls: per-service
identity allow-lists (`--allow-identity`) and opt-in link-request stamping
(`--link-stamp-cost`). The Android client vendors `a76e162`, which predates
both, so a phone cannot reach a server that allow-lists identities, and cannot
even establish a Link to a server that requires proof-of-work. Since stamping
needs `rns>=1.6` and the app pins `rns==1.5.7`, the gap cannot be closed by
copying the vendor alone.

## What Changes

- Re-vendor `app/rns_portfwd/` from upstream `ee2068a`: adds identity
  allow-list support, `LinkRequestGuard`, `StampedDestination`,
  `configure_link_stamping`, `parse_stamp_cost` and the client probe retry.
  `vendor/VENDOR.md` records the new commit.
- **BREAKING (build)**: `buildozer.spec` moves from `rns==1.5.7` to
  `rns==1.6.0`, the first version with native link-request stamps. The whole
  feature set lands in one change; there is no 1.5.7 fallback release.
- A device Reticulum identity is created on demand in the app sandbox
  (`files/portfwd/identity.id`, `0600`) and shared by every profile. The UI
  shows its 32-hex hash and copies it to the clipboard so it can be added to a
  server allow-list.
- Identifying is **opt-in per profile** and off by default: the client stays
  anonymous unless «Identify this device» is enabled. When enabled, the Link is
  identified before any application traffic, on first connection and on every
  reconnect, and `/probe` re-identifies and retries once when the proof races
  the request.
- Each profile carries a link-stamp mode: `auto` (default, follow the server's
  advertised `stamp_cost`), `off`, or a custom cost. `configure_link_stamping`
  runs before every `RNS.Link` the app creates, including list and probe.
- A server that demands stamps while the local Reticulum cannot generate them
  fails as a terminal, translated `stamp_unsupported` error instead of an
  endless `linking` retry.
- Profiles persist `identify`, `stamp_mode` and `stamp_cost`; files written by
  previous versions keep working with anonymous, automatic defaults.
- New i18n keys land in `app/assets/i18n/translations.json` and `es.json`;
  the headless engine gains matching flags for integration tests.
- Docs: `README.md`, `AGENTS.md` and `vendor/VENDOR.md` describe the new
  options, the RNS pin and the Android stamping caveats.

## Capabilities

### New Capabilities

- `client-identity`: how the app stores, displays and sends its Reticulum
  identity, when it stays anonymous, and how an identification proof race is
  handled.
- `link-stamping`: how the app decides the link-request stamp cost, applies it
  to every Link, exposes the advanced override, and reports an incapable local
  Reticulum.

### Modified Capabilities

None. The existing `udp-forwarding` and `public-forwards` deltas are unchanged
and still apply.

## Non-goals

- No server-side work: the app never exposes services and never runs
  `--allow-identity`/`--link-stamp-cost` logic itself.
- No protocol or wire change: `PROTOCOL_VERSION` stays at 1, and the vendor
  copy is still never patched locally.
- No identity import, export or per-profile identities: one device identity,
  one file, visible only as a hash.
- No identity or stamping controls in the notification or the foreground
  service: they stay profile-scoped and reconnect-aware.
- No secret rotation UI, and no user-visible private key: only the public hash
  is ever displayed.

## Impact

- Vendored code: `app/rns_portfwd/` (re-copied verbatim), `vendor/VENDOR.md`.
- Build: `buildozer.spec` (`rns==1.6.0`), APK rebuild and device validation.
- Engine: `app/portfwd_android/rns_ops.py`, `session.py`, `engine.py`,
  `validation.py`, `profiles.py`, `paths.py`, new `identity.py`, `messages.py`.
- UI: `app/main.py` (`ConnectScreen` profile form, identity popup) and both
  i18n catalogs.
- Tests: unit suites for profiles, validation, messages, `rns_ops`, `session`;
  integration harness flags plus a new identity/stamping suite.
- Tooling/docs: `tools/headless_engine.py`, `README.md`, `AGENTS.md`.
