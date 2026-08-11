"""Dynamic drive arrays (profile key "drive_array" / wizard hw key
"drive_mix"): discrete per-drive porous boxes in a front bay grid, with
unpopulated bays as OPEN fluid domain, mixed 2.5in/3.5in populations, and
the legacy monolithic slab kept bit-identical when the key is absent.

Schema reference: the build_geometry docstring. Impedance basis: the
profile's drive_zeta scaled by the existing DRIVE_TYPE_ZETA multipliers
per class (2.5in x0.75, 3.5in x1.15) over each drive box's own z-length,
with the profile's drive_permeability as K - no new impedance model."""
import copy

import pytest

from conftest import PROFILE_NAMES, cc


@pytest.fixture()
def s6029(cfg):
    return copy.deepcopy(cfg["servers"]["6029U"])


def with_array(profile, spec, **extra):
    p = copy.deepcopy(profile)
    p["drive_array"] = spec
    p.update(extra)
    return p


MIX = [{"count": 2, "size": "2.5"}, {"count": 4, "size": "3.5"}]


# --- parse_drive_mix / normalize_drive_size ----------------------------------

@pytest.mark.parametrize("spelling,canonical", [
    ("2.5", "2.5in NVMe/SAS"), ("2.5in", "2.5in NVMe/SAS"),
    ("2.5in NVMe/SAS", "2.5in NVMe/SAS"), (2.5, "2.5in NVMe/SAS"),
    ("3.5", "3.5in HDD"), ("3.5IN", "3.5in HDD"),
    ("3.5in HDD", "3.5in HDD"), (3.5, "3.5in HDD"),
])
def test_normalize_drive_size_accepted_spellings(spelling, canonical):
    assert cc.normalize_drive_size(spelling) == canonical


def test_normalize_drive_size_rejects_unknown():
    with pytest.raises(ValueError, match="unknown drive size"):
        cc.normalize_drive_size("5.25")


def test_parse_drive_mix_compact_string():
    assert cc.parse_drive_mix("2x2.5 + 4x3.5") == [
        {"count": 2, "size": "2.5in NVMe/SAS"},
        {"count": 4, "size": "3.5in HDD"}]
    # comma separator and upper-case X too
    assert cc.parse_drive_mix("1X3.5in,3x2.5") == [
        {"count": 1, "size": "3.5in HDD"},
        {"count": 3, "size": "2.5in NVMe/SAS"}]


def test_parse_drive_mix_json_string_and_list():
    want = [{"count": 1, "size": "3.5in HDD"}]
    assert cc.parse_drive_mix('[{"count": 1, "size": "3.5in"}]') == want
    got = cc.parse_drive_mix([{"count": "1", "size": 3.5}])
    assert got == want


def test_parse_drive_mix_empty_means_no_drives():
    assert cc.parse_drive_mix("") == []
    assert cc.parse_drive_mix([]) == []


def test_parse_drive_mix_keeps_label():
    got = cc.parse_drive_mix([{"count": 1, "size": "2.5", "label": "NVME"}])
    assert got[0]["label"] == "NVME"


@pytest.mark.parametrize("bad", ["garbage", "2y3.5", "x3.5", "2x"])
def test_parse_drive_mix_rejects_malformed_terms(bad):
    with pytest.raises(ValueError, match="expected"):
        cc.parse_drive_mix(bad)


def test_parse_drive_mix_rejects_negative_and_missing():
    with pytest.raises(ValueError, match=">= 0"):
        cc.parse_drive_mix([{"count": -1, "size": "2.5"}])
    with pytest.raises(ValueError, match='"size"'):
        cc.parse_drive_mix([{"count": 2}])
    with pytest.raises(ValueError, match='"count"'):
        cc.parse_drive_mix([{"count": "two", "size": "2.5"}])
    with pytest.raises(TypeError, match="list"):
        cc.parse_drive_mix(42)


# --- backward compatibility: no drive_array key => legacy behaviour ----------

@pytest.mark.parametrize("profile", PROFILE_NAMES, indirect=True)
def test_legacy_profiles_never_enter_discrete_mode(profile):
    geo = cc.build_geometry(profile)
    assert geo["drive_boxes"] == []
    assert geo["drive_mode"] in ("slab", "none")
    assert (geo["drive_mode"] == "slab") == (geo["drives"] is not None)
    if geo["drive_mode"] == "slab":
        # the monolith still spans the full cross-section over drive_zone_z
        W, H, _L = geo["dims"]
        dz0, dz1 = profile["drive_zone_z"]
        assert geo["drives"][1] == (0.0, 0.0, float(dz0), W, H, float(dz1))
        assert geo["n_drives"] == geo["n_bays"]
    else:
        assert geo["n_drives"] == 0


