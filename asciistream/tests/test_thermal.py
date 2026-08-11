"""Energy-equation host layer: the thermal gate (worker args key
"thermal"), the config "heat_w" plumbing, and thermal_heat_plan() - the
pure watts->regions mapping the in-container solver turns into volumetric
sources. Everything here runs with numpy alone (the import-hygiene
invariant); the actual advection-diffusion solve is container-only and is
exercised by the in-container regression runs, not this suite.

The load-bearing constraint under test: GPUs fitted through the wizard
are meshed as SOLID PCIe cards, which the CSG step CUTS OUT of the fluid
domain - there are no cells inside a card, so its wattage must ship to
the 1 cm washing SHELL around it (plan key "cards"), never as an in-card
volumetric source. Porous zones (CPU sinks, C4130-style GPU bays) do have
cells and take volumetric watts directly.
"""
import copy

import pytest

from conftest import PROFILE_NAMES, cc


# --- thermal_enabled: the args gate ------------------------------------------

@pytest.mark.parametrize("val", [None, "", "0", "off", "OFF", "false", "no"])
def test_thermal_gate_off_values(val):
    assert cc.thermal_enabled({"thermal": val}) is False


def test_thermal_gate_missing_key_is_off():
    assert cc.thermal_enabled({}) is False


@pytest.mark.parametrize("val", ["1", "on", "ON", "true", "yes", True])
def test_thermal_gate_on_values(val):
    assert cc.thermal_enabled({"thermal": val}) is True


@pytest.mark.parametrize("val", ["maybe", "2", "enable", "0.5"])
def test_thermal_gate_garbage_exits(val):
    with pytest.raises(SystemExit, match="--thermal"):
        cc.thermal_enabled({"thermal": val})


# --- apply_hw_overrides: the wizard's GPU watts survive separately ------------

@pytest.fixture()
def s6029(cfg):
    return copy.deepcopy(cfg["servers"]["6029U"])


def test_gpu_heat_w_recorded_next_to_folded_total(s6029):
    base = s6029["heat_load"]
    out = cc.apply_hw_overrides(s6029, {"gpu_count": 2, "gpu_watts": 300})
    assert out["heat_load"] == pytest.approx(base + 600.0)
    assert out["gpu_heat_w"] == pytest.approx(600.0)


def test_gpu_heat_w_absent_without_gpus(s6029):
    out = cc.apply_hw_overrides(copy.deepcopy(s6029), {"nic": True})
    assert "gpu_heat_w" not in out          # NIC-only: no GPU wattage
    out = cc.apply_hw_overrides(copy.deepcopy(s6029), {})
    assert "gpu_heat_w" not in out


# --- build_geometry: heat_w plumbing ------------------------------------------

def _porous_zone(**kw):
    z = {"name": "belly_heater", "type": "porous",
         "box": [0.02, 0.0, 0.28, 0.09, 0.03, 0.33],
         "zeta": 10.0, "permeability": 1e-6}
    z.update(kw)
    return z


def test_heat_w_passes_through_to_extra_porous(s6029):
    s6029.setdefault("custom_zones", []).append(_porous_zone(heat_w=50))
    geo = cc.build_geometry(s6029)
    z = next(z for z in geo["extra_porous"] if z["name"] == "belly_heater")
    assert z["heat_w"] == pytest.approx(50.0)
    # zones without the key carry an explicit None (PSU impedance blocks)
    psu = next(z for z in geo["extra_porous"] if z["name"] == "psu_1")
    assert psu["heat_w"] is None


def test_negative_heat_w_rejected(s6029):
    s6029.setdefault("custom_zones", []).append(_porous_zone(heat_w=-1))
    with pytest.raises(ValueError, match="heat_w"):
        cc.build_geometry(s6029)


def test_heat_w_on_solid_zone_rejected(s6029):
    """A solid is CUT OUT of the fluid domain - a volumetric source in it
    would land in a region with no cells. Refuse loudly at build time."""
    s6029.setdefault("custom_zones", []).append(
        {"name": "hot_brick", "type": "solid",
         "box": [0.02, 0.0, 0.28, 0.09, 0.03, 0.33], "heat_w": 100})
    with pytest.raises(ValueError, match="no cells"):
        cc.build_geometry(s6029)


# --- thermal_heat_plan --------------------------------------------------------

def _plan(server_cfg, engine="3d"):
    return cc.thermal_heat_plan(server_cfg, cc.build_geometry(server_cfg),
                                engine=engine)


def test_plan_6029u_default_all_to_cpu_sinks(cfg):
    s = cfg["servers"]["6029U"]
    plan = _plan(s)
    assert plan["total_w"] == pytest.approx(s["heat_load"])
    assert plan["explicit"] == []           # PSU zones carry no heat_w
    assert plan["cards"] == []              # no wizard GPUs in the JSON
    assert plan["implicit_tags"] == [(cc.VOL_CPUS, "cpu_heatsinks")]
    assert plan["rest_w"] == pytest.approx(s["heat_load"])
    assert plan["uniform_fallback"] is False


def test_plan_wizard_gpus_go_to_card_shells(s6029):
    base = s6029["heat_load"]
    out = cc.apply_hw_overrides(s6029, {"gpu_count": 2, "gpu_watts": 300,
                                        "nic": True})
    plan = _plan(out)
    # 3 cards meshed, but the NIC (last slot) must not receive GPU watts
    names = [n for n, _b, _w in plan["cards"]]
    assert names == ["pcie_card_1", "pcie_card_2"]
    assert all(w == pytest.approx(300.0) for _n, _b, w in plan["cards"])
    assert plan["rest_w"] == pytest.approx(base)      # CPUs keep the rest
    assert plan["total_w"] == pytest.approx(base + 600.0)


