# Vendored `rns-portfwd`

- Upstream: `rns-portfwd`
- Commit: `c2400eb` (`chore: license under GPL-3.0-or-later`)
- Version: `0.1.0`
- License: GPL-3.0-or-later
- Local path: `app/rns_portfwd/`
- Upstream path: `src/rns_portfwd/`
- Patches: **none**

## Files

```
__init__.py  auth.py  cli_client.py  cli_server.py  client.py  config.py
constants.py credits.py  discovery.py  errors.py  link_manager.py  logging.py
mux.py  protocol.py  registry.py  server.py  streams.py
```

`app/rns_portfwd/` is copied verbatim. The app only imports it; it never
modifies it. The mobile orchestration (shared-instance preflight, state
machine, listeners, profiles, UI) lives in `app/portfwd_android/`.

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
```

Then update the commit above, run `python3 -m pytest -q`, and rebuild the APK.
The app pins `rns==1.5.7`; a protocol update that needs a different RNS version
must be validated on the reference device before changing the pin.

## License

Because the APK contains this GPL-3.0-or-later code, the whole app is
GPL-3.0-or-later. See `LICENSE`.
