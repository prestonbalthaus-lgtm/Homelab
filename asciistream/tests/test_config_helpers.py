"""resolve_fan(), mesh_level_lc(), est_cells(), apply_hw_overrides() and
friends: the config layer every run flows through."""
import copy

import pytest

from conftest import PROFILE_NAMES, cc


# --- resolve_fan --------------------------------------------------------------

def test_resolve_config_fan(cfg):
    fan = cc.resolve_fan(cfg, {"fan": "supermicro-fan-0118l4"})
    assert fan is cfg["fans"]["supermicro-fan-0118l4"]
    assert fan["max_cfm"] > 0 and fan["max_mmh2o"] > 0


def test_resolve_custom_fan_from_cli_strings(cfg):
    fan = cc.resolve_fan(cfg, {"fan": "custom", "fan_cfm": "95",
                               "fan_mmh2o": "38"})
    assert fan["max_cfm"] == pytest.approx(95.0)
    assert fan["max_mmh2o"] == pytest.approx(38.0)
    assert "Custom Fan" in fan["display"]
    # custom fans carry no rpm/dBA/wattage - the telemetry table prints n/a
    assert "rpm" not in fan and "max_dBA" not in fan


def test_resolve_custom_fan_missing_specs_exits(cfg):
    with pytest.raises(SystemExit):
        cc.resolve_fan(cfg, {"fan": "custom"})
    with pytest.raises(SystemExit):
        cc.resolve_fan(cfg, {"fan": "custom", "fan_cfm": "95",
                             "fan_mmh2o": "lots"})


@pytest.mark.parametrize("cfm,mmh2o", [(0, 38), (-5, 38), (95, 0), (95, -1)])
def test_resolve_custom_fan_nonpositive_specs_exit(cfg, cfm, mmh2o):
    with pytest.raises(SystemExit):
        cc.resolve_fan(cfg, {"fan": "custom", "fan_cfm": cfm,
                             "fan_mmh2o": mmh2o})


def test_custom_fan_cfg_display():
    fan = cc.custom_fan_cfg(95, 38)
    assert fan["display"] == "Custom Fan (95 CFM / 38 mmH2O)"


# --- mesh_level_lc ------------------------------------------------------------

def test_mesh_presets_resolve_from_profile(cfg):
    s = cfg["servers"]["6029U"]
    assert cc.mesh_level_lc(s, "coarse") == pytest.approx(0.015)
    assert cc.mesh_level_lc(s, "medium") == pytest.approx(0.008)
    assert cc.mesh_level_lc(s, "fine") == pytest.approx(0.004)
    assert cc.mesh_level_lc(s, "ultra") == pytest.approx(0.0025)


def test_mesh_literal_mm_value(cfg):
    s = cfg["servers"]["6029U"]
    assert cc.mesh_level_lc(s, "0.8") == pytest.approx(0.0008)
    assert cc.mesh_level_lc(s, 2.0) == pytest.approx(0.002)


def test_mesh_floor_is_inclusive(cfg):
    s = cfg["servers"]["6029U"]
    assert cc.mesh_level_lc(s, "0.5") == pytest.approx(
        cc.MESH_MM_FLOOR / 1000.0)


@pytest.mark.parametrize("level", ["0.49", "0.1", "-3", "0", "nan"])
def test_mesh_below_floor_or_nan_exits(cfg, level):
    with pytest.raises(SystemExit, match="floor"):
        cc.mesh_level_lc(cfg["servers"]["6029U"], level)


def test_mesh_unknown_preset_exits(cfg):
    with pytest.raises(SystemExit, match="--mesh must be"):
        cc.mesh_level_lc(cfg["servers"]["6029U"], "extreme")


def test_mesh_settings_fallback_for_legacy_config():
    """A JSON that predates the mesh_settings block still resolves presets
    through DEFAULT_MESH_SETTINGS."""
    assert cc.mesh_level_lc({}, "coarse") == pytest.approx(0.015)


