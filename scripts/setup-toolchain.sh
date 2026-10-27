#!/usr/bin/env bash
# Creates the pinned build toolchain: a venv with buildozer and a p4a checkout.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

P4A_TAG="v2024.01.21"

# buildozer 1.5.0 needs urllib.request.FancyURLopener (removed in Python 3.14)
# and p4a v2024.01.21 supports host Python 3.7-3.11 only.
HOST_PYTHON=""
for candidate in python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
        HOST_PYTHON="$candidate"
        break
    fi
done
[ -n "$HOST_PYTHON" ] || {
    echo "error: falta python3.11 o python3.10 (el python3 del sistema no sirve para buildozer 1.5.0)" >&2
    exit 1
}

if [ -x .build-venv/bin/buildozer ]; then
    .build-venv/bin/buildozer --version >/dev/null 2>&1 || {
        echo "aviso: .build-venv no funciona con este Python; lo recreo" >&2
        rm -rf .build-venv
    }
fi

if [ ! -x .build-venv/bin/buildozer ]; then
    rm -rf .build-venv
    "$HOST_PYTHON" -m venv .build-venv
    .build-venv/bin/pip install -q -U pip wheel setuptools "cython<3" buildozer==1.5.0
fi

if [ ! -d .p4a ]; then
    git clone -q --depth 1 --branch "$P4A_TAG" https://github.com/kivy/python-for-android.git .p4a
fi

echo "buildozer: $(.build-venv/bin/buildozer --version)"
echo "p4a: $(git -C .p4a describe --tags --always)"
echo "siguiente: scripts/build-apk.sh (la primera vez descarga SDK/NDK)"