def test_legacy_slab_impedance_untouched(s6029):
    """The pinned 6029U fan gate (129.0 CFM / 1.77 m/s) lives in
    test_fan_operating_point; here we pin that the slab C2/K expression
    itself did not move."""
    geo = cc.build_geometry(s6029)
    _n, _b, K, C2 = geo["drives"]
    dz0, dz1 = s6029["drive_zone_z"]
    assert K == pytest.approx(float(s6029["drive_permeability"]))
    assert C2 == pytest.approx(float(s6029["drive_zeta"]) / (dz1 - dz0))


# --- discrete arrays: layout, naming, impedance ------------------------------

def test_mixed_array_shapes_names_labels(s6029):
    geo = cc.build_geometry(with_array(s6029, MIX))
    assert geo["drive_mode"] == "discrete"
    assert geo["drives"] is None            # the slab is gone
    assert geo["n_bays"] == 8               # capacity is unchanged
    assert geo["n_drives"] == 6
    names = [d["name"] for d in geo["drive_boxes"]]
    assert names == [f"drive_{i}" for i in range(1, 7)]
    labels = [geo["labels"][n] for n in names]
    assert labels == ["SSD 1", "SSD 2", "HDD 1", "HDD 2", "HDD 3", "HDD 4"]
    tags = [d["tag"] for d in geo["drive_boxes"]]
    assert tags == [cc.VOL_DRIVE0 + i for i in range(6)]


def test_mixed_array_footprints_differ_per_class(s6029):
    """2.5in ~ 70x100x15 mm, 3.5in ~ 102x147x26 mm: the two classes must
    mesh with genuinely different boxes (flat orientation in the 6029U's
    3.5in bay grid)."""
    geo = cc.build_geometry(with_array(s6029, MIX))
    small = [d for d in geo["drive_boxes"] if d["size"] == "2.5in NVMe/SAS"]
    large = [d for d in geo["drive_boxes"] if d["size"] == "3.5in HDD"]
    for d in small:
        b = d["box"]
        assert (b[3] - b[0], b[4] - b[1]) == pytest.approx((0.070, 0.015))
        assert b[5] - b[2] == pytest.approx(0.100)     # own length < zone
    for d in large:
        b = d["box"]
        assert (b[3] - b[0], b[4] - b[1]) == pytest.approx((0.102, 0.026))
        # 147 mm drive in the 150 mm zone: its own length, not the zone's
        assert b[5] - b[2] == pytest.approx(0.147)


def test_mixed_array_impedance_uses_drive_type_zeta_basis(s6029):
    """Per-class zeta = drive_zeta x DRIVE_TYPE_ZETA[class] over the box's
    own z-length; K = drive_permeability. No new impedance model."""
    geo = cc.build_geometry(with_array(s6029, MIX))
    z0 = float(s6029["drive_zeta"])
    for d in geo["drive_boxes"]:
        want = z0 * cc.DRIVE_TYPE_ZETA[d["size"]]
        depth = d["box"][5] - d["box"][2]
        assert d["zeta"] == pytest.approx(want)
        assert d["C2"] == pytest.approx(want / depth)
        assert d["K"] == pytest.approx(float(s6029["drive_permeability"]))


def test_drive_boxes_validate_and_stay_apart(s6029):
    """build_geometry runs _validate_geometry - so the boxes are in the
    envelope, off the fan wall and overlap-free. Double-check pairwise
    separation explicitly (the point of discrete bays)."""
    geo = cc.build_geometry(with_array(s6029, MIX))
    boxes = [d["box"] for d in geo["drive_boxes"]]
    for i, a in enumerate(boxes):
        assert a[5] <= geo["fan_z"]                    # front of the fans
        for b in boxes[i + 1:]:
            assert not all(min(a[k + 3], b[k + 3]) - max(a[k], b[k]) > 1e-9
                           for k in range(3)), (a, b)


def test_unpopulated_bays_leave_open_domain(s6029):
    """8-bay chassis with 3 drives => exactly 3 boxes; the other 5 bays
    have NO geometry at all (open air is the absence of a box)."""
    geo = cc.build_geometry(with_array(s6029,
                                       [{"count": 3, "size": "3.5"}]))
    assert geo["n_drives"] == 3 and geo["n_bays"] == 8
    assert len(geo["drive_boxes"]) == 3


def test_empty_array_means_fully_open_front_bay(s6029):
    geo = cc.build_geometry(with_array(s6029, []))
    assert geo["drive_mode"] == "discrete"
    assert geo["drives"] is None
    assert geo["drive_boxes"] == [] and geo["n_drives"] == 0
    assert geo["n_bays"] == 8


def test_zero_count_groups_are_skipped(s6029):
    geo = cc.build_geometry(with_array(
        s6029, [{"count": 0, "size": "2.5"}, {"count": 2, "size": "3.5"}]))
    assert [d["size"] for d in geo["drive_boxes"]] == ["3.5in HDD"] * 2


