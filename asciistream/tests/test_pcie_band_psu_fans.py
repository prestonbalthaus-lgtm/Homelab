"""pcie_x_band / pcie_max_slots / pcie_card_count / fan_momentum: the
C4130 + R640 geometry overhaul.

Covers the four seams added for the decibel-target branch:
  A. pcie_x_band - the optional per-profile card band build_geometry lays
     PCIe cards into (absent = the legacy full-width layout, bit for bit).
  B. C4130 - front-mounted GPU bays, side riser cages, 0..2 cards and
     0 drive bays all validate.
  C. R640 - 0.25 m deep PSUs and the fan_momentum source keys
     (psu_fan_dp); unflagged profiles must NOT grow momentum sources.
  D. apply_hw_overrides pcie_card_count - the wizard's explicit card
     population, clamped to pcie_max_slots.
"""
import copy

import pytest

from conftest import PROFILE_NAMES, cc


def cards_of(geo):
    return [(n, b) for n, b in geo["solids"] if n.startswith("pcie_card_")]


# --- A. pcie_x_band -----------------------------------------------------------

@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_band_absent_keeps_legacy_layout_bit_identical(cfg, name):
    """Profiles without pcie_x_band must place cards EXACTLY where the old
    hardcoded full-width formula put them (the meshed cell count is a
    pinned regression gate - even a last-ulp shift is a change)."""
    s = cfg["servers"][name]
    if s.get("pcie_x_band") is not None:
        pytest.skip("profile sets a band - covered by the band tests")
    n = int(s.get("populated_pcie_slots", 0))
    if n == 0:
        pytest.skip("no cards populated")
    W, H = float(s["chassis_width"]), float(s["chassis_height"])
    pz0, pz1 = s["pcie_zone_z"]
    side, gap = 0.02, 0.02
    card_w = (W - 2 * side - (n - 1) * gap) / n
    expect = [(side + i * (card_w + gap), 0.12 * H, float(pz0),
               side + i * (card_w + gap) + card_w, 0.82 * H, float(pz1))
              for i in range(n)]
    got = [b for _n, b in cards_of(cc.build_geometry(s))]
    assert got == expect