def test_mesh_desc():
    assert cc.mesh_desc("coarse", 0.015) == "coarse preset"
    assert cc.mesh_desc("0.8", 0.0008) == "0.8 mm custom"


# --- est_cells ----------------------------------------------------------------

@pytest.mark.parametrize("name", PROFILE_NAMES)
def test_est_cells_positive_for_all_profiles(cfg, name):
    s = cfg["servers"][name]
    geo = cc.build_geometry(s)
    assert cc.est_cells(geo, cc.mesh_level_lc(s, "coarse")) > 0


def test_est_cells_cubic_scaling(cfg):
    geo = cc.build_geometry(cfg["servers"]["6029U"])
    assert cc.est_cells(geo, 0.005) / cc.est_cells(geo, 0.010) == \
        pytest.approx(8.0)


def test_est_cells_monotone_in_lc(cfg):
    geo = cc.build_geometry(cfg["servers"]["6029U"])
    sizes = [0.0025, 0.004, 0.008, 0.015]
    counts = [cc.est_cells(geo, lc) for lc in sizes]
    assert counts == sorted(counts, reverse=True)


def test_est_cells_solids_reduce_fluid_volume(cfg):
    geo = cc.build_geometry(cfg["servers"]["6029U"])
    hollow = dict(geo, solids=[])
    assert cc.est_cells(hollow, 0.015) > cc.est_cells(geo, 0.015)


def test_est_cells_6029u_coarse_calibration(cfg):
    """Docstring calibration: 6029U coarse actual 25,008 cells vs ~22.7k
    estimated. Lock the estimate into a generous band so a silent change
    to the estimator (or the chassis volume) is caught."""
    s = cfg["servers"]["6029U"]
    est = cc.est_cells(cc.build_geometry(s), cc.mesh_level_lc(s, "coarse"))
    assert 15_000 < est < 35_000, f"6029U coarse estimate drifted: {est:.0f}"


# --- apply_hw_overrides -------------------------------------------------------

@pytest.fixture()
def s6029(cfg):
    return copy.deepcopy(cfg["servers"]["6029U"])


def test_no_overrides_is_identity(s6029):
    before = copy.deepcopy(s6029)
    assert cc.apply_hw_overrides(s6029, {}) == before


def test_drive_type_scales_zeta(s6029):
    z0 = s6029["drive_zeta"]
    out = cc.apply_hw_overrides(copy.deepcopy(s6029),
                                {"drive_type": "3.5in HDD"})
    assert out["drive_zeta"] == pytest.approx(z0 * 1.15)
    assert out["drive_bay_type"] == "3.5in HDD"
    out = cc.apply_hw_overrides(copy.deepcopy(s6029),
                                {"drive_type": "2.5in NVMe/SAS"})
    assert out["drive_zeta"] == pytest.approx(z0 * 0.75)


def test_drive_type_ignored_without_bays(s6029):
    s6029["drive_bay_count"] = 0
    z0 = s6029["drive_zeta"]
    out = cc.apply_hw_overrides(s6029, {"drive_type": "3.5in HDD"})
    assert out["drive_zeta"] == z0


def test_heat_and_temperature_overrides(s6029):
    out = cc.apply_hw_overrides(s6029, {"heat_load_w": 500,
                                        "inlet_temp_c": 18,
                                        "exhaust_temp_c": 45})
    assert out["heat_load"] == pytest.approx(500.0)
    assert out["requirements"]["inlet_temp_c"] == pytest.approx(18.0)
    assert out["requirements"]["outlet_temp_max_c"] == pytest.approx(45.0)


def test_gpu_overrides_slots_and_heat(s6029):
    base_heat = s6029["heat_load"]
    out = cc.apply_hw_overrides(s6029, {"gpu_count": 3, "gpu_watts": 250})
    assert out["populated_pcie_slots"] == 3
    assert out["heat_load"] == pytest.approx(base_heat + 3 * 250.0)


