"""flow_streamtubes.py: the streamtube layer both host renderers share.

Covers the pure derivations (seed selection, geometry/velocity-scaled
integration parameters, tube radius, polyline connectivity), the
end-to-end entry point on a synthetic chassis-like flow (tubes exist,
stay inside the chassis, stay out of solid boxes, carry |u| on the
caller's clim), the 2-D / degenerate / malformed degrade paths (always
[] + one log line, never a raise), the default-ON toggle, and the
import-hygiene rule the dormant sidecar depends on.

pyvista-level tests build tiny synthetic ImageData flows - no solver
export is needed and nothing touches the repo's watch dir.
"""
import logging
import subprocess
import sys

import numpy as np
import pytest

from conftest import REPO  # noqa: F401  (bootstraps sys.path to the repo)

import flow_streamtubes as ft

DIMS = (0.43, 0.08, 0.7)         # the 6029U chassis, front(z=0)->back
DIAG = sum(d * d for d in DIMS) ** 0.5


def chassis_flow(nx=12, ny=6, nz=24, speed=2.0):
    """Synthetic chassis-filling flow: uniform +z at `speed` m/s."""
    import pyvista as pv
    W, H, L = DIMS
    grid = pv.ImageData(dimensions=(nx, ny, nz),
                        spacing=(W / (nx - 1), H / (ny - 1), L / (nz - 1)))
    vel = np.zeros((grid.n_points, 3))
    vel[:, 2] = speed
    grid.point_data["velocity"] = vel
    return grid


def manifest(engine="3d", solids=(), dims=DIMS):
    comps = [{"name": f"solid_{i}", "label": "S", "kind": "solid",
              "box": [b[0], b[2], b[4], b[1], b[3], b[5]]}
             for i, b in enumerate(solids)]
    return {"type": "viz", "engine": engine,
            "geometry": {"dims": list(dims), "components": comps}}


# ------------------------------------------------------------------------------
#  Toggle
# ------------------------------------------------------------------------------

def test_default_is_on():
    assert ft.streamtubes_enabled(environ={}) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF ",
                                   "False"])
def test_disabled_values(value):
    assert ft.streamtubes_enabled(
        environ={ft.ENV_TOGGLE: value}) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
def test_enabled_values(value):
    assert ft.streamtubes_enabled(environ={ft.ENV_TOGGLE: value}) is True


# ------------------------------------------------------------------------------
#  Pure derivations: radius + integration parameters scale with the data
# ------------------------------------------------------------------------------

def test_tube_radius_scales_with_the_chassis():
    r = ft.tube_radius(DIMS, DIAG)
    assert 0.0 < r <= ft.TUBE_RADIUS_MINDIM_FRAC * min(DIMS)
    # a chassis twice the size gets a tube (up to) twice as fat
    r2 = ft.tube_radius(tuple(2 * d for d in DIMS), 2 * DIAG)
    assert r2 == pytest.approx(2 * r)


def test_tube_radius_capped_by_the_smallest_dim():
    # pizza-box: 1U height must cap the radius, not the long diagonal
    flat = (0.9, 0.02, 0.9)
    diag = sum(d * d for d in flat) ** 0.5
    assert ft.tube_radius(flat, diag) == pytest.approx(
        ft.TUBE_RADIUS_MINDIM_FRAC * 0.02)


def test_tube_radius_without_dims_uses_the_diagonal():
    assert ft.tube_radius(None, DIAG) == pytest.approx(
        ft.TUBE_RADIUS_DIAG_FRAC * DIAG)


def test_integration_length_scales_with_geometry_not_constants():
    p1 = ft.integration_params(DIMS, DIAG, 30000, vmax=2.0)
    p2 = ft.integration_params(tuple(2 * d for d in DIMS), 2 * DIAG,
                               30000, vmax=2.0)
    assert p1["max_length"] == pytest.approx(ft.LENGTH_DIAGS * DIAG)
    assert p2["max_length"] == pytest.approx(2 * p1["max_length"])


