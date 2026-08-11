#!/usr/bin/env python3
"""ASCIISTREAM host-side 3-D viewer sidecar (PyVista/Qt).

run.sh launches this on the HOST, next to the dolfinx container - a GUI
cannot cross the container boundary - with the fixed contract:

    .venv-viewer/bin/python viewer_sidecar.py --watch-dir <repo>

stdin is /dev/null and stdout/stderr land in
${TMPDIR:-/tmp}/asciistream-viewer.log. The container bind-mounts the repo
at /work, so both sides share one directory and talk ONLY through files in
it (all names imported from chassis_cfd.py - single source):

  VIEWER_READY_FILE     dropped here on start, removed on exit. While it
                        exists the containerized launcher passes
                        --viz-every to the solver, enabling the periodic
                        mid-run export this window feeds on.
  VIEWER_TRIGGER_FILE   written by the dashboard's [p] hotkey; consumed
                        here. Until it appears this process is DORMANT:
                        no QApplication, no window, no Qt/VTK imports -
                        just a slow stat() poll loop.
  OUT_VIZ_MANIFEST      the solver's atomically-replaced manifest naming
                        the EXACT dataset file for each field of the
                        newest export in fields[...]["file"]. Those paths
                        are the only ones ever read: the bare
                        velocity.vtu / pressure.vtu / zones.vtu at an
                        export root are PVD-style collection INDEXES that
                        parse fine but carry no data.

On trigger a pyvistaqt BackgroundPlotter opens (Qt owns the main thread;
manifest polling runs on QTimers, never blocking the event loop): the
chassis hardware is drawn from the 'zone' cell array (DG0 tags; tag
VOL_OPEN = plain air), the flow field coloured by |u|, both live-refreshed
as new solver steps land. The solver keeps only VIZ_KEEP_DIRS export
directories and deletes older ones mid-flight, so every read tolerates
FileNotFoundError / torn piece sets by re-reading the manifest next tick.
"done": true switches the view to the final full export. Closing the
window returns to dormant; [p] re-triggers. SIGTERM/SIGINT (run.sh sends
SIGTERM on any exit, including Ctrl+C) tears Qt down and exits promptly,
removing the marker.

An OPTIONAL hardware-boundary overlay is supported: the first *.glb /
*.gltf found in the watch dir (Draco-compressed primitives included, via
trimesh + DracoPy) is drawn translucently over the chassis. No such asset
ships with the repo; when none is present the path is skipped silently.
"""

import argparse
import glob
import json
import logging
import os
import signal
import sys
import time

# File-protocol names come from the main module (its top level is
# stdlib+numpy only, so this import is cheap and container-safe).
from chassis_cfd import (OUT_VIZ_MANIFEST, VIEWER_READY_FILE,
                         VIEWER_TRIGGER_FILE, VOL_OPEN)

POLL_DORMANT_SEC = 0.5     # trigger poll cadence while dormant (one stat)
REFRESH_MS = 1000          # manifest poll cadence while the window is open
SIGNAL_TICK_MS = 200       # no-op QTimer: keeps Python bytecode running
                           # under app.exec_() so SIGTERM handlers fire
WINDOW_TITLE = "ASCIISTREAM - chassis 3-D (PyVista)"

log = logging.getLogger("viewer_sidecar")

_shutdown = False          # set by the signal handler, checked everywhere
_qt_app = None             # QApplication once created (reused per session)


def _on_signal(signum, _frame):
    """SIGTERM/SIGINT: run.sh does `kill $VIEWER_PID` then `wait` with NO
    SIGKILL escalation - a slow exit here hangs the user's terminal on
    Ctrl+C. Keep the handler minimal (no logging: not reentrancy-safe):
    flag the loops and quit the Qt event loop if one is running."""
    global _shutdown
    _shutdown = True
    if _qt_app is not None:
        try:
            _qt_app.quit()
        except Exception:
            pass


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _remove_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ------------------------------------------------------------------------------
#  Manifest-driven dataset loading (also used by visualize.py / convert.py).
#  No pyvista import at module level: the dormant sidecar must not pay the
#  VTK startup cost, and importing this module must stay near-free.
# ------------------------------------------------------------------------------

