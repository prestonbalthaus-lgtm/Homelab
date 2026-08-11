"""Smooth streamtube rendering of the velocity field for the host viewers.

Both renderers - the live pop-out (viewer_sidecar.py) and the headless
snapshot (visualize.py) - draw their streamtube layer through ONE entry
point here, `streamtube_layers(man, flow, vec_array, clim)`, exactly like
the hardware-boundary layer in hardware_assets.py, so the two views cannot
drift apart. The layer is a set of velocity streamlines swept into tubes,
coloured by |u| with the SAME clim as the flow layer (the scalar bar the
flow layer already shows stays meaningful; the tubes add no second bar).

DEFAULT / TOGGLE: ON for 3-D exports. Set ASCIISTREAM_STREAMTUBES=0 (or
false/no/off) to disable; visualize.py additionally accepts
--streamtubes / --no-streamtubes on the command line (the flag wins over
the environment). 2-D planar exports NEVER get tubes: the slab has zero
thickness, and a streamline integrator in a degenerate-thickness dataset
steps out of the domain immediately - the 2-D view keeps its colour map
and direction arrows instead, behind one log line.

SEEDING - deliberate, not a uniform grid. A uniform seed grid drops
seeds inside the solid voids (the solver CUTS solids out of the mesh) and
in dead air, which yields sparse, unrepresentative tubes. Instead the
seeds are a speed-weighted random sample OF THE MESH'S OWN POINTS:

  * every mesh point lies in the fluid domain by construction, so no
    seed can start inside a RAM bank, PCIe card or any other void;
  * points below SEED_MIN_FRAC of the peak speed are excluded (a tube
    seeded in dead air shows numerical noise, not flow);
  * the sample is weighted by local |u|, so tubes concentrate where the
    air actually moves - the fan jets, the CPU-sink channels - not in
    quiet corners;
  * the RNG seed is FIXED, so the same export always renders the same
    tubes (refreshes do not flicker between different tube sets).

INTEGRATION - every parameter is derived from the geometry and the
measured velocity scale, never hardcoded in absolute units (the chassis
is ~0.4-0.9 m and |u| spans ~1-26 m/s across profiles and fan duties; a
constant tuned for one is wrong for the others - the same failure class
as the chassis-sized arrow glyphs fixed earlier):

  * max_length   = LENGTH_DIAGS x the mesh bounding-box diagonal
                   (a tube can cross the chassis and recirculate, but
                   cannot orbit forever) - vtkStreamTracer's maximum
                   propagation is an ARC LENGTH, so geometry, not time,
                   is the right scale;
  * step length  = STEP_FRAC_CELL in CELL-LENGTH units ('cl'), so the
                   integrator step adapts to the local mesh resolution
                   automatically (RK45 adapts further within
                   [min_step, max_step] x cell length);
  * max_steps    = the step count needed to cover max_length at the
                   estimated cell size, x10 headroom for the adaptive
                   integrator, clamped to [MAX_STEPS_FLOOR,
                   MAX_STEPS_CAP] so one pathological line can never
                   stall a refresh;
  * terminal_speed = TERMINAL_SPEED_FRAC x the peak |u| of THIS export,
                   so integration stops in dead air at 26 m/s peak and
                   at 1 m/s peak alike.

CLIPPING - tubes must not protrude outside the chassis nor into solids.
Streamlines are integrated strictly inside the mesh (which already
excludes the solid voids), so the CENTERLINES cannot leave the fluid;
what can protrude is the tube SURFACE, which extends one radius r from
the centerline. Enforcement is therefore geometric and exact: before
sweeping, the centerlines are clipped to the chassis box SHRUNK by r on
every face, and clipped away from every solid component box DILATED by
r - so every point of the swept tube surface stays inside the chassis
and outside every solid, by construction. Fragments shorter than
MIN_TUBE_LEN_FRAC of the diagonal are dropped (wall-hugging stubs read
as noise, not flow).

TUBE RADIUS scales with the chassis, never a fixed number: the smaller
of TUBE_RADIUS_DIAG_FRAC x diagonal and TUBE_RADIUS_MINDIM_FRAC x the
smallest chassis dimension - thin enough that a tube threading the DIMM
channels does not visually swallow the neighbouring banks, in a 1U pizza
box and a 4U tower alike.

FAILURE POLICY: `streamtube_layers` NEVER raises into a render loop. No
seeds, an empty tracer result, a degenerate mesh, a missing array or any
VTK failure all degrade to one log line and [] - the scene simply renders
without tubes.

This module imports only the stdlib at module level - numpy and pyvista
are lazy inside functions - so the dormant sidecar can import it for free
(same rule as hardware_assets.py, enforced by the same kind of test).
"""

