"""The manifest geometry block and the 3-D spatial labels built from it.

zones*.pvtu carries only numeric cell tags, so the host viewer cannot name
anything on its own. `chassis_cfd._viz_geometry` ships the labelled boxes
in the manifest and `viewer_sidecar.spatial_labels` turns them into scene
annotations. Both are pure data, so both are testable on the host with no
container and no Qt.

The labels the user-facing spec requires are asserted by name here: FRONT
(Intake / Drives), BACK (Exhaust / PSUs), CPU 1, CPU 2, RAM and Fan Wall.
"""
import copy

import pytest

from conftest import PROFILE_NAMES, cc

import viewer_sidecar as vs

REQUIRED = ["FRONT (Intake / Drives)", "BACK (Exhaust / PSUs)",
            "CPU 1", "CPU 2", "RAM", "Fan Wall"]


@pytest.fixture()
def geom6029(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    return cc._viz_geometry(cc.build_geometry(s))


# --- the manifest geometry block ---------------------------------------------

def test_geometry_block_shape(geom6029):
    assert len(geom6029["dims"]) == 3
    assert all(d > 0 for d in geom6029["dims"])
    assert isinstance(geom6029["fan_z"], float)
    assert geom6029["components"]


def test_geometry_is_json_serialisable(geom6029):
    """It crosses a file boundary as JSON - no numpy scalars allowed."""
    import json
    json.loads(json.dumps(geom6029))          # must not raise


@pytest.mark.parametrize("profile", PROFILE_NAMES, indirect=True)
def test_geometry_block_builds_for_every_profile(profile):
    g = cc._viz_geometry(cc.build_geometry(profile))
    W, H, L = g["dims"]
    for c in g["components"]:
        x0, y0, z0, x1, y1, z1 = c["box"]
        assert x0 < x1 and y0 < y1 and z0 < z1, c["name"]
        assert c["label"], c["name"]
        assert c["kind"] in ("solid", "porous")


def test_labels_match_the_ascii_renderer(geom6029):
    """One source of truth: the 3-D tag for a component is the same string
    the terminal view stamps, so the two cannot drift apart."""
    by_name = {c["name"]: c["label"] for c in geom6029["components"]}
    assert by_name["cpu1_heatsink"] == "CPU 1"
    assert by_name["cpu2_heatsink"] == "CPU 2"
    assert by_name["dimm_bank_0"] == "RAM"
    assert by_name["drive_array"] == "DRIVES"


# --- spatial_labels ----------------------------------------------------------

def test_required_labels_all_present(geom6029):
    _pts, txt = vs.spatial_labels(geom6029)
    missing = [t for t in REQUIRED if t not in txt]
    assert not missing, f"missing required 3-D labels: {missing}"


def test_front_and_back_bracket_the_chassis(geom6029):
    pts, txt = vs.spatial_labels(geom6029)
    _W, _H, L = geom6029["dims"]
    front = pts[txt.index("FRONT (Intake / Drives)")]
    back = pts[txt.index("BACK (Exhaust / PSUs)")]
    assert front[2] < 0.0, "intake tag must sit outside the front face"
    assert back[2] > L, "exhaust tag must sit outside the rear face"
    assert front[2] < back[2]


def test_fan_wall_label_sits_on_the_fan_plane(geom6029):
    pts, txt = vs.spatial_labels(geom6029)
    assert pts[txt.index("Fan Wall")][2] == pytest.approx(geom6029["fan_z"])


def test_repeated_labels_collapse_to_one(geom6029):
    """Three DIMM banks are all 'RAM' - one tag at their centroid, not
    three stacked on top of each other."""
    banks = [c for c in geom6029["components"] if c["label"] == "RAM"]
    assert len(banks) > 1, "fixture must have several RAM banks"
    _pts, txt = vs.spatial_labels(geom6029)
    assert txt.count("RAM") == 1


def test_distinct_cpus_stay_separate(geom6029):
    pts, txt = vs.spatial_labels(geom6029)
    assert txt.count("CPU 1") == 1 and txt.count("CPU 2") == 1
    assert pts[txt.index("CPU 1")][0] != pts[txt.index("CPU 2")][0]


def test_labels_stay_inside_the_chassis_height(geom6029):
    pts, _txt = vs.spatial_labels(geom6029)
    _W, H, _L = geom6029["dims"]
    assert all(0.0 <= p[1] <= H for p in pts)


def test_no_pcie_label_when_no_cards_fitted(cfg):
    """Ties the label layer to the PCIe ghosting fix: with GPUs and NIC
    both declined there must be no PCIe tag in the 3-D scene either."""
    s = cc.apply_hw_overrides(copy.deepcopy(cfg["servers"]["6029U"]),
                              {"nic": False})
    g = cc._viz_geometry(cc.build_geometry(s))
    _pts, txt = vs.spatial_labels(g)
    assert "PCIe" not in txt
    assert "RISER" in txt, "the static risers must still be annotated"


# --- malformed input degrades, never raises ----------------------------------

@pytest.mark.parametrize("bad", [
    None, {}, [], "nope", 42,
    {"dims": [0, 0, 0]},
    {"dims": ["a", "b", "c"]},
    {"dims": [1, 2]},
])
def test_malformed_geometry_returns_empty(bad):
    assert vs.spatial_labels(bad) == ([], [])


def test_malformed_components_are_skipped_not_fatal():
    g = {"dims": [1.0, 1.0, 1.0], "fan_z": 0.5, "components": [
        {"label": "GOOD", "box": [0, 0, 0, 1, 1, 1]},
        {"label": "no box"},
        {"box": [0, 0, 0, 1, 1, 1]},          # no label
        {"label": "short", "box": [0, 0, 0]},
        {"label": "nonnumeric", "box": ["a", 0, 0, 1, 1, 1]},
        "not even a dict",
    ]}
    _pts, txt = vs.spatial_labels(g)
    assert "GOOD" in txt
    assert not {"no box", "short", "nonnumeric"} & set(txt)


def test_out_of_range_fan_plane_is_dropped():
    g = {"dims": [1.0, 1.0, 1.0], "fan_z": 9.0, "components": []}
    _pts, txt = vs.spatial_labels(g)
    assert "Fan Wall" not in txt