def read_manifest(watch_dir):
    """Parse OUT_VIZ_MANIFEST in watch_dir. None when absent, torn or not
    a viz manifest (the writer replaces it atomically, so a torn read only
    happens on foreign/hand-edited files - treated the same: retry)."""
    path = os.path.join(watch_dir, OUT_VIZ_MANIFEST)
    try:
        with open(path) as f:
            man = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(man, dict) or man.get("type") != "viz":
        return None
    return man


def load_field_mesh(watch_dir, fields, name):
    """One field's dataset, strictly via the manifest's
    fields[name]["file"] - NEVER a constructed path: the mid-run and final
    exports number their piece sets differently and the bare <field>.vtu
    files are dataless collection indexes. Returns a pyvista dataset or
    None (field absent, ring-buffer race, torn piece set, empty mesh);
    None always means "skip now, retry on the next manifest read"."""
    import pyvista as pv
    entry = fields.get(name) if isinstance(fields, dict) else None
    if not isinstance(entry, dict) or not entry.get("file"):
        return None
    path = os.path.join(watch_dir, entry["file"])
    try:
        mesh = pv.read(path)
    except FileNotFoundError:
        return None               # ring buffer rotated the dir away
    except Exception:
        return None               # torn/partial piece set mid-delete
    if isinstance(mesh, pv.MultiBlock):
        try:
            mesh = mesh.combine()
        except Exception:
            return None
    if mesh.n_points == 0 and mesh.n_cells == 0:
        return None               # collection index / empty export
    return mesh


def velocity_magnitude(mesh, array):
    """Attach a '|u|' point array (magnitude of `array`); None when the
    named array is missing. Handles 2-component (2d engine) and
    3-component vectors alike."""
    import numpy as np
    data = mesh.point_data.get(array)
    if data is None:
        return None
    data = np.asarray(data)
    mag = np.abs(data) if data.ndim == 1 else np.linalg.norm(data, axis=1)
    mesh.point_data["|u|"] = mag
    return mag


def load_hardware_overlay(watch_dir):
    """OPTIONAL Draco/.glb hardware-boundary asset: first readable
    *.glb/*.gltf in the watch dir, converted to a pyvista surface. The
    repo ships no such asset - absence is the normal case and is silent.
    Never raises; a broken asset costs one warning and is skipped."""
    paths = sorted(glob.glob(os.path.join(watch_dir, "*.glb"))
                   + glob.glob(os.path.join(watch_dir, "*.gltf")))
    for path in paths:
        try:
            import numpy as np
            import pyvista as pv
            import trimesh
            tm = trimesh.load(path, force="mesh")
            faces = np.asarray(getattr(tm, "faces", ()), dtype=np.int64)
            verts = np.asarray(getattr(tm, "vertices", ()), dtype=float)
            if faces.size == 0 or verts.size == 0:
                log.warning("hardware asset %s has no triangles - skipped",
                            path)
                continue
            vtk_faces = np.hstack(
                [np.full((len(faces), 1), 3, dtype=np.int64), faces]).ravel()
            mesh = pv.PolyData(verts, vtk_faces)
            log.info("hardware asset overlay: %s (%d triangles)",
                     path, mesh.n_cells)
            return mesh
        except Exception as exc:
            log.warning("could not load hardware asset %s: %s", path, exc)
    return None


# ------------------------------------------------------------------------------
#  Interactive scene
# ------------------------------------------------------------------------------

