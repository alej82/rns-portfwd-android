# Smoke test on the device — 2026-11-17 20:18

Device: POCO F2 Pro (`8ffbcdbf`), Android 13 / HyperOS, `arm64-v8a`.
Build: `bin/portfwd-0.3.0-arm64-v8a-debug.apk`.
Server: desktop `rns-portfwd-server`, hash `5e5c054ec00d76dc6cb32d5d556d32ca`,
four services and `--link-stamp-cost 8`:

```
--expose            127.0.0.1:2222:echo:secret
--expose-udp        127.0.0.1:2223:uecho:secret
--expose-public     127.0.0.1:2224:web
--expose-udp-public 127.0.0.1:2225:upub
--allow-identity    echo:4b9d6a7bed219ce24e7a6b8bf99c05e1
```

Profile «UDP y público» (seeded in `files/portfwd/profiles.json`, `0600`):
`identify: true`, `stamp_mode: "cost"`, `stamp_cost: 8.0`; the four forwards
below use the same local port as the server side to keep the transcript short.

## Transcript

```
# public TCP forward
$ adb shell 'printf warmup | toybox nc -w 5 127.0.0.1 2224'
warmup

# private TCP forward with the allow-listed device identity
$ adb shell 'printf id-privada | toybox nc -w 5 127.0.0.1 2222'
id-privada

# public TCP forward again, now that the Link already carried a stream
$ adb shell 'printf web-publica | toybox nc -w 5 127.0.0.1 2224'
web-publica

# private UDP forward
$ adb shell 'printf udp-id | toybox nc -u -w 4 127.0.0.1 2223'
udp-id

# public UDP forward: first datagram dropped while the session rebuilt the Link
$ adb shell 'printf udp-pub | toybox nc -u -w 4 127.0.0.1 2225'
(no output)

# same datagram again, on the Link the previous attempt built
$ adb shell 'printf upub-retry | toybox nc -u -w 4 127.0.0.1 2225'
upub-retry
```

Server side of the same session (`server-log.txt`):

```
[INFO] stream echo/2 opened
[INFO] stream echo/2 closed: both directions closed (10 bytes in, 10 bytes out, 0.0s)
[INFO] stream web/3 opened
[INFO] stream web/3 closed: both directions closed (11 bytes in, 11 bytes out, 0.0s)
[INFO] stream uecho/4 opened
[INFO] stream uecho/4 closed: link lost (6 bytes in, 6 bytes out, 15.0s)
[INFO] stream upub/1 opened
[INFO] stream upub/1 closed: link lost (10 bytes in, 10 bytes out, 15.0s)
```

Counters on the session screen after the run (`session-serving.png`): `echo`
21/21, `uecho` 17/28, `web` 28/28, `upub` 17/17, 13 connections, 83 bytes in
and 94 out. The extra bytes come from the earlier attempts in this session,
including the one dropped datagram.

## The one failure, and why it is expected

The first datagram for `upub` was lost. The activity log of that same session
shows the sequence:

```
stream uecho@2223/5 closed: link closed (6 bytes in, 0 bytes out, 0.1s)
Cerrado upub: link closed (0 entrada, 0 salida, 0.097s)
Link perdido; reconectando
Abierto upub en el puerto 2225
```

A session that has been idle for more than 20 s rebuilds its Link before the
next connection (`harden-stamp-cost-and-idle-link`), and the object that
triggers the rebuild here is the datagram itself. UDP has no retransmission, so
if the connection arrives while that rebuild is in flight the first datagram is
dropped and the client's `nc` has nothing to print. The next datagram opens the
stream normally and the round trip works.

TCP is not affected: `_wait_link()` makes the accepted connection wait for the
replacement Link, which is what the earlier device passes measured.