def test_deterministic_layout(s6029):
    a = cc.build_geometry(with_array(s6029, MIX))["drive_boxes"]
    b = cc.build_geometry(with_array(s6029, MIX))["drive_boxes"]
    assert a == b


def test_on_edge_single_row_in_tall_chassis(cfg):
    """R740xd (2U, 24x 2.5in bays): drives stand ON EDGE - 24 across in
    ONE row, 70 mm tall, straddling the mid-height plane (so the 2-D
    engine sees them)."""
    p = with_array(cfg["servers"]["R740xd"], [{"count": 24, "size": "2.5"}])
    geo = cc.build_geometry(p)
    H = geo["dims"][1]
    boxes = [d["box"] for d in geo["drive_boxes"]]
    assert len(boxes) == 24
    assert len({round(b[1], 6) for b in boxes}) == 1     # one row
    for b in boxes:
        assert b[4] - b[1] == pytest.approx(0.070)       # on edge
        assert cc._midplane_hit(b, H)


def test_low_first_row_documented_absent_from_midplane(s6029):
    """Documented 2-D projection semantics: a 2U 3.5in row sitting on the
    bay floor tops out below mid-height, so a bottom-row-only population
    is absent from the planar slice (build_geometry docstring NOTE)."""
    geo = cc.build_geometry(with_array(s6029,
                                       [{"count": 3, "size": "3.5"}]))
    H = geo["dims"][1]
    for d in geo["drive_boxes"]:
        assert not cc._midplane_hit(d["box"], H)


def test_custom_label_prefix(s6029):
    geo = cc.build_geometry(with_array(
        s6029, [{"count": 2, "size": "2.5", "label": "NVME"}]))
    assert [geo["labels"][d["name"]] for d in geo["drive_boxes"]] == \
        ["NVME 1", "NVME 2"]


# --- misfits die loudly ------------------------------------------------------

def test_more_drives_than_bays_rejected(s6029):
    with pytest.raises(ValueError, match="only 8 bays"):
        cc.build_geometry(with_array(s6029, [{"count": 9, "size": "3.5"}]))


def test_35_drive_in_25_bays_rejected(cfg):
    """R640 ships 2.5in bays: a 3.5in drive physically cannot slot in."""
    p = with_array(cfg["servers"]["R640"], [{"count": 1, "size": "3.5"}])
    with pytest.raises(ValueError, match="3.5in drives need 3.5in bays"):
        cc.build_geometry(p)


def test_1u_grid_overflow_rejected(cfg):
    """Eight 3.5in drives in a 1U: only 4 flat bays fit one row and a 1U
    cannot stack a second - a CLEAR error, never overlapping boxes."""
    p = with_array(cfg["servers"]["R640"], [{"count": 8, "size": "3.5"}],
                   drive_bay_size="3.5", drive_bay_count=8)
    with pytest.raises(ValueError, match="do not fit the front bay grid"):
        cc.build_geometry(p)


def test_chassis_too_low_even_flat_rejected(s6029):
    p = with_array(s6029, [{"count": 1, "size": "3.5"}],
                   chassis_height=0.025, drive_bay_count=1)
    with pytest.raises(ValueError, match="even lying flat"):
        cc.build_geometry(p)


def test_drive_array_requires_drive_zone(s6029):
    p = with_array(s6029, [{"count": 1, "size": "2.5"}])
    del p["drive_zone_z"]
    with pytest.raises(ValueError, match="drive_zone_z"):
        cc.build_geometry(p)


def test_unknown_size_rejected_at_build(s6029):
    with pytest.raises(ValueError, match="unknown drive size"):
        cc.build_geometry(with_array(s6029, [{"count": 1, "size": "m.2"}]))


def test_validator_rejects_planted_drive_box_overlap():
    """_validate_geometry treats drive_boxes as porous zones: overlaps
    with solids (and the fan wall straddle) are rejected."""
    geo = {
        "dims": (0.4, 0.1, 0.8), "fan_z": 0.3,
        "drives": None, "cpus": [], "solids": [
            ("block", (0.05, 0.01, 0.05, 0.15, 0.09, 0.15))],
        "extra_porous": [], "labels": {}, "solid_telem": {},
        "fan_marks": [], "optics_box": None, "optics_custom": False,
        "n_bays": 1,
        "drive_boxes": [{"name": "drive_1",
                         "box": (0.10, 0.01, 0.10, 0.20, 0.09, 0.20)}],
    }
    with pytest.raises(ValueError, match="overlaps solid"):
        cc._validate_geometry(geo)
    geo["solids"] = []
    geo["drive_boxes"][0]["box"] = (0.1, 0.01, 0.25, 0.2, 0.09, 0.35)
    with pytest.raises(ValueError, match="straddles the fan wall"):
        cc._validate_geometry(geo)


