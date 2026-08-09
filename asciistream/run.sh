#!/usr/bin/env bash
# ==============================================================================
#  ASCIISTREAM run.sh - zero-setup launcher
#
#  Pulls the official dolfinx container (first run only), mounts this
#  directory at /work and starts the TUI. Everything - gmsh, FEniCSx/dolfinx,
#  Open MPI, the solver workers - runs inside that one container: the TUI
#  launcher spawns its own `mpiexec -n N` worker pool IN the container, so
#  the host needs no MPI, no Python packages - just Podman or Docker.
#
#    ./run.sh                    interactive wizard + live dashboard
#    ./run.sh --write-config     regenerate the example server_configs.json
#    ./run.sh --worker ...       scripted single-rank solve (multi-rank:
#                                run mpiexec in the container - see README)
#
#  Container engine: rootless Podman is preferred (no daemon, no root, no
#  docker-group socket issues), Docker is the fallback. Force one with:
#    ENGINE=docker ./run.sh
# ==============================================================================
set -euo pipefail

IMAGE="docker.io/dolfinx/dolfinx:stable"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }

# --- host platform ------------------------------------------------------------
# x86-64 and ARM64 hosts (Apple M-series, Graviton, ...) both run natively:
# the image is multi-arch - the registry manifest for dolfinx/dolfinx:stable
# publishes linux/amd64 AND linux/arm64 (verified 2026-08) - and the engine
# picks the matching variant on pull automatically; no --platform flag needed.
OS="$(uname -s)" ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)  ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) printf 'run.sh: note: CPU architecture %s - the image ships only\n' \
              "$ARCH" >&2
       printf '  amd64/arm64, so the engine may fall back to emulation.\n' >&2
       ;;
esac

# --- pick a container engine --------------------------------------------------
if [[ -n "${ENGINE:-}" ]]; then
    command -v "$ENGINE" >/dev/null 2>&1 || die "ENGINE=$ENGINE not found in PATH"
elif command -v podman >/dev/null 2>&1; then
    ENGINE=podman
elif command -v docker >/dev/null 2>&1; then
    ENGINE=docker
else
    die "neither podman nor docker found - install one of them first
       Fedora/RHEL:   sudo dnf install podman
       Debian/Ubuntu: sudo apt install podman"
fi

if ! "$ENGINE" info >/dev/null 2>&1; then
    if [[ "$OS" == Darwin ]]; then
        die "$ENGINE is installed but its VM is not running. On macOS start it
       first:  podman machine start   (or open Docker Desktop), then re-run."
    fi
    if [[ "$ENGINE" == docker ]]; then
        die "docker is installed but not usable (daemon down, or permission
       denied on /var/run/docker.sock). Rootless podman avoids both:
       sudo dnf install podman   (then just re-run ./run.sh)"
    fi
    die "$ENGINE is installed but '$ENGINE info' failed"
fi

# --- image (pull once, reuse afterwards) --------------------------------------
if ! "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1; then
    echo " [run.sh] pulling $IMAGE (first run only, ~2 GB)..."
    "$ENGINE" pull "$IMAGE"
fi

# --- run ----------------------------------------------------------------------
# -t only when we actually have a terminal, so piped/scripted use still works.
# :z relabels the mount for SELinux hosts (Fedora); harmless elsewhere.
# COLORTERM=truecolor lets rich use the 24-bit CFD colormap.
tty_flags=(-i)
[[ -t 0 && -t 1 ]] && tty_flags=(-it)

exec "$ENGINE" run --rm "${tty_flags[@]}" \
    -v "$DIR":/work:z -w /work \
    -e COLORTERM=truecolor \
    "$IMAGE" python3 chassis_cfd.py "$@"
