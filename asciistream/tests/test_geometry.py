"""build_geometry() baseline: every profile in server_configs.json must
produce a well-formed geometry, and the conditional-hardware rules
(0 PCIe slots / 0 drive bays / 0 CPU sockets) must keep holding."""
import copy

import pytest

from conftest import PROFILE_NAMES, all_component_boxes, cc

TOL = 1e-9

pytestmark = pytest.mark.parametrize("profile", PROFILE_NAMES, indirect=True)


def test_geo_dict_shape(profile):
    geo = cc.build_geometry(profile)
    for key in ("dims", "fan_z", "drives", "cpus", "solids", "extra_porous",
                "labels", "solid_telem", "fan_marks", "optics_box",
                "optics_custom", "n_bays"):
        assert key in geo, f"geometry dict lost key '{key}'"
    W, H, L = geo["dims"]
    assert W > 0 and H > 0 and L > 0


def test_boxes_well_formed(profile):
    """Every box must satisfy x0<x1, y0<y1, z0<z1."""
    geo = cc.build_geometry(profile)
    for name, b in all_component_boxes(geo):
        assert len(b) == 6, f"{name}: box is not 6 numbers: {b}"
        assert b[0] < b[3], f"{name}: x0 >= x1 in {b}"
        assert b[1] < b[4], f"{name}: y0 >= y1 in {b}"
        assert b[2] < b[5], f"{name}: z0 >= z1 in {b}"


def test_boxes_inside_chassis_envelope(profile):
    geo = cc.build_geometry(profile)
    W, H, L = geo["dims"]
    for name, b in all_component_boxes(geo):
        assert b[0] >= -TOL and b[3] <= W + TOL, f"{name} escapes in x: {b}"
        assert b[1] >= -TOL and b[4] <= H + TOL, f"{name} escapes in y: {b}"
        assert b[2] >= -TOL and b[5] <= L + TOL, f"{name} escapes in z: {b}"


def test_fan_wall_inside_chassis(profile):
    geo = cc.build_geometry(profile)
    _W, _H, L = geo["dims"]
    assert 0.0 < geo["fan_z"] < L


def test_no_zone_straddles_fan_wall(profile):
    """The mesh has two components meeting at the fan wall; a box crossing
    it cannot be assigned to either side."""
    geo = cc.build_geometry(profile)
    fz = geo["fan_z"]
    for name, b in all_component_boxes(geo):
        assert not (b[2] < fz < b[5]), f"{name} straddles fan wall: {b}"


def test_component_counts_match_config(profile):
    geo = cc.build_geometry(profile)
    n_cpu = int(profile.get("cpu_sockets", 0))
    assert len(geo["cpus"]) == n_cpu
    n_pcie = int(profile.get("populated_pcie_slots", 0))
    cards = [n for n, _b in geo["solids"] if n.startswith("pcie_card_")]
    assert len(cards) == n_pcie
    assert geo["n_bays"] == int(profile.get("drive_bay_count", 0))
    assert len(geo["extra_porous"]) <= len(profile.get("custom_zones", []))
    # every custom zone landed somewhere (porous list or solids list)
    named_solids = {n for n, _b in geo["solids"]}
    for zone in profile.get("custom_zones", []):
        name = zone["name"]
        assert (name in named_solids
                or any(z["name"] == name for z in geo["extra_porous"]))


def test_drive_cage_when_bays_present(profile):
    geo = cc.build_geometry(profile)
    if int(profile.get("drive_bay_count", 0)) > 0 and profile.get("drive_zone_z"):
        assert geo["drives"] is not None
        name, b, perm, c2 = geo["drives"]
        assert name == "drive_array"
        dz0, dz1 = profile["drive_zone_z"]
        assert c2 == pytest.approx(float(profile["drive_zeta"]) / (dz1 - dz0))
        assert perm == pytest.approx(float(profile["drive_permeability"]))
    else:
        assert geo["drives"] is None


# --- conditional hardware existence (synthetic overrides on a real profile) ---

def test_zero_pcie_slots_emits_no_cards(profile):
    p = copy.deepcopy(profile)
    p["populated_pcie_slots"] = 0
    geo = cc.build_geometry(p)
    assert not any(n.startswith("pcie_card_") for n, _b in geo["solids"])


def test_zero_drive_bays_yields_no_drive_cage(profile):
    p = copy.deepcopy(profile)
    p["drive_bay_count"] = 0
    geo = cc.build_geometry(p)
    assert geo["drives"] is None
    assert geo["n_bays"] == 0


def test_zero_cpu_sockets_yields_no_heatsinks(profile):
    p = copy.deepcopy(profile)
    p["cpu_sockets"] = 0
    geo = cc.build_geometry(p)
    assert geo["cpus"] == []
    # RAM banks flank the CPUs - no CPUs, no banks
    assert not any(n.startswith("dimm_bank_") for n, _b in geo["solids"])
