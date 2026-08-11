#!/usr/bin/env bash
# ==============================================================================
#  ASCIISTREAM setup_host_viewer.sh - host-side viewer venv bootstrap
#
#  The CFD solver runs inside the dolfinx container (see run.sh). The
#  OPTIONAL PyVista/Qt 3-D viewer sidecar runs on the HOST - a GUI cannot
#  cross the container boundary - and needs its own Python environment.
#  This script provisions it:
#
#      ./setup_host_viewer.sh        ->  .venv-viewer/
#
#  It also carries the host regression suite's dependencies (numpy, rich,
#  pytest), so after provisioning you can run:
#
#      .venv-viewer/bin/python -m pytest tests/ -q
#
#  Interpreter: Homebrew CPython 3.12. vtk/pyvista/PyQt5 publish no
#  CPython 3.14 wheels, so the macOS system/default python3 (3.14) cannot
#  host this stack. Install the interpreter with:  brew install python@3.12
#
#  Idempotent: safe to re-run. A stamp file records the interpreter version
#  and the sha256 of requirements-viewer.txt; when both still match, the
#  script exits immediately without touching the venv. Changing either the
#  requirements file or the interpreter re-provisions automatically.
# ==============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv-viewer"
REQS="$DIR/requirements-viewer.txt"
STAMP="$VENV/.provisioned"
PY312="/opt/homebrew/opt/python@3.12/bin/python3.12"

die() { printf 'setup_host_viewer.sh: %s\n' "$*" >&2; exit 1; }

[[ -f "$REQS" ]] || die "requirements-viewer.txt not found next to this script"

# --- locate Homebrew CPython 3.12 --------------------------------------------
# Apple Silicon keg path first; fall back to wherever brew says the keg is
# (Intel Macs use /usr/local). Anything else is a hard, actionable error.
if [[ ! -x "$PY312" ]]; then
    if command -v brew >/dev/null 2>&1; then
        prefix="$(brew --prefix python@3.12 2>/dev/null || true)"
        PY312="$prefix/bin/python3.12"
    fi
    [[ -n "${prefix:-}" && -x "$PY312" ]] || die "Homebrew python 3.12 not found.
       The viewer stack (vtk/pyvista/PyQt5) ships no CPython 3.14 wheels,
       so the system python3 cannot be used. Install the interpreter:
           brew install python@3.12
       then re-run:
           ./setup_host_viewer.sh"
fi

# --- idempotence stamp: interpreter version + requirements fingerprint --------
want="$("$PY312" -V 2>&1) $(shasum -a 256 "$REQS" | cut -d' ' -f1)"
if [[ -f "$STAMP" && -x "$VENV/bin/python" ]] \
        && [[ "$(cat "$STAMP")" == "$want" ]]; then
    echo " [setup_host_viewer] $VENV already provisioned - nothing to do."
    exit 0
fi

# --- create / refresh the venv ------------------------------------------------
# Reuse an existing venv only when its interpreter is still the same 3.12
# (a brew upgrade or a half-built venv gets rebuilt from scratch).
have="$("$VENV/bin/python" -V 2>&1 || true)"
if [[ "$have" != "$("$PY312" -V 2>&1)" ]]; then
    echo " [setup_host_viewer] creating venv at $VENV ($("$PY312" -V 2>&1))..."
    "$PY312" -m venv --clear "$VENV"
else
    echo " [setup_host_viewer] reusing venv at $VENV ($have)..."
fi

echo " [setup_host_viewer] installing the viewer stack (vtk/pyvista/Qt are" \
     "large wheels - first run can take a few minutes)..."
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$REQS"

# --- import smoke check: catch a broken wheel here, not at viewer launch ------
"$VENV/bin/python" - <<'EOF'
import importlib
for m in ("numpy", "rich", "vtk", "pyvista", "pyvistaqt", "PyQt5.QtCore",
          "meshio", "DracoPy", "trimesh", "pytest"):
    importlib.import_module(m)
print(" [setup_host_viewer] import smoke check OK")
EOF

printf '%s' "$want" > "$STAMP"
echo " [setup_host_viewer] done - viewer venv ready at $VENV"
echo " [setup_host_viewer] run the host regression suite with:"
echo "     $VENV/bin/python -m pytest tests/ -q"
