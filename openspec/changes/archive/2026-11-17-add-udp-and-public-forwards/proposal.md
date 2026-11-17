## Why

The Android client only forwards TCP services that require a password. Upstream
`rns-portfwd` now ships UDP tunneling and passwordless public services, so a
phone that only speaks the old client cannot reach a growing share of servers:
UDP-only services (DNS, game and media servers, syslog) simply have no entry
point, and a public service can only be used by inventing a fake password that
the server ignores. The vendored protocol copy here predates both features.

## What Changes

- Re-vendor `app/rns_portfwd/` from upstream commit `a76e162`: adds
  `datagrams.py`, `MSG_DGRAM`, `DatagramMessage`, the `protocol` field on
  `Service`/`Forward`, `password: str | None`, and the zero-length `OPEN`
  password. `vendor/VENDOR.md` records the new commit.
- A forward definition gains `protocol` (`tcp` or `udp`) and a nullable
  password; `None` means a passwordless public service.
- UDP forwards bind a loopback UDP listener on IPv4 and, when possible, IPv6 on
  the same port, reusing the existing port `0` and `<1024` rules and degrading
  to IPv4 only.
- One UDP stream is opened per local source and forward, lazily on first
  datagram, with bounded pending buffers, credit-aware sending, idle expiry and
  drop-instead-of-queueing behavior.
- Probing no longer requires a password: a public forward probes with an empty
  password, and a forward whose protocol differs from the server's answer fails
  with a translated error instead of silently looping.
- Listing shows each service's protocol and whether it needs a password, both
  localized.
- Profiles persist `protocol` and a missing password; existing profile files
  keep working (missing protocol means `tcp`, existing passwords stay).
- The forward editor gains a protocol selector and a "public, no password"
  switch; `--forward-udp`, `--forward-public` and `--forward-udp-public`
  equivalents exist in `tools/headless_engine.py` for tests.
- New i18n keys land in `app/assets/i18n/translations.json` and `es.json`.

## Capabilities

### New Capabilities

- `udp-forwarding`: forwarding UDP services over one multiplexed Link, the
  loopback dual-stack UDP listener, per-source session mapping, lazy open,
  idle expiry and drop semantics, and the local port rules that apply to UDP.
- `public-forwards`: defining a passwordless forward, probing it without a
  password, rejecting protocol mismatches, persisting protocol and password
  presence in profiles, and reporting `auth` in the service list.

### Modified Capabilities

<!-- openspec/specs/ is empty: this repo has no archived capabilities yet. -->

## Impact

- Vendor: `app/rns_portfwd/**` (full re-vendor from `a76e162`), `vendor/VENDOR.md`.
- Engine: `app/portfwd_android/listeners.py` (UDP sockets, per-forward
  protocol), `session.py` (protocol routing, `DatagramSession` lifecycle, probe
  and `OPEN` with an empty password, protocol mismatch errors),
  `validation.py` (optional password, `protocol` field, duplicate ports keyed
  by protocol), `profiles.py` (schema and migration), `engine.py` (forward
  passthrough, secret registration skipping public forwards), `messages.py`
  (new error codes), `i18n` catalogs.
- UI: `app/main.py` (`ForwardEditor` protocol selector and public switch,
  `ConnectScreen` rows and warnings, `ServiceListPopup` auth/protocol labels,
  log rendering of protocol mismatch).
- Tooling/tests: `tools/headless_engine.py`,
  `tests/integration/harness.py` (UDP echo and public service servers),
  `tests/unit/*` for validation, profiles, listeners and session helpers,
  `tests/integration/test_integration.py` for UDP and public forwards.
- Docs: `README.md`, `AGENTS.md` (vendor commit, UDP/public invariants).
