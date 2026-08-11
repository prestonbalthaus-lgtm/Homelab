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

The hardware-boundary layer is delegated to hardware_assets.py (one
entry point shared with visualize.py, so the pop-out and the PNG stay
identical): a per-profile CAD asset from assets/<profile>.glb|gltf or
assets/default.glb|gltf (Draco-compressed primitives decoded via
DracoPy), fitted to the chassis dims, else a procedural chassis built
from the manifest geometry block. See that module's docstring for the
search order, unit/placement rule and failure policy.

The streamtube layer is delegated the same way to flow_streamtubes.py
(one entry point, both renderers): velocity streamlines seeded by local
speed, swept into chassis-scaled tubes, coloured by |u| on the flow
layer's clim. ON by default for 3-D exports; ASCIISTREAM_STREAMTUBES=0
turns it off. Tracing can be expensive, so it runs on a daemon worker
thread: the refresh QTimer only records what the newest export needs and
later adds the FINISHED tube mesh - the Qt event loop never waits on
vtkStreamTracer. See that module's docstring for the seeding,
integration-scale, clipping and failure policy.
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

# File-protocol names come from the main module (its top level is
# stdlib+numpy only, so this import is cheap and container-safe).
from chassis_cfd import (OUT_VIZ_MANIFEST, VIEWER_READY_FILE,
                         VIEWER_TRIGGER_FILE, VOL_OPEN)
# stdlib-only at module level (numpy/pyvista stay lazy inside), so the
# dormant sidecar imports it for free - same rule as hardware_assets
from flow_streamtubes import (TUBE_ACTOR_NAME, streamtube_layers,
                              streamtubes_enabled)

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


# ------------------------------------------------------------------------------
#  Interactive scene
# ------------------------------------------------------------------------------

def spatial_labels(geom):
    """(points, texts) for the scene's spatial annotations.

    Pure geometry -> no pyvista, no Qt, so it is unit-testable on the host
    and shared by the live pop-out and the still-image snapshot, which
    keeps the two captioned identically.

    `geom` is manifest["geometry"]: chassis dims, the fan plane, and every
    component box already carrying the SAME label the ASCII renderer uses.
    Returns ([], []) for a missing or malformed block so a caller can just
    render without annotations rather than fail.

    Conventions: z runs front (intake, 0) to back (exhaust, L); y is
    HEIGHT. Components sharing a label - the three DIMM banks are all
    "RAM" - collapse to a single tag at their combined centroid rather
    than stacking identical labels on top of one another."""
    if not isinstance(geom, dict):
        return [], []
    try:
        W, H, L = (float(v) for v in geom["dims"])
    except (KeyError, TypeError, ValueError):
        return [], []
    if not (W > 0 and H > 0 and L > 0):
        return [], []

    pts, txt = [], []

    def put(x, y, z, text):
        pts.append([float(x), float(y), float(z)])
        txt.append(text)

    # nudged just outside the shell so the tags do not float in the flow
    pad = 0.04 * L
    put(0.5 * W, 0.5 * H, -pad, "FRONT (Intake / Drives)")
    put(0.5 * W, 0.5 * H, L + pad, "BACK (Exhaust / PSUs)")

    fan_z = geom.get("fan_z")
    if isinstance(fan_z, (int, float)) and 0.0 <= float(fan_z) <= L:
        put(0.5 * W, H, float(fan_z), "Fan Wall")

    groups = {}
    for c in geom.get("components") or []:
        if not isinstance(c, dict):
            continue
        label, box = c.get("label"), c.get("box")
        if not label or not isinstance(box, (list, tuple)) or len(box) != 6:
            continue
        try:
            vals = [float(v) for v in box]
        except (TypeError, ValueError):
            continue      # convert BEFORE inserting: setdefault would
                          # otherwise leave an empty group behind when the
                          # conversion raises, and the centroid below then
                          # divides by zero
        groups.setdefault(str(label), []).append(vals)
    for label, boxes in sorted(groups.items()):
        cx = sum(0.5 * (b[0] + b[3]) for b in boxes) / len(boxes)
        cz = sum(0.5 * (b[2] + b[5]) for b in boxes) / len(boxes)
        cy = min(max(b[4] for b in boxes), H)   # sit on top of the block
        put(cx, cy, cz, label)
    return pts, txt


