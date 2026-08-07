#!/usr/bin/env bash
# Build homelab.HelloApplet into HelloApplet.jar.
#
# CheerpJ runs a Java 8 runtime, and applets do not load at all under Java 11+,
# so this always builds with a JDK 8 compiler. If one is not on PATH it fetches
# Temurin 8 into ~/.cache/homelab-applet and reuses it on later builds.

set -euo pipefail

cd "$(dirname "$0")"

CACHE="${HOME}/.cache/homelab-applet"
JAR="HelloApplet.jar"

is_jdk8() {
    [ -x "$1" ] && "$1" -version 2>&1 | grep -q '^javac 1\.8\.'
}

find_javac() {
    # 1. javac already on PATH, if it happens to be a JDK 8.
    if command -v javac >/dev/null 2>&1 && is_jdk8 "$(command -v javac)"; then
        command -v javac
        return 0
    fi

    # 2. JAVA_HOME, if it points at a JDK 8.
    if [ -n "${JAVA_HOME:-}" ] && is_jdk8 "${JAVA_HOME}/bin/javac"; then
        echo "${JAVA_HOME}/bin/javac"
        return 0
    fi

    # 3. A Temurin 8 we downloaded on a previous run.
    local cached
    cached=$(find "$CACHE" -maxdepth 3 -type f -name javac 2>/dev/null | head -n 1 || true)
    if [ -n "$cached" ] && is_jdk8 "$cached"; then
        echo "$cached"
        return 0
    fi

    return 1
}

fetch_jdk8() {
    echo "==> no JDK 8 found, downloading Temurin 8 (~100 MB) into ${CACHE}" >&2
    mkdir -p "$CACHE"
    curl -sSL -o "${CACHE}/temurin8.tar.gz" \
        "https://api.adoptium.net/v3/binary/latest/8/ga/linux/x64/jdk/hotspot/normal/eclipse"
    tar xzf "${CACHE}/temurin8.tar.gz" -C "$CACHE"
    rm -f "${CACHE}/temurin8.tar.gz"
}

JAVAC=$(find_javac || true)
if [ -z "${JAVAC}" ]; then
    fetch_jdk8
    JAVAC=$(find_javac) || { echo "error: still no JDK 8 after download" >&2; exit 1; }
fi
JAR_TOOL="$(dirname "$JAVAC")/jar"

echo "==> javac: ${JAVAC} ($("$JAVAC" -version 2>&1))"

rm -rf build
mkdir -p build

echo "==> compiling src/homelab/*.java"
"$JAVAC" -Xlint:all -d build src/homelab/*.java

echo "==> packaging ${JAR}"
"$JAR_TOOL" cf "$JAR" -C build .

echo "==> done: ${JAR} ($(wc -c < "$JAR") bytes)"
echo "    now run ./serve.sh and open http://localhost:8080/"