def test_gpu_count_clamped_to_eight(s6029):
    out = cc.apply_hw_overrides(s6029, {"gpu_count": 12, "gpu_watts": 100})
    assert out["populated_pcie_slots"] == 8


def test_nic_only_means_exactly_one_card(s6029):
    """A NIC and no GPU is ONE card, not the profile default plus one.

    This assertion was inverted before: it pinned `min(n0 + 1, 8)`, i.e.
    3 cards on the 6029U, because the old code incremented whatever the
    profile shipped with. Answering "no GPUs, yes NIC" would then mesh
    MORE hardware than the untouched profile - the same root cause as the
    PCIe ghosting bug (the wizard's answers did not own the population).
    The test encoded the defect rather than the requirement, so it moves
    with the fix."""
    out = cc.apply_hw_overrides(s6029, {"nic": True})
    assert out["populated_pcie_slots"] == 1
    assert out["nic_slot"] is True
    geo = cc.build_geometry(out)
    cards = [n for n, _b in geo["solids"] if n.startswith("pcie_card_")]
    assert cards == ["pcie_card_1"]
    assert geo["labels"]["pcie_card_1"] == "NIC"


def test_no_gpu_and_no_nic_leaves_zero_cards(s6029):
    """The reported ghosting bug: answering 'no' to both prompts must
    leave the PCIe zone EMPTY, not fall back to the profile default.
    Only the static risers may remain in the domain."""
    n0 = s6029["populated_pcie_slots"]
    assert n0 > 0, "fixture must start with cards, or this proves nothing"
    out = cc.apply_hw_overrides(s6029, {"nic": False})
    assert out["populated_pcie_slots"] == 0
    assert out["nic_slot"] is False
    geo = cc.build_geometry(out)
    cards = [n for n, _b in geo["solids"] if n.startswith("pcie_card_")]
    risers = [n for n, _b in geo["solids"] if "riser" in n]
    assert cards == [], f"ghost PCIe cards still in the domain: {cards}"
    assert risers, "static risers must survive when the cards are gone"
    assert not any("PCIe" in str(v) for v in geo["labels"].values())


@pytest.mark.parametrize("gpus,nic,expect", [
    (0, False, 0), (0, True, 1), (1, False, 1),
    (2, False, 2), (2, True, 3), (8, True, 8),   # clamped at 8
])
def test_pcie_population_is_exactly_what_was_answered(s6029, gpus, nic,
                                                      expect):
    """Card count comes from the answers alone, never from the profile."""
    hw = {"nic": nic}
    if gpus:
        hw.update(gpu_count=gpus, gpu_watts=100.0)
    out = cc.apply_hw_overrides(copy.deepcopy(s6029), hw)
    assert out["populated_pcie_slots"] == expect
    geo = cc.build_geometry(out)
    cards = [n for n, _b in geo["solids"] if n.startswith("pcie_card_")]
    assert len(cards) == expect


def test_gpu_ignored_without_pcie_zone(s6029):
    del s6029["pcie_zone_z"]
    base_heat = s6029["heat_load"]
    n0 = s6029["populated_pcie_slots"]
    out = cc.apply_hw_overrides(s6029, {"gpu_count": 4, "gpu_watts": 300,
                                        "nic": True})
    assert out["heat_load"] == base_heat
    assert out["populated_pcie_slots"] == n0
    assert "nic_slot" not in out


def test_overridden_profile_still_builds(s6029):
    out = cc.apply_hw_overrides(s6029, {"drive_type": "3.5in HDD",
                                        "gpu_count": 2, "gpu_watts": 300,
                                        "nic": True, "heat_load_w": 800})
    geo = cc.build_geometry(out)          # must not raise
    cards = [n for n, _b in geo["solids"] if n.startswith("pcie_card_")]
    assert len(cards) == 3                # 2 GPUs + 1 NIC
