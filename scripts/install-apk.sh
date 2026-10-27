#!/usr/bin/env bash
# Installs the debug APK on the connected device and launches it.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERIAL="${ANDROID_SERIAL:-}"
APK="$(ls -t "$ROOT"/bin/*.apk 2>/dev/null | head -1 || true)"

[ -n "$APK" ] || { echo "error: no hay APK en bin/ (ejecuta scripts/build-apk.sh)" >&2; exit 1; }

ADB=(adb)
[ -n "$SERIAL" ] && ADB+=( -s "$SERIAL" )

"${ADB[@]}" install -r "$APK"
"${ADB[@]}" shell monkey -p io.areche.portfwd -c android.intent.category.LAUNCHER 1 >/dev/null
echo "instalado y lanzado: $APK"
