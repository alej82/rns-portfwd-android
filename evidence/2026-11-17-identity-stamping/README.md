# 2026-11-17 — client identity and link stamping

Device: POCO F2 Pro (`8ffbcdbf`), Android 13 / HyperOS, `arm64-v8a`.
Build: `bin/portfwd-0.2.0-arm64-v8a-debug.apk`, `rns==1.6.0`.
Shared instance provider: Columba (`network.columba.app`) on `127.0.0.1:37428`.
Test server: `rns-portfwd-server` on the desktop, exposed to the phone over a
TCP interface (`172.17.3.117:4242`), `--allow-identity` bound to this device's
hash and `--link-stamp-cost 8`.

## What is verified here

| # | Check | Result |
| --- | --- | --- |
| 1 | New profile controls render in Spanish | pass — `profile-identify-stamp.png` |
| 2 | Identity file is created `0600` in the sandbox | pass — 64 bytes, `-rw-------`, `u0_a1223` |
| 3 | Hash popup shows the 32-hex identity hash | pass — `identity-hash-popup.png` |
| 4 | Copy action reports success | pass — `identity-copied.png` |
| 5 | Stamp modes are selectable | pass — `stamp-modes.png` |
| 6 | Profile restores `identify`, `stamp_mode` and `stamp_cost` | pass — `profile-identify-stamp.png` |
| 7 | `rns` 1.6.0 (with `RNS/Stamps.pyc`) is packaged in the APK | pass — see command below |
| 8 | Forward through a server with `--allow-identity` **and** `--link-stamp-cost 8` | pass — `session-listening-allow-list-stamp.png`, `server-allow-list-stamp.log` |
| 9 | Anonymous client against the same server is rejected with a visible error | pass — `anonymous-rejected.png` |
| 10 | Reconnect after the shared instance restarts (stamp and identity re-applied) | pass — `shared-instance-outage.png`, `reconnected-after-outage.png` |

## Commands and transcripts

### Identity file created with owner-only permissions

```
$ adb shell 'run-as io.areche.portfwd ls -l files/portfwd'
total 8
-rw------- 1 u0_a1223 u0_a1223  64 2026-11-17 18:59 identity.id
```

### APK payload contains Reticulum 1.6.0 and the stamp module

```
$ unzip -p bin/portfwd-0.2.0-arm64-v8a-debug.apk lib/arm64-v8a/libpybundle.so \
    | tar -tzf - | grep -E 'RNS/(Stamps|_version)\.pyc|rns-1\.6\.0\.dist-info'
_python_bundle/site-packages/RNS/Stamps.pyc
_python_bundle/site-packages/RNS/_version.pyc
_python_bundle/site-packages/rns-1.6.0.dist-info/METADATA
```

### End-to-end forward (stamped Link + allow-listed identity)

Profile «Allow-listed»: hash `5e5c054ec00d76dc6cb32d5d556d32ca`, forward
`echo` → `127.0.0.1:2222`, `identify: true`, `stamp_mode: "cost"`,
`stamp_cost: 8.0`.

```
$ adb shell input tap <Conectar>
$ adb exec-out screencap -p        # "Escuchando", "Link activo"
$ adb shell 'printf hello-stamped | toybox nc -w 8 127.0.0.1 2222'
hello-stamped

# server side (server-allow-list-stamp.log)
[INFO] server ready: 5e5c054ec00d76dc6cb32d5d556d32ca (1 services, listing disabled)
[INFO] stream echo/1 opened
[INFO] stream echo/1 closed: both directions closed (13 bytes in, 13 bytes out, 0.0s)
```

The counters in the session screen show `1 conexión · 13 entrada / 13 salida`,
and the activity log records the stream open/close from both sides
(`session-listening-allow-list-stamp.png`).

### Anonymous client is rejected with an actionable error

With «Identificar este dispositivo» switched off and the same profile:

```
$ adb shell input tap <Conectar>
# status line (anonymous-rejected.png)
El servidor rechazó la credencial. Revisa la contraseña; si el servicio usa
lista de identidades, activa «Identificar este dispositivo» en el perfil.
```

### Reconnect after the shared instance restarts

```
$ adb shell am force-stop network.columba.app
# activity log (shared-instance-outage.png)
Link perdido; reconectando
Transporte: unavailable
Reintento 1 en 1s (no_path)
Reintento 2 en 3s (no_path)

$ adb shell monkey -p network.columba.app -c android.intent.category.LAUNCHER 1
# activity log (reconnected-after-outage.png)
Transporte: connected
Escuchando / Link activo

$ adb shell 'printf after-reconnect | toybox nc -w 8 127.0.0.1 2222'
after-reconnect
# server: stream echo/1 closed: both directions closed (15 bytes in, 15 bytes out, 0.1s)
```

The new attempt re-resolved the path, generated a fresh link stamp at cost 8 and
re-sent the identity proof, so both access controls survive a reconnect.


## Issues found and fixed during the device pass

1. **Stale `rns` in the p4a install directory.** `buildozer.spec` moved to
   `rns==1.6.0`, but p4a reused
   `build-arm64-v8a/build/python-installs/portfwd/arm64-v8a` and kept the 1.5.7
   files next to a `rns-1.6.0.dist-info`, so the first APK shipped Reticulum
   **1.5.7** without `Stamps.pyc`. Fixed by deleting the old `RNS` directory and
   both `rns-*.dist-info` entries before rebuilding; the check above proves the
   new payload. Documented in `AGENTS.md` (Build notes).
2. **`scripts/build-apk.sh` picked a non-existent JDK path** on this machine
   (`/usr/lib/jvm/java-17-openjdk`), so p4a failed at `avdmanager list target`.
   The script now probes `~/.local/jdk17` and the usual
   `/usr/lib/jvm/java-17*` locations.
3. **The switch graphics overflowed the widget box** and collided with the
   screen edge. Kivy's defaulttheme switch image is 83×32; the widget was
   64×32, so the image was centred and spilled ≈10 dp on each side. It is now
   84×32 with a 12 dp inset in its row (`profile-identify-stamp.png`).
4. **A session that fails before the session screen leaves the connect screen
   stuck on «Comprobando credenciales…»**. `PortfwdApp._tick` only surfaced
   `state == "error"` when the current screen was `session`, so a credential or
   allow-list rejection (which happens at probe time, before the screen
   switches) never produced a message. `_tick` now handles the error from the
   connect screen too, and `error.auth` mentions the identity option.

## Pending

Nothing outstanding for this change. The remaining known limitation is the
stamp cost discovery: a server that enforces stamps without advertising a cost
still times out unless the profile sets a custom cost.
