"""_validate_geometry() negative tests: the validator is the last line of
defence before gmsh - it must REJECT malformed geometry, not merely accept
good geometry. These tests pin every rejection branch."""
import copy

import pytest

from conftest import cc


def make_geo(**over):
    """Minimal VALID geometry dict (only the keys _validate_geometry reads,
    plus the rest of the contract so it stays a realistic geo dict)."""
    geo = {
        "dims": (0.4, 0.1, 0.8), "fan_z": 0.3,
        "drives": None, "cpus": [], "solids": [], "extra_porous": [],
        "labels": {}, "solid_telem": {}, "fan_marks": [],
        "optics_box": (0.02, 0.01, 0.77, 0.38, 0.09, 0.795),
        "optics_custom": False, "n_bays": 0,
    }
    geo.update(over)
    return geo


def porous(name, box, zeta=100.0, K=1e-7):
    return {"name": name, "box": box, "zeta": zeta,
            "C2": zeta / max(box[5] - box[2], 1e-9), "K": K,
            "tag": cc.VOL_EXTRA0, "telemetry": None}


def test_valid_minimal_geometry_passes():
    cc._validate_geometry(make_geo())


def test_valid_populated_geometry_passes():
    geo = make_geo(
        drives=("drive_array", (0.0, 0.0, 0.05, 0.4, 0.1, 0.2), 5e-7, 633.0),
        cpus=[("cpu1_heatsink", (0.1, 0.006, 0.4, 0.18, 0.07, 0.5),
               2e-7, 550.0)],
        solids=[("dimm_bank_0", (0.02, 0.006, 0.4, 0.06, 0.055, 0.5))],
        extra_porous=[porous("psu_1", (0.01, 0.01, 0.7, 0.09, 0.09, 0.78))],
    )
    cc._validate_geometry(geo)


# --- fan wall -----------------------------------------------------------------

@pytest.mark.parametrize("fz", [0.0, -0.1, 0.8, 1.5])
def test_fan_wall_outside_chassis_rejected(fz):
    with pytest.raises(ValueError, match="fan_wall_z"):
        cc._validate_geometry(make_geo(fan_z=fz))


@pytest.mark.parametrize("kind", ["solid", "porous"])
def test_zone_straddling_fan_wall_rejected(kind):
    b = (0.1, 0.01, 0.25, 0.2, 0.08, 0.35)      # crosses fan_z = 0.3
    geo = (make_geo(solids=[("bad_zone", b)]) if kind == "solid"
           else make_geo(extra_porous=[porous("bad_zone", b)]))
    with pytest.raises(ValueError, match="straddles the fan wall"):
        cc._validate_geometry(geo)


# --- malformed / escaping boxes -----------------------------------------------

@pytest.mark.parametrize("box", [
    (0.3, 0.01, 0.4, 0.1, 0.08, 0.5),     # x0 > x1 (inverted)
    (0.1, 0.08, 0.4, 0.3, 0.01, 0.5),     # y0 > y1 (inverted)
    (0.1, 0.01, 0.5, 0.3, 0.08, 0.5),     # z0 == z1 (degenerate)
    (-0.05, 0.01, 0.4, 0.1, 0.08, 0.5),   # pokes out at x < 0
    (0.1, 0.01, 0.4, 0.5, 0.08, 0.5),     # pokes out at x > W
    (0.1, 0.01, 0.4, 0.3, 0.2, 0.5),      # pokes out of the lid (y > H)
    (0.1, 0.01, 0.75, 0.3, 0.08, 0.9),    # pokes out of the rear (z > L)
])
def test_bad_solid_box_rejected(box):
    with pytest.raises(ValueError, match="escapes the chassis"):
        cc._validate_geometry(make_geo(solids=[("bad_zone", box)]))


def test_bad_porous_box_rejected():
    b = (0.1, 0.01, 0.4, 0.05, 0.08, 0.5)          # inverted x
    with pytest.raises(ValueError, match="escapes the chassis"):
        cc._validate_geometry(make_geo(extra_porous=[porous("bad", b)]))


