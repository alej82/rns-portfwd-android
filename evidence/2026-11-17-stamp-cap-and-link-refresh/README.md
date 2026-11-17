# 2026-11-17 — stamp cost cap and idle-Link refresh

Device: POCO F2 Pro (`8ffbcdbf`), Android 13 / HyperOS, `arm64-v8a`.
Build: `bin/portfwd-0.2.0-arm64-v8a-debug.apk`, `rns==1.6.0`.
Shared instance: Columba (`network.columba.app`) on `127.0.0.1:37428`.
Test server: desktop `rns-portfwd-server`, hash
`5e5c054ec00d76dc6cb32d5d556d32ca`, `--allow-identity` for this device and
`--link-stamp-cost 8`, default `--auth-timeout 30`.
Profile «Allow-listed»: `identify: true`, `stamp_mode: "cost"`,
`stamp_cost: 8.0`.

## What is verified here

| # | Check | Result |
| --- | --- | --- |
| 1 | The custom cost field reads «Coste (0-16)» and keeps 8 | pass — `cost-field-0-16.png` |
| 2 | A custom cost of 17 is refused, no session starts | pass — `cost-17-entered.png`, `cost-17-rejected.png` |
| 3 | A forward after ~22 s of an unused Link still works | pass — `idle-link-refresh.png` |
| 4 | The refresh is visible as a translated activity line only | pass — `idle-link-refresh.png` |
| 5 | Server side shows the stream on the `echo` service | pass — `server-log.txt` |

## Cost cap

```
# profile form, custom cost selected
Coste (0-16)
8
Automático usa el coste que anuncia el servidor; el coste personalizado llega
hasta 16.

# after typing 17 and pressing «Conectar»
El coste de stamping debe ser un número entre 0 y 16.
```

No session starts and nothing reaches the server: the previous build accepted
40 and sat on «Abriendo Link…» forever, because cost 40 means ≈2^40 stamp
attempts (days on a phone).

## Idle-Link refresh

The Link was established at 19:48:40 and a forward was attempted at 19:49:02,
i.e. 22 s later, inside the 20 s refresh window and still 8 s before the
server's 30 s authentication timeout.

```
# activity log (idle-link-refresh.png)
Renovando el enlace inactivo
Link perdido; reconectando
Abierto echo en el puerto 2222
stream echo@2222/1 closed: both directions closed (13 bytes in, 13 bytes out, 0.0s)
Cerrado echo: both directions closed (13 entrada, 13 salida, 0.038s)

# device shell
$ adb shell 'printf final-refresh | toybox nc -w 10 127.0.0.1 2222'
final-refresh
```

The session rebuilt the Link before serving the connection, so the forward
never raced the server's expiry. Only the translated log line is shown; the
English diagnostic was demoted to `debug` after the first run showed it in the
activity log.

## Release build

The same code was rebuilt as `bin/portfwd-0.3.0-arm64-v8a-debug.apk`
(`versionName=0.3.0`, `versionCode=1024300`) and reinstalled over the 0.2.0
build without losing the profile; «Ajustes» reports «Versión 0.3.0». The
screenshots above were taken with the 0.2.0 build of the same commit content.

## Behaviour worth knowing

The vendored server closes any Link that has not opened a stream within
`--auth-timeout` (30 s default), and neither `/probe` nor Reticulum keepalives
cancel that timer. A session that is connected but never used therefore
reconnects about every 30 s; that is upstream behaviour and predates this
change. Once any stream has opened the timer is cancelled for good, and the
refresh path never touches a used Link.