def test_terminal_speed_scales_with_the_velocity_scale():
    slow = ft.integration_params(DIMS, DIAG, 30000, vmax=1.0)
    fast = ft.integration_params(DIMS, DIAG, 30000, vmax=26.0)
    assert fast["terminal_speed"] == pytest.approx(
        26.0 * slow["terminal_speed"])
    # dead air at ANY scale stops integration: threshold is relative
    assert slow["terminal_speed"] == pytest.approx(
        ft.TERMINAL_SPEED_FRAC * 1.0)


def test_max_steps_bounded_and_mesh_aware():
    coarse = ft.integration_params(DIMS, DIAG, 1000, vmax=2.0)
    fine = ft.integration_params(DIMS, DIAG, 10**7, vmax=2.0)
    for p in (coarse, fine):
        assert ft.MAX_STEPS_FLOOR <= p["max_steps"] <= ft.MAX_STEPS_CAP
    assert fine["max_steps"] >= coarse["max_steps"]
    assert coarse["step_unit"] == "cl"      # steps follow the local mesh


def test_integration_params_survive_missing_dims():
    p = ft.integration_params(None, DIAG, 30000, vmax=2.0)
    assert p["max_length"] == pytest.approx(ft.LENGTH_DIAGS * DIAG)
    assert p["max_steps"] >= ft.MAX_STEPS_FLOOR


# ------------------------------------------------------------------------------
#  Seed selection: deliberate, in-flow, deterministic
# ------------------------------------------------------------------------------

def test_seeds_avoid_dead_air():
    mag = np.array([0.0, 0.001, 0.02, 1.0, 2.0, 0.04])
    idx = ft.select_seed_indices(mag, n_seeds=6)
    assert len(idx) > 0
    assert (mag[idx] >= ft.SEED_MIN_FRAC * 2.0).all()


def test_seeds_are_deterministic_for_one_export():
    mag = np.random.default_rng(3).uniform(0.0, 5.0, 500)
    a = ft.select_seed_indices(mag)
    b = ft.select_seed_indices(mag)
    assert np.array_equal(a, b), "same export must render the same tubes"


def test_seed_count_capped_and_exhaustive():
    mag = np.full(10, 1.0)
    assert len(ft.select_seed_indices(mag, n_seeds=80)) == 10
    mag = np.full(500, 1.0)
    idx = ft.select_seed_indices(mag, n_seeds=80)
    assert len(idx) == 80
    assert len(np.unique(idx)) == 80          # without replacement


def test_seeds_weighted_towards_fast_flow():
    # one point carries ~99% of the total speed: it must be sampled
    mag = np.full(1000, 0.1)
    mag[123] = 90.0
    idx = ft.select_seed_indices(mag, n_seeds=5)
    assert 123 in idx


@pytest.mark.parametrize("mag", [
    np.empty(0), np.zeros(50), np.full(20, np.nan),
    np.array([np.inf * 0.0, np.nan])])
def test_no_flow_means_no_seeds(mag):
    assert len(ft.select_seed_indices(mag)) == 0


# ------------------------------------------------------------------------------
#  Polyline connectivity helpers
# ------------------------------------------------------------------------------

def test_split_polyline_cells():
    conn = np.array([3, 0, 1, 2, 2, 5, 6])
    cells = ft.split_polyline_cells(conn)
    assert [c.tolist() for c in cells] == [[0, 1, 2], [5, 6]]


def test_split_polyline_cells_malformed_tail_dropped():
    assert [c.tolist() for c in ft.split_polyline_cells([2, 0, 1, 9, 4])] \
        == [[0, 1]]
    assert ft.split_polyline_cells([]) == []
    assert ft.split_polyline_cells([0, 0]) == []


def test_drop_short_polylines_keeps_point_data():
    import pyvista as pv
    pts = np.array([[0, 0, 0], [0, 0, 0.5],          # 0.5 m line
                    [0.2, 0, 0], [0.201, 0, 0]])     # 1 mm stub
    poly = pv.PolyData(pts, lines=np.array([2, 0, 1, 2, 2, 3]))
    poly.point_data["|u|"] = np.array([1.0, 2.0, 3.0, 4.0])
    out = ft.drop_short_polylines(poly, min_len=0.05)
    assert out is not None and out.n_cells == 1
    assert np.array_equal(out.point_data["|u|"], poly.point_data["|u|"])
    assert ft.drop_short_polylines(poly, min_len=10.0) is None