import logging
import os

from hardware_assets import _geometry_dims

# --- toggle -------------------------------------------------------------------
ENV_TOGGLE = "ASCIISTREAM_STREAMTUBES"   # 0/false/no/off disables; default ON

# --- seeding ------------------------------------------------------------------
SEED_COUNT = 80          # tubes drawn (fewer when fewer candidates exist)
SEED_MIN_FRAC = 0.05     # exclude points slower than this fraction of peak |u|
SEED_RNG = 0x5EED        # FIXED: the same export always renders the same tubes

# --- integration (all applied relative to geometry / data, see docstring) -----
LENGTH_DIAGS = 2.5       # max streamline arc length, x bounding-box diagonal
STEP_FRAC_CELL = 0.5     # initial RK45 step, in cell-length units
TERMINAL_SPEED_FRAC = 1e-3   # stop integrating below this fraction of peak |u|
MAX_STEPS_FLOOR = 1000
MAX_STEPS_CAP = 20000

# --- tube appearance ----------------------------------------------------------
TUBE_RADIUS_DIAG_FRAC = 0.005    # radius as fraction of the chassis diagonal
TUBE_RADIUS_MINDIM_FRAC = 0.04   # cap: fraction of the smallest chassis dim
TUBE_SIDES = 12                  # circumferential resolution (smooth, cheap)
MIN_TUBE_LEN_FRAC = 0.05         # drop fragments shorter than this x diagonal
TUBE_ACTOR_NAME = "streamtubes"  # one actor name -> refreshes replace in place

FLAT_REL_EPS = 1e-9      # extent below this x diagonal = degenerate axis

log = logging.getLogger("flow_streamtubes")


def streamtubes_enabled(environ=None):
    """The default-ON toggle: False only when ASCIISTREAM_STREAMTUBES is
    set to 0/false/no/off (case-insensitive)."""
    env = os.environ if environ is None else environ
    return str(env.get(ENV_TOGGLE, "1")).strip().lower() not in (
        "0", "false", "no", "off")


# ------------------------------------------------------------------------------
#  Pure derivations (no numpy/pyvista - unit-testable on the host for free)
# ------------------------------------------------------------------------------

def tube_radius(dims, diag):
    """Tube radius [m] derived from the chassis, never a fixed number:
    min(TUBE_RADIUS_DIAG_FRAC x diagonal, TUBE_RADIUS_MINDIM_FRAC x
    smallest chassis dimension). `dims` may be None (no usable manifest
    geometry); the diagonal alone then sets the scale."""
    r = TUBE_RADIUS_DIAG_FRAC * float(diag)
    if dims:
        r = min(r, TUBE_RADIUS_MINDIM_FRAC * min(float(d) for d in dims))
    return r


def integration_params(dims, diag, n_cells, vmax):
    """vtkStreamTracer parameters derived from geometry + velocity scale
    (see the module docstring for the reasoning behind each one).

    dims:    (W, H, L) chassis dims, or None -> a diag-based volume
             estimate stands in for the cell-size derivation.
    diag:    mesh bounding-box diagonal [m].
    n_cells: cell count of the flow mesh (cell-size estimate).
    vmax:    peak |u| of THIS export [m/s].
    """
    diag = float(diag)
    max_length = LENGTH_DIAGS * diag
    if dims:
        vol = 1.0
        for d in dims:
            vol *= float(d)
    else:
        # a box with diagonal `diag` and 43:8:70-ish proportions has
        # volume ~diag^3/25; the exact factor only bounds max_steps
        vol = diag ** 3 / 25.0
    h_est = (vol / max(int(n_cells), 1)) ** (1.0 / 3.0)   # typical cell [m]
    steps = 10.0 * max_length / max(STEP_FRAC_CELL * h_est, 1e-12)
    return {
        "initial_step_length": STEP_FRAC_CELL,
        "step_unit": "cl",
        "max_steps": int(min(MAX_STEPS_CAP, max(MAX_STEPS_FLOOR, steps))),
        "terminal_speed": TERMINAL_SPEED_FRAC * float(vmax),
        "max_length": max_length,
    }