def test_band_constrains_cards_inside_it(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["pcie_x_band"] = [0.10, 0.30]
    s["populated_pcie_slots"] = 2
    for _n, b in cards_of(cc.build_geometry(s)):
        assert b[0] >= 0.10 - 1e-12 and b[3] <= 0.30 + 1e-12
    # edge-to-edge: first card starts AT the band, last ends AT the band
    boxes = [b for _n, b in cards_of(cc.build_geometry(s))]
    assert boxes[0][0] == pytest.approx(0.10)
    assert boxes[-1][3] == pytest.approx(0.30)


@pytest.mark.parametrize("band", [
    [0.3, 0.1],          # inverted
    [-0.05, 0.3],        # escapes at x < 0
    [0.1, 9.9],          # escapes at x > W
    [0.2, 0.2],          # degenerate
])
def test_band_malformed_rejected_even_with_zero_cards(cfg, band):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["pcie_x_band"] = band
    s["populated_pcie_slots"] = 0     # validated regardless of population
    with pytest.raises(ValueError, match="pcie_x_band"):
        cc.build_geometry(s)


def test_band_not_a_pair_rejected(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["pcie_x_band"] = [0.1]
    with pytest.raises(ValueError, match="pcie_x_band"):
        cc.build_geometry(s)


def test_band_too_narrow_for_cards_rejected(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["pcie_x_band"] = [0.10, 0.15]      # 50 mm
    s["populated_pcie_slots"] = 3        # 2 gaps of 20 mm -> 3.3 mm cards
    with pytest.raises(ValueError, match="too narrow"):
        cc.build_geometry(s)
    s["populated_pcie_slots"] = 2        # 15 mm cards - fine
    assert len(cards_of(cc.build_geometry(s))) == 2


# --- B. C4130 -----------------------------------------------------------------

def test_c4130_gpus_front_mounted(cfg):
    """GPU bays live in the front compute section: after the drive cage
    (z=0.10), before the fan wall (z=0.22), porous, still 300 W each."""
    s = cfg["servers"]["C4130"]
    geo = cc.build_geometry(s)
    dz1 = float(s["drive_zone_z"][1])
    gpus = [z for z in geo["extra_porous"] if z["name"].startswith("gpu_")]
    assert len(gpus) == 4
    for z in gpus:
        b = z["box"]
        assert b[2] >= dz1 - 1e-12, f"{z['name']} inside the drive cage"
        assert b[5] <= geo["fan_z"] + 1e-12, \
            f"{z['name']} crosses the fan wall"
        assert z["heat_w"] == pytest.approx(300.0)
        assert z["telemetry"] == "gpu"


@pytest.mark.parametrize("bays", [0, 2])
@pytest.mark.parametrize("n_cards", [0, 1, 2])
def test_c4130_builds_with_any_drive_and_card_population(cfg, bays, n_cards):
    """The reported failure: populated_pcie_slots 1..2 used to die on the
    mid-width risers, then on the rear GPU bays. Every drive x card combo
    must now validate."""
    s = copy.deepcopy(cfg["servers"]["C4130"])
    s["drive_bay_count"] = bays
    s["populated_pcie_slots"] = n_cards
    geo = cc.build_geometry(s)
    assert len(cards_of(geo)) == n_cards
    assert (geo["drives"] is None) == (bays == 0)


def test_c4130_risers_at_chassis_sides(cfg):
    """Real C4130 riser cages sit at the chassis sides - and the card band
    must keep the runtime cards clear of them AND of the mid PSU bank."""
    s = cfg["servers"]["C4130"]
    W = float(s["chassis_width"])
    for spec in s["pcie_risers"]:
        x0, x1 = spec["x"]
        assert x1 <= 0.05 or x0 >= W - 0.05, \
            f"riser {spec['name']} is not at a chassis side: {spec['x']}"


def test_c4130_two_cards_clear_risers_and_psu(cfg):
    s = copy.deepcopy(cfg["servers"]["C4130"])
    s["populated_pcie_slots"] = 2
    geo = cc.build_geometry(s)
    psu = dict(geo["solids"])["psu_bank"]
    risers = [b for n, b in geo["solids"] if "riser" in n]
    for _n, cb in cards_of(geo):
        for other in risers + [psu]:
            overlap = all(min(cb[i + 3], other[i + 3])
                          - max(cb[i], other[i]) > 1e-6 for i in range(3))
            assert not overlap


def test_c4130_heat_plan_unchanged_by_the_move(cfg):
    """Front-mounting the GPUs must not change the thermal split: 4 x 300 W
    explicit + CPUs implicit + 300 W remainder, both engines."""
    s = cfg["servers"]["C4130"]
    geo = cc.build_geometry(s)
    for engine in ("3d", "2d"):
        plan = cc.thermal_heat_plan(s, geo, engine=engine)
        assert sorted(n for _t, n, _w in plan["explicit"]) == \
            [f"gpu_{i}" for i in range(1, 5)]
        assert plan["rest_w"] == pytest.approx(300.0)
        assert plan["total_w"] == pytest.approx(1500.0)


# --- C. R640 ------------------------------------------------------------------

def test_r640_psus_deep_and_clear(cfg):
    """PSUs reach the brief's 0.25 m depth and stay clear of the (edge)
    risers and the (inboard-banded) cards - proven by build_geometry not
    raising, and pinned here so a config regression is loud."""
    s = cfg["servers"]["R640"]
    geo = cc.build_geometry(s)
    psus = [z for z in geo["extra_porous"] if z["name"].startswith("psu_")]
    assert len(psus) == 2
    for z in psus:
        b = z["box"]
        assert b[5] - b[2] == pytest.approx(0.25)
        assert b[2] > geo["fan_z"]         # rear compartment, no straddle


def test_r640_risers_still_at_the_edges(cfg):
    """The bug report claimed the R640 risers float mid-rear - they do not
    (verified by execution); pin the correct outer-edge placement."""
    s = cfg["servers"]["R640"]
    W = float(s["chassis_width"])
    xs = sorted(tuple(spec["x"]) for spec in s["pcie_risers"])
    assert xs[0][1] <= 0.05 and xs[1][0] >= W - 0.05


def test_r640_psu_fan_momentum_keys(cfg):
    """fan_momentum zones carry the solver keys: dp from psu_fan_dp and
    the +z force density dp / L_z."""
    geo = cc.build_geometry(cfg["servers"]["R640"])
    for z in geo["extra_porous"]:
        assert z["name"].startswith("psu_")
        dp = cc.psu_fan_dp(15000, 40)
        assert z["fan_dp_pa"] == pytest.approx(dp)
        assert z["fan_force"] == pytest.approx(dp / 0.25)
        assert z["fan_rpm"] == 15000 and z["fan_size_mm"] == 40


@pytest.mark.parametrize("name", [n for n in PROFILE_NAMES if n != "R640"])
def test_only_r640_has_momentum_sources(cfg, name):
    """fan_rpm alone stays an annotation: no other profile may grow a
    momentum source, or the pinned 6029U regression gates would move."""
    geo = cc.build_geometry(cfg["servers"][name])
    assert all("fan_force" not in z for z in geo["extra_porous"])


def test_psu_fan_dp_scaling():
    """dp ~ rpm^2 and ~ D^2 (fan scaling laws), and the 40 mm / 15 krpm
    R640 unit lands in the published high-static-fan range."""
    dp = cc.psu_fan_dp(15000, 40)
    assert 50.0 < dp < 300.0
    assert cc.psu_fan_dp(30000, 40) == pytest.approx(4.0 * dp)
    assert cc.psu_fan_dp(15000, 80) == pytest.approx(4.0 * dp)


def test_fan_momentum_requires_fan_rpm(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["custom_zones"] = [{"name": "z", "type": "porous",
                          "box": [0.1, 0.01, 0.5, 0.2, 0.06, 0.6],
                          "zeta": 100.0, "permeability": 1e-7,
                          "fan_momentum": True}]
    with pytest.raises(ValueError, match="requires fan_rpm"):
        cc.build_geometry(s)


def test_fan_momentum_on_solid_rejected(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["custom_zones"] = [{"name": "z", "type": "solid",
                          "box": [0.1, 0.01, 0.5, 0.2, 0.06, 0.6],
                          "fan_rpm": 15000, "fan_momentum": True}]
    with pytest.raises(ValueError, match="only supported on porous"):
        cc.build_geometry(s)


def test_unflagged_zone_dicts_carry_no_fan_keys(cfg):
    """Zones without the flag must not even grow the keys - profile geo
    dicts (and anything hashing/comparing them) stay byte-identical."""
    geo = cc.build_geometry(cfg["servers"]["6029U"])
    for z in geo["extra_porous"]:
        for key in ("fan_rpm", "fan_size_mm", "fan_dp_pa", "fan_force"):
            assert key not in z


# --- D. pcie_card_count -------------------------------------------------------

def test_explicit_card_count_owns_population(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    out = cc.apply_hw_overrides(s, {"pcie_card_count": 4})
    assert out["populated_pcie_slots"] == 4
    assert len(cards_of(cc.build_geometry(out))) == 4


def test_explicit_count_supersedes_gpu_nic_derivation(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    out = cc.apply_hw_overrides(s, {"gpu_count": 1, "gpu_watts": 250,
                                    "nic": True, "pcie_card_count": 5})
    assert out["populated_pcie_slots"] == 5
    # the GPU wattage still folds into the heat load
    assert out["gpu_heat_w"] == pytest.approx(250.0)


def test_explicit_count_clamped_to_profile_max_slots(cfg):
    s = copy.deepcopy(cfg["servers"]["C4130"])       # pcie_max_slots: 2
    out = cc.apply_hw_overrides(s, {"pcie_card_count": 6})
    assert out["populated_pcie_slots"] == 2
    cc.build_geometry(out)                           # and it validates


def test_gpu_nic_derivation_also_respects_max_slots(cfg):
    s = copy.deepcopy(cfg["servers"]["C4130"])
    out = cc.apply_hw_overrides(s, {"gpu_count": 3, "gpu_watts": 300,
                                    "nic": True})
    assert out["populated_pcie_slots"] == 2
    cc.build_geometry(out)


def test_negative_card_count_rejected(cfg):
    with pytest.raises(ValueError, match="pcie_card_count"):
        cc.apply_hw_overrides(copy.deepcopy(cfg["servers"]["6029U"]),
                              {"pcie_card_count": -1})


def test_card_count_ignored_without_pcie_zone(cfg):
    s = copy.deepcopy(cfg["servers"]["6029U"])
    del s["pcie_zone_z"]
    n0 = s["populated_pcie_slots"]
    out = cc.apply_hw_overrides(s, {"pcie_card_count": 5})
    assert out["populated_pcie_slots"] == n0


def test_zero_count_leaves_only_risers(cfg):
    s = copy.deepcopy(cfg["servers"]["R640"])
    out = cc.apply_hw_overrides(s, {"pcie_card_count": 0})
    geo = cc.build_geometry(out)
    assert cards_of(geo) == []
    assert any("riser" in n for n, _b in geo["solids"])
