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

# --- host viewer sidecar (optional) -------------------------------------------
# The PyVista/Qt 3-D viewer runs on the HOST, next to the container - a GUI
# cannot cross the container boundary. Launch contract (the viewer module is
# a separate component; run.sh only provides the seam):
#
#     "$DIR/.venv-viewer/bin/python" "$DIR/viewer_sidecar.py" --watch-dir "$DIR"
#
# The sidecar is SKIPPED - leaving the containerized TUI exactly as before -
# when any of these hold:
#   * ASCIISTREAM_VIEWER=0 (or false/no/off): explicit opt-out
#   * scripted modes (--worker / --write-config passthrough)
#   * .venv-viewer/ is not provisioned (run ./setup_host_viewer.sh first)
#   * viewer_sidecar.py does not exist (viewer not shipped yet)
#
# The container can no longer be exec'd (exec replaces the shell, so nothing
# would remain to reap the sidecar): it now runs as a normal child, its exit
# status is captured and propagated, and the EXIT/INT/TERM traps below make
# sure Ctrl+C never orphans a Qt process.
VIEWER_PID=""
cleanup() {
    if [[ -n "$VIEWER_PID" ]] && kill -0 "$VIEWER_PID" 2>/dev/null; then
        kill "$VIEWER_PID" 2>/dev/null || true
        wait "$VIEWER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

want_viewer=1
case "${ASCIISTREAM_VIEWER:-1}" in
    0|false|no|off) want_viewer=0 ;;
esac
for arg in "$@"; do
    case "$arg" in
        --worker|--write-config) want_viewer=0 ;;
    esac
done

VIEWER_PY="$DIR/.venv-viewer/bin/python"
VIEWER_APP="$DIR/viewer_sidecar.py"
if (( want_viewer )) && [[ -x "$VIEWER_PY" && -f "$VIEWER_APP" ]]; then
    VIEWER_LOG="${TMPDIR:-/tmp}/asciistream-viewer.log"
    "$VIEWER_PY" "$VIEWER_APP" --watch-dir "$DIR" \
        </dev/null >"$VIEWER_LOG" 2>&1 &
    VIEWER_PID=$!
    printf ' [run.sh] host viewer sidecar started (pid %s, log %s)\n' \
           "$VIEWER_PID" "$VIEWER_LOG" >&2
elif (( want_viewer )) && [[ -f "$VIEWER_APP" && ! -x "$VIEWER_PY" ]]; then
    printf ' [run.sh] note: 3-D viewer available but .venv-viewer/ is not\n' >&2
    printf '   provisioned - run ./setup_host_viewer.sh to enable it.\n' >&2
fi

# --- run ----------------------------------------------------------------------
# -t only when we actually have a terminal, so piped/scripted use still works.
# :z relabels the mount for SELinux hosts (Fedora); harmless elsewhere.
# COLORTERM=truecolor lets rich use the 24-bit CFD colormap.
tty_flags=(-i)
[[ -t 0 && -t 1 ]] && tty_flags=(-it)

rc=0
"$ENGINE" run --rm "${tty_flags[@]}" \
    -v "$DIR":/work:z -w /work \
    -e COLORTERM=truecolor \
    "$IMAGE" python3 chassis_cfd.py "$@" || rc=$?
exit "$rc"