# ------------------------------------------------------------------------------
#  streamtube_layers: end to end on a synthetic chassis flow
# ------------------------------------------------------------------------------

def test_layers_render_inside_the_chassis_and_outside_solids():
    solid = (0.10, 0.30, 0.01, 0.06, 0.30, 0.42)   # mid-chassis block
    man = manifest(solids=[solid])
    flow = chassis_flow()
    clim = [0.0, 5.0]
    layers = ft.streamtube_layers(man, flow, "velocity", clim)
    assert len(layers) == 1
    tubes, kwargs = layers[0]
    assert tubes.n_points > 0
    # colour contract: |u| on the caller's clim, no second scalar bar
    assert kwargs["scalars"] == "|u|"
    assert kwargs["clim"] == clim
    assert kwargs["name"] == ft.TUBE_ACTOR_NAME
    assert kwargs["show_scalar_bar"] is False
    assert "|u|" in tubes.point_data
    # containment contract: the tube SURFACE stays inside the chassis...
    W, H, L = DIMS
    b = tubes.bounds
    eps = 1e-6
    assert b[0] >= -eps and b[1] <= W + eps
    assert b[2] >= -eps and b[3] <= H + eps
    assert b[4] >= -eps and b[5] <= L + eps
    # ...and out of every solid box (the synthetic flow runs straight
    # through the box, so only the clip can be keeping it out)
    pts = np.asarray(tubes.points)
    x0, x1, y0, y1, z0, z1 = solid
    inside = ((pts[:, 0] > x0 + eps) & (pts[:, 0] < x1 - eps)
              & (pts[:, 1] > y0 + eps) & (pts[:, 1] < y1 - eps)
              & (pts[:, 2] > z0 + eps) & (pts[:, 2] < z1 - eps))
    assert not inside.any(), "tube surface entered a solid component"


def test_layers_are_deterministic():
    man = manifest()
    a = ft.streamtube_layers(man, chassis_flow(), "velocity", [0, 5])
    b = ft.streamtube_layers(man, chassis_flow(), "velocity", [0, 5])
    assert a[0][0].n_points == b[0][0].n_points


def test_default_clim_matches_the_field_peak():
    layers = ft.streamtube_layers(manifest(), chassis_flow(speed=3.0),
                                  "velocity", None)
    assert layers and layers[0][1]["clim"] == [0.0, 3.0]


def test_layers_survive_missing_geometry_block():
    man = {"type": "viz", "engine": "3d"}     # older manifest: no geometry
    layers = ft.streamtube_layers(man, chassis_flow(), "velocity", [0, 5])
    assert len(layers) == 1                   # mesh bounds stand in


# ------------------------------------------------------------------------------
#  Degrade paths: always [] + a log line, never a raise
# ------------------------------------------------------------------------------

def test_2d_engine_is_skipped(caplog):
    with caplog.at_level(logging.INFO, logger="flow_streamtubes"):
        assert ft.streamtube_layers(manifest(engine="2d"), chassis_flow(),
                                    "velocity", [0, 5]) == []
    assert any("2-D" in r.message for r in caplog.records)


def test_flat_slab_is_skipped(caplog):
    """A zero-thickness dataset (the 2-D engine's export shape) must be
    caught even when the manifest fails to say engine=2d."""
    import pyvista as pv
    W, _H, L = DIMS
    slab = pv.ImageData(dimensions=(12, 24, 1),
                        spacing=(W / 11, L / 23, 1.0))
    vel = np.zeros((slab.n_points, 3))
    vel[:, 1] = 2.0
    slab.point_data["velocity"] = vel
    with caplog.at_level(logging.INFO, logger="flow_streamtubes"):
        assert ft.streamtube_layers(manifest(), slab, "velocity",
                                    [0, 5]) == []
    assert any("degenerate" in r.message for r in caplog.records)


@pytest.mark.parametrize("case", ["none_flow", "bad_man", "no_array",
                                  "all_zero", "all_nan"])