def test_bad_drive_box_rejected():
    drives = ("drive_array", (0.0, 0.0, 0.05, 0.5, 0.1, 0.2), 5e-7, 633.0)
    with pytest.raises(ValueError, match="escapes the chassis"):
        cc._validate_geometry(make_geo(drives=drives))          # W is 0.4


# --- overlaps -----------------------------------------------------------------

def test_overlapping_porous_zones_rejected():
    a = porous("zone_a", (0.05, 0.01, 0.4, 0.20, 0.09, 0.55))
    b = porous("zone_b", (0.15, 0.01, 0.5, 0.30, 0.09, 0.65))
    with pytest.raises(ValueError, match="overlap"):
        cc._validate_geometry(make_geo(extra_porous=[a, b]))


def test_porous_overlapping_solid_rejected():
    p = porous("zone_a", (0.05, 0.01, 0.4, 0.20, 0.09, 0.55))
    s = ("psu_block", (0.10, 0.01, 0.45, 0.30, 0.09, 0.60))
    with pytest.raises(ValueError, match="overlaps solid"):
        cc._validate_geometry(make_geo(extra_porous=[p], solids=[s]))


def test_cpu_overlapping_drive_cage_rejected():
    drives = ("drive_array", (0.0, 0.0, 0.05, 0.4, 0.1, 0.2), 5e-7, 633.0)
    cpu = ("cpu1_heatsink", (0.1, 0.006, 0.15, 0.18, 0.07, 0.25),
           2e-7, 550.0)
    with pytest.raises(ValueError, match="overlap"):
        cc._validate_geometry(make_geo(drives=drives, cpus=[cpu]))


def test_touching_faces_are_not_an_overlap():
    """Shared faces are fine (the fragment map handles conformal contact);
    only genuine volume intersection is an error."""
    a = porous("zone_a", (0.05, 0.01, 0.40, 0.20, 0.09, 0.50))
    s = ("block", (0.20, 0.01, 0.40, 0.30, 0.09, 0.50))   # touches at x=0.2
    cc._validate_geometry(make_geo(extra_porous=[a], solids=[s]))


# --- integration: bad configs must die inside build_geometry ------------------

def test_build_geometry_rejects_cpu_zone_straddling_fan_wall(cfg):
    p = copy.deepcopy(cfg["servers"]["6029U"])       # fan_wall_z = 0.25
    p["cpu_zone_z"] = [0.2, 0.3]
    with pytest.raises(ValueError, match="straddles the fan wall"):
        cc.build_geometry(p)


def test_build_geometry_rejects_short_custom_zone_box(cfg):
    p = copy.deepcopy(cfg["servers"]["6029U"])
    p["custom_zones"] = [{"name": "bad", "type": "solid",
                          "box": [0.1, 0.01, 0.4, 0.2]}]
    with pytest.raises(ValueError, match="box must be"):
        cc.build_geometry(p)


def test_build_geometry_rejects_porous_zone_without_zeta(cfg):
    p = copy.deepcopy(cfg["servers"]["6029U"])
    p["custom_zones"] = [{"name": "bad", "type": "porous",
                          "box": [0.1, 0.01, 0.4, 0.2, 0.07, 0.5]}]
    with pytest.raises(ValueError, match="needs zeta"):
        cc.build_geometry(p)


def test_build_geometry_rejects_unknown_zone_type(cfg):
    p = copy.deepcopy(cfg["servers"]["6029U"])
    p["custom_zones"] = [{"name": "bad", "type": "liquid",
                          "box": [0.1, 0.01, 0.4, 0.2, 0.07, 0.5]}]
    with pytest.raises(ValueError, match="type must be"):
        cc.build_geometry(p)


def test_build_geometry_rejects_escaping_custom_zone(cfg):
    p = copy.deepcopy(cfg["servers"]["6029U"])
    p["custom_zones"] = [{"name": "bad", "type": "solid",
                          "box": [0.1, 0.01, 0.4, 0.9, 0.07, 0.5]}]
    with pytest.raises(ValueError, match="escapes the chassis"):
        cc.build_geometry(p)
