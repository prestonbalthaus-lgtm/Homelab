#!/usr/bin/env bash
# CheerpJ will not run from a file:// URL -- it needs real HTTP for the jar
# fetch and for its WebAssembly runtime. This serves the folder on :8080.

set -euo pipefail

cd "$(dirname "$0")"

PORT="${1:-8080}"

if [ ! -f HelloApplet.jar ]; then
    echo "HelloApplet.jar missing -- running ./build.sh first" >&2
    ./build.sh
fi

echo "serving $(pwd) on http://localhost:${PORT}/  (ctrl-c to stop)"
exec python3 -m http.server "$PORT" --bind 127.0.0.1