def test_degrade_paths_return_empty(case, caplog):
    man = manifest()
    flow = chassis_flow()
    if case == "none_flow":
        flow = None
    elif case == "bad_man":
        man = "not a dict"
    elif case == "no_array":
        del flow.point_data["velocity"]
    elif case == "all_zero":
        flow.point_data["velocity"][:] = 0.0
    elif case == "all_nan":
        flow.point_data["velocity"][:] = np.nan
    with caplog.at_level(logging.DEBUG, logger="flow_streamtubes"):
        assert ft.streamtube_layers(man, flow, "velocity", [0, 5]) == []
    # a degrade is one line, not a traceback
    assert all(r.exc_info is None for r in caplog.records)


def test_vtk_failure_is_one_warning_no_raise(caplog, monkeypatch):
    """Any exception inside (here: a poisoned tracer) must surface as
    exactly one WARNING line, never cross into the render loop."""
    flow = chassis_flow()

    def boom(*_a, **_k):
        raise RuntimeError("vtk exploded")
    monkeypatch.setattr(type(flow), "streamlines_from_source", boom,
                        raising=True)
    with caplog.at_level(logging.DEBUG, logger="flow_streamtubes"):
        assert ft.streamtube_layers(manifest(), flow, "velocity",
                                    [0, 5]) == []
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None


# ------------------------------------------------------------------------------
#  The sidecar hand-off: worker thread computes, the timer tick applies
# ------------------------------------------------------------------------------

class FakePlotter:
    def __init__(self):
        self.added, self.removed = [], []

    def add_mesh(self, mesh, **kwargs):
        self.added.append((mesh, kwargs))

    def remove_actor(self, name):
        self.removed.append(name)


def test_scene_pump_applies_finished_tubes_off_the_worker_thread():
    import time
    import viewer_sidecar as vs
    scene = vs.SceneState("/nonexistent")
    scene.tubes_on = True
    plotter = FakePlotter()
    key = ("dir", 1, False)
    scene._tube_want = (key, manifest(), chassis_flow(), "velocity",
                        [0.0, 5.0])
    scene._pump_streamtubes(plotter)          # starts the worker
    assert scene._tube_thread is not None
    deadline = time.time() + 60
    while scene._tube_result is None and time.time() < deadline:
        time.sleep(0.05)
    assert scene._tube_result is not None, "worker never finished"
    assert scene._tube_thread.daemon, "tracer must not block SIGTERM exit"
    scene._pump_streamtubes(plotter)          # next tick applies it
    assert ft.TUBE_ACTOR_NAME in plotter.removed
    assert len(plotter.added) == 1
    assert plotter.added[0][1]["name"] == ft.TUBE_ACTOR_NAME
    assert scene._tube_result is None
    # the same export is never traced twice
    scene._tube_want = (key, manifest(), chassis_flow(), "velocity",
                        [0.0, 5.0])
    thread_before = scene._tube_thread
    scene._pump_streamtubes(plotter)
    assert scene._tube_thread is thread_before


def test_scene_pump_replaces_stale_tubes_with_nothing():
    """A newer export that yields NO tubes must still clear the old
    actor - stale tubes over fresh data would misrepresent the flow."""
    import viewer_sidecar as vs
    scene = vs.SceneState("/nonexistent")
    plotter = FakePlotter()
    scene._tube_result = (("dir", 2, True), [])
    scene._pump_streamtubes(plotter)
    assert ft.TUBE_ACTOR_NAME in plotter.removed
    assert plotter.added == []


# ------------------------------------------------------------------------------
#  Dormancy: importing the module must stay near-free
# ------------------------------------------------------------------------------

def test_import_pulls_no_heavy_stack():
    """The sidecar imports flow_streamtubes while DORMANT (it is spawned
    on every interactive run): numpy/pyvista/vtk must stay lazy inside
    functions, exactly like hardware_assets."""
    heavy = ("numpy", "pyvista", "vtk", "trimesh", "DracoPy", "PIL")
    code = (
        "import sys\n"
        "import flow_streamtubes\n"
        f"heavy = {heavy!r}\n"
        "bad = sorted(m for m in sys.modules"
        " if m.split('.')[0] in heavy)\n"
        "print(','.join(bad))\n"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "", (
        "importing flow_streamtubes pulled in heavy modules: "
        f"{res.stdout.strip()}")