class SceneState:
    """Everything one viewer session renders + the refresh logic driven by
    a QTimer. Every failure mode in here degrades to a log line and a
    retry on the next tick - an exception must never cross into Qt."""

    def __init__(self, watch_dir):
        self.watch = watch_dir
        self.key = None            # (dir, step, done) already rendered
        self.camera_set = False
        self.overlay = load_hardware_overlay(watch_dir)
        self.overlay_added = False
        self._logged = set()       # one log line per distinct situation

    def _log_once(self, msg):
        if msg not in self._logged:
            self._logged.add(msg)
            log.info(msg)

    def _status(self, plotter, text):
        try:
            plotter.add_text(text, name="status", position="upper_left",
                             font_size=10, color="white")
        except Exception:
            pass

    def refresh(self, plotter):
        try:
            self._refresh(plotter)
        except Exception as exc:   # belt and braces: never kill the timer
            self._log_once(f"refresh skipped ({exc.__class__.__name__}: "
                           f"{exc}) - retrying")

    def _refresh(self, plotter):
        import numpy as np
        man = read_manifest(self.watch)
        if man is None:
            self._status(plotter, "waiting for solver data...\n"
                                  "start a run with ./run.sh")
            self._log_once("no viz manifest yet - waiting for the solver")
            return
        key = (man.get("dir"), man.get("step"), man.get("done"))
        if key == self.key:
            return                 # nothing new since the last render
        fields = man.get("fields") or {}
        flow = load_field_mesh(self.watch, fields, "velocity")
        zones = load_field_mesh(self.watch, fields, "zones")
        if flow is None and zones is None:
            # ring-buffer race / half-written export / empty mesh: the
            # manifest will resolve it - re-read on the next tick
            self._log_once("export not readable yet (rotating or "
                           "incomplete) - retrying")
            return

        if flow is not None:
            arr = (fields.get("velocity") or {}).get("array", "velocity")
            mag = velocity_magnitude(flow, arr)
            if mag is not None and mag.size:
                vmax = float(np.nanmax(mag))
                clim = [0.0, vmax if np.isfinite(vmax) and vmax > 0
                        else 1.0]
                # translucent in 3-D so the hardware inside stays visible;
                # the 2d engine's flat slice can be opaque
                opaque = man.get("engine") == "2d"
                plotter.add_mesh(
                    flow, scalars="|u|", cmap="jet", clim=clim,
                    opacity=1.0 if opaque else 0.5, name="flow",
                    scalar_bar_args={"title": "|u| [m/s]", "color": "white"})
                plotter.add_mesh(flow.outline(), color="#c8c8d0",
                                 name="outline")
            else:
                self._log_once(f"velocity dataset has no '{arr}' array - "
                               "flow layer skipped")

        if zones is not None:
            zarr = (fields.get("zones") or {}).get("array", "zone")
            hardware = None
            try:
                if zarr in zones.cell_data:
                    # tag VOL_OPEN is plain air; every higher tag is a
                    # component zone (drive cage, heatsinks, DIMM/PCIe/PSU
                    # blocks). Solid parts are voids in the mesh already.
                    hardware = zones.threshold(VOL_OPEN + 0.5, scalars=zarr)
                else:
                    self._log_once(f"zones dataset has no '{zarr}' cell "
                                   "array - hardware layer skipped")
            except Exception as exc:
                self._log_once(f"zone threshold failed ({exc}) - "
                               "hardware layer skipped")
            if hardware is not None and hardware.n_cells:
                plotter.add_mesh(hardware, color="#9a9aa4", opacity=0.9,
                                 name="hardware", show_scalar_bar=False)

        if self.overlay is not None and not self.overlay_added:
            try:
                plotter.add_mesh(self.overlay, color="#d0d0d8", opacity=0.5,
                                 name="hardware_asset")
                self.overlay_added = True
            except Exception as exc:
                self.overlay = None
                log.warning("hardware asset overlay failed to render: %s",
                            exc)

        done = bool(man.get("done"))
        self._status(plotter,
                     f"{'FINAL' if done else 'live'}  "
                     f"t={man.get('t', 0.0):.2f}s  "
                     f"step {man.get('step', '?')}/{man.get('steps', '?')}  "
                     f"engine={man.get('engine', '?')}  "
                     f"cells={man.get('cells', '?')}")
        if not self.camera_set:
            try:
                plotter.view_isometric()
                plotter.reset_camera()
            except Exception:
                pass
            self.camera_set = True   # after this the camera is the user's
        # only mark this export rendered once every field it advertises
        # was actually loaded; otherwise keep retrying this step
        if ((flow is not None or "velocity" not in fields)
                and (zones is not None or "zones" not in fields)):
            self.key = key
            log.info("rendered %s step %s (done=%s)",
                     man.get("dir"), man.get("step"), done)