class SceneState:
    """Everything one viewer session renders + the refresh logic driven by
    a QTimer. Every failure mode in here degrades to a log line and a
    retry on the next tick - an exception must never cross into Qt."""

    def __init__(self, watch_dir):
        self.watch = watch_dir
        self.key = None            # (dir, step, done) already rendered
        self.camera_set = False
        # hardware-boundary layer (per-profile asset or procedural boxes,
        # via hardware_assets): resolved lazily on the first manifest -
        # the lookup needs the manifest's geometry block, and the
        # geometry is fixed for a run so once is enough
        self.hw_layers = None
        self.hw_added = False
        # geometry is fixed for a run, so the spatial labels are placed
        # once and left alone while the field refreshes underneath them
        self.labels_added = False
        self._logged = set()       # one log line per distinct situation
        # streamtube layer (flow_streamtubes): tracing is too expensive
        # for the Qt event loop, so the refresh tick only RECORDS what
        # the newest export needs (_tube_want) and APPLIES finished
        # meshes (_tube_result); a daemon worker thread does the tracing
        # in between. Plain attribute hand-off - assignment is atomic -
        # and only the main thread ever touches the plotter.
        self.tubes_on = streamtubes_enabled()
        self._tube_want = None     # (key, man, flow copy, array, clim)
        self._tube_done = None     # key of the newest finished trace
        self._tube_result = None   # (key, layers) finished, not applied
        self._tube_thread = None

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
        # streamtubes traced since the last tick get added first: this
        # runs every tick, so a finished trace lands on screen within one
        # REFRESH_MS even when the export key has not changed
        self._pump_streamtubes(plotter)
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
                if opaque:                       # 2-D slab: no tubes (see
                    self._log_once("2-D planar export - streamtube "
                                   "layer not applicable")   # module doc)
                elif self.tubes_on:
                    # deep copy: the worker thread must never share a
                    # dataset with the actor Qt is rendering
                    self._tube_want = (key, man, flow.copy(deep=True),
                                       arr, clim)
                else:
                    self._log_once("streamtube layer disabled via "
                                   "ASCIISTREAM_STREAMTUBES")
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

        self._add_labels(plotter, man)

        if self.hw_layers is None:
            # never raises: a failure inside is one log line and []
            from hardware_assets import hardware_boundary_layers
            self.hw_layers = hardware_boundary_layers(self.watch, man)
        if self.hw_layers and not self.hw_added:
            try:
                for hw_mesh, hw_kwargs in self.hw_layers:
                    plotter.add_mesh(hw_mesh, **hw_kwargs)
                self.hw_added = True
            except Exception as exc:
                self.hw_layers = []
                log.warning("hardware boundary layer failed to render: %s",
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
        # hand a just-recorded tube request to the worker on THIS tick -
        # waiting for the next tick's pump would add a whole REFRESH_MS
        # before the tracer even starts
        self._pump_streamtubes(plotter)

    def _pump_streamtubes(self, plotter):
        """Main-thread half of the streamtube pipeline, called every
        refresh tick: put a FINISHED trace on screen, then hand the
        newest wanted export to a fresh worker thread when none is
        running. The expensive part (vtkStreamTracer + tube sweep) never
        runs here, so the Qt event loop never stalls on it; the worker
        never touches the plotter, so rendering state stays single-
        threaded. Failures degrade to one log line, never a crash."""
        res = self._tube_result
        if res is not None:
            self._tube_result = None
            _rkey, layers = res
            try:
                plotter.remove_actor(TUBE_ACTOR_NAME)
            except Exception:
                pass               # nothing on screen yet - fine
            try:
                for tube_mesh, tube_kwargs in layers:
                    plotter.add_mesh(tube_mesh, **tube_kwargs)
            except Exception as exc:
                self._log_once(f"streamtube layer failed to render "
                               f"({exc.__class__.__name__}: {exc})")
        want = self._tube_want
        if want is None:
            return
        if self._tube_thread is not None and self._tube_thread.is_alive():
            return                 # busy: the newest want waits its turn
        self._tube_want = None
        if want[0] == self._tube_done:
            return                 # this export is already traced

        def _trace(scene=self, want=want):
            wkey, man, flow, arr, clim = want
            # streamtube_layers never raises (its contract) - belt and
            # braces anyway: a daemon thread must die quietly
            try:
                layers = streamtube_layers(man, flow, arr, clim)
            except Exception:
                layers = []
            scene._tube_done = wkey
            scene._tube_result = (wkey, layers)

        # daemon: SIGTERM must stay prompt - run.sh waits with no SIGKILL
        # escalation, and a live tracer thread must not hold exit hostage
        self._tube_thread = threading.Thread(
            target=_trace, name="streamtubes", daemon=True)
        self._tube_thread.start()

    def _add_labels(self, plotter, man):
        """Spatial annotations inside the 3-D scene: which end is the
        intake, which is the exhaust, where the fan wall sits, and what
        each component block is.

        Everything comes from manifest["geometry"], which the solver
        builds from the SAME geo dict and the SAME labels the ASCII
        renderer uses - the two views cannot drift apart. Components
        sharing a label (three DIMM banks all called RAM) collapse to one
        annotation at their combined centroid instead of stacking three
        identical tags on top of each other. Older manifests have no
        geometry block; the scene then simply renders unlabelled."""
        if self.labels_added:
            return
        pts, txt = spatial_labels(man.get("geometry"))
        if not pts:
            if man.get("geometry") is not None:
                self._log_once("manifest geometry block unusable - 3-D "
                               "labels skipped")
            return
        try:
            plotter.add_point_labels(
                pts, txt, name="spatial_labels", font_size=13,
                text_color="white", shape_color="#101014", shape_opacity=0.55,
                point_color="#ffd479", point_size=7, always_visible=True,
                render_points_as_spheres=True)
            self.labels_added = True
            log.info("3-D spatial labels added: %s", ", ".join(txt))
        except Exception as exc:
            # annotations are a convenience - never lose the scene over one
            self._log_once(f"3-D labels could not be added ({exc})")


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