def select_seed_indices(mag, n_seeds=SEED_COUNT, min_frac=SEED_MIN_FRAC,
                        rng_seed=SEED_RNG):
    """Indices into `mag` (per-point |u|) chosen as streamline seeds:
    a |u|-weighted sample without replacement of the points at or above
    min_frac x peak speed. Deterministic for a given input (fixed RNG).
    Returns an empty index array when nothing moves."""
    import numpy as np
    mag = np.asarray(mag, dtype=float)
    if mag.size == 0:
        return np.empty(0, dtype=np.intp)
    finite = np.isfinite(mag)
    if not finite.any():
        return np.empty(0, dtype=np.intp)
    vmax = float(mag[finite].max())
    if vmax <= 0.0:
        return np.empty(0, dtype=np.intp)
    cand = np.flatnonzero(finite & (mag >= min_frac * vmax))
    if cand.size == 0:
        return np.empty(0, dtype=np.intp)
    take = min(int(n_seeds), cand.size)
    w = mag[cand]
    rng = np.random.default_rng(rng_seed)
    return rng.choice(cand, size=take, replace=False, p=w / w.sum())


def split_polyline_cells(lines):
    """The index arrays of each cell in a VTK legacy line-connectivity
    array [n0, i, i, ..., n1, i, ...]. Malformed tails are dropped, never
    fatal. Pure list/ndarray work - unit-testable without pyvista."""
    import numpy as np
    lines = np.asarray(lines, dtype=np.int64).ravel()
    cells, off = [], 0
    while off < lines.size:
        n = int(lines[off])
        if n <= 0 or off + 1 + n > lines.size:
            break                      # malformed tail: stop cleanly
        cells.append(lines[off + 1:off + 1 + n])
        off += 1 + n
    return cells


# ------------------------------------------------------------------------------
#  pyvista-level helpers
# ------------------------------------------------------------------------------

def drop_short_polylines(poly, min_len):
    """A copy of `poly` (line-cells PolyData) without polylines whose arc
    length is below min_len; None when nothing survives. Point data is
    carried over (the point array is left as-is; only connectivity
    shrinks)."""
    import numpy as np
    import pyvista as pv
    pts = np.asarray(poly.points)
    keep = []
    for cell in split_polyline_cells(poly.lines):
        if cell.size < 2:
            continue
        arc = np.linalg.norm(np.diff(pts[cell], axis=0), axis=1).sum()
        if arc >= min_len:
            keep.append(cell)
    if not keep:
        return None
    conn = np.concatenate([np.concatenate(([c.size], c)) for c in keep])
    out = pv.PolyData(pts.copy(), lines=conn)
    for name in poly.point_data.keys():
        out.point_data[name] = np.asarray(poly.point_data[name]).copy()
    return out


def _clip_lines_to_fluid(lines, bounds, dims, solids, r):
    """Clip streamline CENTERLINES so the swept tube surface (centerline
    +/- radius r) stays inside the chassis and outside every solid box:
    keep inside the chassis box shrunk by r per face, drop inside each
    solid box dilated by r. Returns a dataset (possibly empty)."""
    if dims:
        W, H, L = dims
        shell = (0.0, W, 0.0, H, 0.0, L)
    else:
        shell = tuple(bounds)          # no manifest dims: the mesh IS the
                                       # fluid domain - use its box
    x0, x1, y0, y1, z0, z1 = shell
    # never invert a face pair on an implausibly fat radius
    rr = min(r, 0.25 * min(x1 - x0, y1 - y0, z1 - z0))
    out = lines.clip_box((x0 + rr, x1 - rr, y0 + rr, y1 - rr,
                          z0 + rr, z1 - rr), invert=False)
    for sx0, sx1, sy0, sy1, sz0, sz1 in solids:
        if out.n_cells == 0:
            break
        out = out.clip_box((sx0 - rr, sx1 + rr, sy0 - rr, sy1 + rr,
                            sz0 - rr, sz1 + rr), invert=True)
    return out


def _solid_boxes(geom):
    """Solid component boxes from a manifest geometry block, in pyvista
    bounds order. Porous components (drive cage, CPU sinks) carry real
    flow and are NOT clipped against."""
    boxes = []
    if not isinstance(geom, dict):
        return boxes
    for c in geom.get("components") or []:
        if not isinstance(c, dict) or c.get("kind") != "solid":
            continue
        box = c.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 6:
            continue
        try:
            bx0, by0, bz0, bx1, by1, bz1 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if bx0 < bx1 and by0 < by1 and bz0 < bz1:
            boxes.append((bx0, bx1, by0, by1, bz0, bz1))
    return boxes