# --- fan operating point -----------------------------------------------------

def test_population_monotonically_loads_the_fan(cfg, s6029):
    """Empty bays are really open: fewer drives => less impedance => more
    delivered flow. empty > partial > full, and every point stays on the
    fan curve (0 < q < qmax)."""
    fan = cfg["fans"]["supermicro-fan-0118l4"]
    q = {}
    for n in (0, 3, 8):
        p = with_array(s6029, [{"count": n, "size": "3.5"}])
        geo = cc.build_geometry(p)
        r = cc.fan_operating_point(p, fan, geo, duty=1.0)
        assert 0.0 < r["q_op"] < r["qmax"]
        q[n] = r["q_op"]
    assert q[0] > q[3] > q[8]


def test_heavier_class_loads_the_fan_more(cfg, s6029):
    """Same count, bigger drives (x1.15 zeta, larger frontal area) must
    pass less air than 2.5in drives (x0.75) - the DRIVE_TYPE_ZETA physics
    carried into the discrete path."""
    fan = cfg["fans"]["supermicro-fan-0118l4"]
    q = {}
    for size in ("2.5", "3.5"):
        p = with_array(s6029, [{"count": 4, "size": size}])
        q[size] = cc.fan_operating_point(
            p, fan, cc.build_geometry(p), duty=1.0)["q_op"]
    assert q["2.5"] > q["3.5"]


# --- viz / summary plumbing --------------------------------------------------

def test_viz_geometry_ships_discrete_drives(s6029):
    geo = cc.build_geometry(with_array(s6029, MIX))
    comps = {c["name"]: c for c in cc._viz_geometry(geo)["components"]}
    for i in range(1, 7):
        c = comps[f"drive_{i}"]
        assert c["kind"] == "porous"
        assert c["label"] == geo["labels"][f"drive_{i}"]
    assert "drive_array" not in comps      # no monolith in discrete mode


def test_drive_mix_summary_counts(s6029):
    geo = cc.build_geometry(with_array(s6029, MIX))
    assert cc.drive_mix_summary(geo) == [("2.5in NVMe/SAS", 2),
                                         ("3.5in HDD", 4)]
    assert cc.drive_mix_summary(cc.build_geometry(s6029)) == []


# --- apply_hw_overrides ------------------------------------------------------

def test_hw_drive_mix_owns_the_array(s6029):
    out = cc.apply_hw_overrides(copy.deepcopy(s6029),
                                {"drive_mix": "2x2.5+4x3.5"})
    assert out["drive_array"] == [
        {"count": 2, "size": "2.5in NVMe/SAS"},
        {"count": 4, "size": "3.5in HDD"}]
    geo = cc.build_geometry(out)
    assert geo["drive_mode"] == "discrete" and geo["n_drives"] == 6


def test_hw_drive_mix_list_form(s6029):
    out = cc.apply_hw_overrides(copy.deepcopy(s6029), {"drive_mix": MIX})
    assert out["drive_array"][0]["size"] == "2.5in NVMe/SAS"


def test_hw_drive_mix_disables_legacy_drive_type_scaling(s6029):
    """drive_type must NOT double-scale drive_zeta once a mix owns the
    bay - each class carries its own DRIVE_TYPE_ZETA multiplier."""
    z0 = s6029["drive_zeta"]
    out = cc.apply_hw_overrides(copy.deepcopy(s6029),
                                {"drive_mix": MIX,
                                 "drive_type": "3.5in HDD"})
    assert out["drive_zeta"] == z0
    # without the mix the legacy scaling still applies, bit for bit
    out = cc.apply_hw_overrides(copy.deepcopy(s6029),
                                {"drive_type": "3.5in HDD"})
    assert out["drive_zeta"] == pytest.approx(z0 * 1.15)


def test_hw_empty_mix_depopulates(s6029):
    out = cc.apply_hw_overrides(copy.deepcopy(s6029), {"drive_mix": ""})
    assert out["drive_array"] == []
    geo = cc.build_geometry(out)
    assert geo["drive_mode"] == "discrete" and geo["drive_boxes"] == []


def test_hw_bad_mix_dies_at_prompt_time(s6029):
    with pytest.raises(ValueError):
        cc.apply_hw_overrides(copy.deepcopy(s6029),
                              {"drive_mix": "garbage"})


def test_hw_bay_count_override_is_the_capacity(s6029):
    """The already-wired drive_bay_count hw key is the capacity the mix
    must fit: raising it admits more drives, lowering it rejects them."""
    hw = {"drive_bay_count": 4, "drive_mix": [{"count": 6, "size": "2.5"}]}
    out = cc.apply_hw_overrides(copy.deepcopy(s6029), hw)
    with pytest.raises(ValueError, match="only 4 bays"):
        cc.build_geometry(out)
