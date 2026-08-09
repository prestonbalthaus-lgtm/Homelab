#!/usr/bin/env bash
# ==============================================================================
#  ASCIISTREAM run.sh - zero-setup launcher
#
#  Pulls the official dolfinx container (first run only), mounts this
#  directory at /work and starts the TUI. Everything - gmsh, FEniCSx/dolfinx,
#  Open MPI, the solver workers - runs inside that one container.
#
#    ./run.sh                    interactive wizard + live dashboard
#    ./run.sh --write-config     regenerate the example server_configs.json
#    ./run.sh --worker ...       scripted single-rank solve (see README)
#
#  Container engine: rootless Podman is preferred (no daemon, no root, no
#  docker-group socket issues), Docker is the fallback. Force one with:
#    ENGINE=docker ./run.sh
# ==============================================================================
set -euo pipefail

IMAGE="docker.io/dolfinx/dolfinx:stable"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { printf 'run.sh: %s\n' "$*" >&2; exit 1; }

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
