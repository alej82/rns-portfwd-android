#!/usr/bin/env bash
# Builds the debug APK with the pinned toolchain.
#
# Pin summary:
#   buildozer 1.5.0 (installed in .build-venv)
#   python-for-android v2024.01.21 (commit 957a3e5, Python 3.11.5)
#   Android target API 33, min API 24, NDK 25b
#   arm64-v8a only

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEFAULT_JAVA="$HOME/.local/jdk17"
[ -x "$DEFAULT_JAVA/bin/javac" ] || DEFAULT_JAVA="/usr/lib/jvm/java-17-openjdk"
export JAVA_HOME="${JAVA_HOME:-$DEFAULT_JAVA}"
export PATH="$ROOT/.build-venv/bin:$JAVA_HOME/bin:$PATH"
export VIRTUAL_ENV="$ROOT/.build-venv"

BUILDOZER="$ROOT/.build-venv/bin/buildozer"
[ -x "$BUILDOZER" ] || { echo "error: falta $BUILDOZER (crea el venv con scripts/setup-toolchain.sh)" >&2; exit 1; }
[ -d "$ROOT/.p4a" ] || { echo "error: falta .p4a (ejecuta scripts/setup-toolchain.sh)" >&2; exit 1; }

echo "buildozer: $("$BUILDOZER" --version)"
echo "p4a: $(git -C "$ROOT/.p4a" describe --tags --always)"
echo "JAVA_HOME: $JAVA_HOME"
echo "API: 33, minapi: 24, NDK: 25b, arch: arm64-v8a"

exec "$BUILDOZER" android debug "$@"