# ------------------------------------------------------------------------------
#  The one entry point both renderers use
# ------------------------------------------------------------------------------

def streamtube_layers(man, flow, vec_array="velocity", clim=None):
    """[(mesh, add_mesh_kwargs)] for the streamtube layer of one export,
    or [] behind one log line. `flow` is the already-loaded velocity
    dataset (the callers have it anyway - no second read of a
    ring-buffered file), `man` the manifest dict (geometry block +
    engine), `clim` the [lo, hi] the flow layer colours with, so tube
    colours and the existing scalar bar agree. Never raises."""
    try:
        import numpy as np

        if flow is None or not isinstance(man, dict):
            return []
        if man.get("engine") == "2d":
            log.info("streamtubes skipped: 2-D planar export (zero-"
                     "thickness slab - colour map + arrows carry the flow)")
            return []
        diag = float(flow.length)
        b = flow.bounds
        if diag <= 0.0 or min(b[1] - b[0], b[3] - b[2], b[5] - b[4]) \
                <= FLAT_REL_EPS * diag:
            log.info("streamtubes skipped: degenerate/flat mesh extent")
            return []

        vec = np.asarray(flow.point_data.get(vec_array, ()))
        if vec.ndim != 2 or vec.shape[1] != 3 or vec.shape[0] == 0:
            log.info("streamtubes skipped: no 3-component '%s' point "
                     "array", vec_array)
            return []
        mag = np.linalg.norm(vec, axis=1)
        finite = np.isfinite(mag)
        vmax = float(mag[finite].max()) if finite.any() else 0.0
        if vmax <= 0.0:
            log.info("streamtubes skipped: velocity field is all-zero/"
                     "non-finite")
            return []
        if "|u|" not in flow.point_data:
            flow.point_data["|u|"] = mag    # carried onto the streamlines

        idx = select_seed_indices(mag)
        if idx.size == 0:
            log.info("streamtubes skipped: no seed candidates above "
                     "%.0f%% of peak |u|", 100.0 * SEED_MIN_FRAC)
            return []
        import pyvista as pv
        seeds = pv.PolyData(np.asarray(flow.points)[idx])

        geom = man.get("geometry")
        dims = _geometry_dims(geom)
        params = integration_params(dims, diag, flow.n_cells, vmax)
        sl = flow.streamlines_from_source(
            seeds, vectors=vec_array, integrator_type=45,
            integration_direction="both", compute_vorticity=False,
            **params)
        if sl.n_points == 0 or sl.n_cells == 0:
            log.info("streamtubes skipped: tracer returned no lines")
            return []

        r = tube_radius(dims, diag)
        clipped = _clip_lines_to_fluid(sl, b, dims, _solid_boxes(geom), r)
        if clipped.n_cells == 0:
            log.info("streamtubes skipped: nothing left after clipping "
                     "to the fluid domain")
            return []
        # clip_box returns an UnstructuredGrid of line fragments: back to
        # PolyData, re-join contiguous fragments into polylines, drop
        # wall-hugging stubs, then sweep.
        lines = clipped.extract_surface(algorithm="dataset_surface")
        lines = lines.strip(join=True)
        lines = drop_short_polylines(lines, MIN_TUBE_LEN_FRAC * diag)
        if lines is None:
            log.info("streamtubes skipped: only sub-scale fragments "
                     "survived clipping")
            return []
        tubes = lines.tube(radius=r, n_sides=TUBE_SIDES, capping=True)
        if tubes is None or tubes.n_points == 0:
            log.info("streamtubes skipped: tube sweep returned nothing")
            return []

        if clim is None:
            clim = [0.0, vmax]
        kwargs = dict(scalars="|u|", cmap="jet", clim=list(clim),
                      smooth_shading=True, name=TUBE_ACTOR_NAME,
                      show_scalar_bar=False)   # the flow layer's bar rules
        log.info("streamtubes: %d tube(s), radius %.4f m, max length "
                 "%.2f m (diag %.2f m, peak |u| %.2f m/s)",
                 lines.n_cells, r, params["max_length"], diag, vmax)
        return [(tubes, kwargs)]
    except Exception as exc:
        log.warning("streamtubes skipped (%s: %s)",
                    exc.__class__.__name__, exc)
        return []