def test_plan_zero_watt_gpus_add_no_card_sources(s6029):
    out = cc.apply_hw_overrides(s6029, {"gpu_count": 2, "gpu_watts": 0})
    plan = _plan(out)
    assert plan["cards"] == []
    assert plan["rest_w"] == pytest.approx(plan["total_w"])


def test_plan_c4130_porous_gpu_bays_take_volumetric_watts(cfg):
    """The C4130 models its GPUs as POROUS custom zones - those HAVE cells,
    so they take explicit volumetric watts, not the shell path."""
    plan = _plan(cfg["servers"]["C4130"])
    gpu_w = sorted((n, w) for _t, n, w in plan["explicit"])
    assert gpu_w == [(f"gpu_{i}", pytest.approx(300.0))
                     for i in range(1, 5)]
    assert plan["cards"] == []
    assert (cc.VOL_CPUS, "cpu_heatsinks") in plan["implicit_tags"]
    assert plan["rest_w"] == pytest.approx(1500.0 - 1200.0)


def test_plan_asr_router_pins_everything_explicitly(cfg):
    """No CPU sinks, no telemetry zones: without the explicit heat_w split
    the router would fall back to a uniform whole-domain source."""
    plan = _plan(cfg["servers"]["ASR1006X"])
    watts = {n: w for _t, n, w in plan["explicit"]}
    assert watts == {"linecard_bay": pytest.approx(1250.0),
                     "rp_esp_bay": pytest.approx(550.0)}
    assert plan["implicit_tags"] == []
    assert plan["rest_w"] == pytest.approx(0.0)
    assert plan["uniform_fallback"] is False


def test_plan_switch_splits_optics_and_asic(cfg):
    plan = _plan(cfg["servers"]["A7050X3"])
    assert {n: w for _t, n, w in plan["explicit"]} == {
        "optics_cage": pytest.approx(115.0)}
    assert plan["implicit_tags"] == [(cc.VOL_CPUS, "cpu_heatsinks")]
    assert plan["rest_w"] == pytest.approx(350.0 - 115.0)


def test_plan_atx_gpu_zone_explicit_cpu_tower_rest(cfg):
    plan = _plan(cfg["servers"]["ATX-MID"])
    assert {n: w for _t, n, w in plan["explicit"]} == {
        "gpu_card": pytest.approx(250.0)}
    assert plan["implicit_tags"] == [(cc.VOL_CPUS, "cpu_heatsinks")]
    assert plan["rest_w"] == pytest.approx(450.0 - 250.0)


def test_plan_uniform_fallback_when_nothing_carries_heat(s6029):
    s6029["cpu_sockets"] = 0
    s6029["custom_zones"] = []
    plan = _plan(s6029)
    assert plan["implicit_tags"] == []
    assert plan["rest_w"] == pytest.approx(s6029["heat_load"])
    assert plan["uniform_fallback"] is True


def test_plan_2d_folds_out_of_slice_zone_into_rest(s6029):
    """A zone that does not straddle the mid-height plane has NO cells in
    the 2-D domain: its watts must fold back into the remainder, never
    vanish into a region the planar mesh does not contain."""
    s6029.setdefault("custom_zones", []).append(_porous_zone(heat_w=50))
    plan3 = _plan(copy.deepcopy(s6029), engine="3d")
    assert {n for _t, n, _w in plan3["explicit"]} == {"belly_heater"}
    assert plan3["rest_w"] == pytest.approx(s6029["heat_load"] - 50.0)
    plan2 = _plan(s6029, engine="2d")
    assert plan2["explicit"] == []          # zone absent from the slice
    assert plan2["rest_w"] == pytest.approx(s6029["heat_load"])


def test_plan_2d_c4130_matches_3d(cfg):
    """The C4130 GPU bays and CPU sinks all straddle mid-height, so the
    planar engine keeps the identical watts map."""
    s = cfg["servers"]["C4130"]
    p3, p2 = _plan(s, "3d"), _plan(s, "2d")
    assert p2["explicit"] == p3["explicit"]
    assert p2["implicit_tags"] == p3["implicit_tags"]
    assert p2["rest_w"] == pytest.approx(p3["rest_w"])


@pytest.mark.parametrize("name", PROFILE_NAMES)
@pytest.mark.parametrize("engine", ["3d", "2d"])
def test_plan_conserves_heat_load_everywhere(cfg, name, engine):
    """Explicit + card + remainder watts must reproduce heat_load exactly
    for every shipped profile in both engines (no watts lost, none
    invented) - and every planned tag must exist in the geometry."""
    s = cfg["servers"][name]
    geo = cc.build_geometry(s)
    plan = cc.thermal_heat_plan(s, geo, engine=engine)
    total = (sum(w for _t, _n, w in plan["explicit"])
             + sum(w for _n, _b, w in plan["cards"]) + plan["rest_w"])
    assert total == pytest.approx(plan["total_w"])
    assert plan["total_w"] == pytest.approx(s["heat_load"])
    valid_tags = {cc.VOL_CPUS} | {z["tag"] for z in geo["extra_porous"]}
    for tag, _n, _w in plan["explicit"]:
        assert tag in valid_tags
    for tag, _n in plan["implicit_tags"]:
        assert tag in valid_tags


def test_plan_mutates_nothing(cfg):
    s = copy.deepcopy(cfg["servers"]["C4130"])
    geo = cc.build_geometry(s)
    snap_s, snap_geo = copy.deepcopy(s), copy.deepcopy(geo)
    cc.thermal_heat_plan(s, geo, engine="2d")
    assert s == snap_s and geo == snap_geo
