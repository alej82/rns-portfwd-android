# Vendored `rns-portfwd`

- Upstream: `rns-portfwd`
- Commit: `ee2068a` (`feat: add opt-in link-request stamping`)
- Version: `0.2.0`
- License: GPL-3.0-or-later
- Local path: `app/rns_portfwd/`
- Upstream path: `src/rns_portfwd/`
- Patches: **none**

## Files

```
__init__.py  auth.py  cli_client.py  cli_server.py  client.py  config.py
constants.py  credits.py  datagrams.py  discovery.py  errors.py
link_manager.py  logging.py  mux.py  protocol.py  registry.py  server.py
streams.py
```

`app/rns_portfwd/` is copied verbatim. The app only imports it; it never
modifies it. The mobile orchestration (shared-instance preflight, state
machine, listeners, profiles, UI) lives in `app/portfwd_android/`.

This snapshot adds two client-facing access controls on top of UDP tunneling
(`datagrams.py`, `MSG_DGRAM`) and passwordless public services
(`Service.password`/`Forward.password` are `str | None`):

- Per-service identity allow-lists (`--allow-identity SERVICE:HASH`), enforced
  on `OPEN` and `/probe`, with the same error, delay and failure accounting as
  a wrong password. Clients remain anonymous unless they identify.
- Opt-in link-request stamping (`--link-stamp-cost`, `--max-half-open`,
  `--half-open-timeout`) plus the client side: `configure_link_stamping()`,
  `parse_stamp_cost()`, `link_stamp_generation_supported()` and
  `link_stamp_validation_supported()` in `discovery.py`, and a bounded probe
  retry in `client.py`.

`PROTOCOL_VERSION` stays at 1; the wire layout is unchanged.

## RNS compatibility

- Identity allow-lists need only `Link.identify()` and
  `Destination.get_remote_identity()`, available since Reticulum 1.5.
- Link stamping is feature-gated to `rns>=1.6`: validation needs `RNS.Stamps`
  and `Destination.set_link_cost()`, generation needs
  `Transport.ALWAYS_STAMP_LRS`/`LR_COST`. The half-open limits work on 1.5.x.
- The app pins `rns==1.6.0` so both features are usable. On Android, stamp
  generation runs through `Stamps.job_android` (forked workers), so any change
  to the pin must be re-validated on the reference device.

History note: the previous pin `c2400eb` (`chore: license under
GPL-3.0-or-later`) no longer exists upstream because the history was
rewritten; `b18a2b9` carries the same change and is its direct predecessor.

## Re-vendor procedure

From the directory that contains both repos:

```bash
cd rns-portfwd
git fetch --tags
git checkout <commit>
cd ../rns-portfwd-android
rm -rf app/rns_portfwd
cp -a ../rns-portfwd/src/rns_portfwd app/
find app/rns_portfwd -name __pycache__ -type d -prune -exec rm -rf {} +
diff -r -x __pycache__ ../rns-portfwd/src/rns_portfwd app/rns_portfwd
```

Then update the commit above, run `python3 -m pytest -q`, and rebuild the APK.
A protocol update that needs a different RNS version must be validated on the
reference device before changing the pin.

## License

Because the APK contains this GPL-3.0-or-later code, the whole app is
GPL-3.0-or-later. See `LICENSE`.