def run_viewer_session(watch_dir):
    """One trigger -> one window. Blocks in the Qt event loop (Qt must own
    the main thread) until the window is closed or shutdown is flagged.
    All Qt/VTK imports happen here, not at module level - the dormant
    process never pays for them."""
    global _qt_app
    try:
        from pyvistaqt import BackgroundPlotter
        from qtpy.QtCore import QTimer
        from qtpy.QtWidgets import QApplication
    except Exception as exc:
        log.error("viewer stack unavailable (%s) - staying dormant", exc)
        return
    app = QApplication.instance() or QApplication([WINDOW_TITLE])
    _qt_app = app
    if _shutdown:                  # SIGTERM raced the construction
        return
    try:
        plotter = BackgroundPlotter(app=app, show=True,
                                    window_size=(1100, 780),
                                    update_app_icon=False)
    except Exception as exc:
        log.error("could not open the Qt window (%s) - staying dormant",
                  exc)
        return
    try:
        plotter.app_window.setWindowTitle(WINDOW_TITLE)
        plotter.set_background("#101014")
        # window closed by the user -> leave the event loop, back to dormant
        plotter.app_window.signal_close.connect(app.quit)

        scene = SceneState(watch_dir)
        refresh = QTimer(plotter.app_window)
        refresh.timeout.connect(lambda: scene.refresh(plotter))
        refresh.start(REFRESH_MS)
        tick = QTimer(plotter.app_window)   # keeps the interpreter running
        tick.timeout.connect(lambda: None)  # so signal handlers can fire
        tick.start(SIGNAL_TICK_MS)

        scene.refresh(plotter)              # first paint immediately
        log.info("window open - rendering from %s", OUT_VIZ_MANIFEST)
        app.exec_()
        refresh.stop()
        tick.stop()
    finally:
        try:
            plotter.close()
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ASCIISTREAM host-side PyVista viewer sidecar "
                    "(started by run.sh; see the module docstring)")
    ap.add_argument("--watch-dir", required=True,
                    help="shared work directory (the repo dir run.sh "
                         "bind-mounts into the container)")
    args = ap.parse_args(argv)
    watch = os.path.abspath(args.watch_dir)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not os.path.isdir(watch):
        log.error("--watch-dir %s is not a directory", watch)
        return 2

    marker = os.path.join(watch, VIEWER_READY_FILE)
    trigger = os.path.join(watch, VIEWER_TRIGGER_FILE)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        _atomic_write(marker, f"{os.getpid()}\n")
    except OSError as exc:
        log.error("cannot create %s (%s) - exiting", marker, exc)
        return 1
    log.info("ready: marker %s in place; dormant until the dashboard's "
             "[p] writes %s", VIEWER_READY_FILE, VIEWER_TRIGGER_FILE)
    _remove_quiet(trigger)         # a stale trigger from a previous session
                                   # must not pop a window on startup
    try:
        while not _shutdown:
            if os.path.exists(trigger):
                _remove_quiet(trigger)     # consume before opening
                log.info("trigger received - opening the viewer window")
                run_viewer_session(watch)
                if not _shutdown:
                    log.info("window closed - dormant again "
                             "([p] re-opens it)")
            elif not os.path.exists(marker):
                try:                       # self-heal: the marker is what
                    _atomic_write(marker, f"{os.getpid()}\n")   # enables
                except OSError:            # mid-run export for NEW runs
                    pass
            time.sleep(POLL_DORMANT_SEC)
    finally:
        _remove_quiet(marker)
        _remove_quiet(trigger)
        log.info("sidecar exited cleanly (marker removed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
