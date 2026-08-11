"""Acoustic dBA-target mode + the enterprise (Dell/HPE) fan library.

These tests call the PRODUCTION functions directly - solve_duty_for_dba,
apply_dba_ceiling and combined_noise_dba are module-level and pure numpy,
so they import on the host with no solver stack. No formula replicas here
beyond the algebraic round-trip check: the inversion is verified by
feeding its result back through the SAME forward laws the codebase uses
(fan_operating_point's dBA + 50*log10(duty) and combined_noise_dba's
+10*log10(n_fans)).

    duty_cap = 10 ** ((target - dBA_rated - 10*log10(n_fans)) / 50)
    effective duty = min(requested --fan-duty, duty_cap)   (quieter wins)

The Dell PowerEdge / HPE ProLiant fan entries are CLASS-REPRESENTATIVE
engineering estimates (labelled so in their display strings) - the tests
enforce the schema, the honesty label, the class physics (40 mm
dual-rotor: high pressure/RPM, low flow; 60 mm: more flow, less
pressure; High-Perf > Std within a family) and a sane operating point on
every profile.
"""
import copy
import math

import pytest

from conftest import PROFILE_NAMES, cc

# supermicro-fan-0118l4 on the 6029U: max_dBA 60.5, fan_count 4 - the
# regression-gated baseline pairing used throughout this file.
BASE_FAN_DBA = 60.5
BASE_N_FANS = 4

NEW_FANS = [
    "dell-1u-std-40mm", "dell-1u-hp-40mm",
    "dell-2u-std-60mm", "dell-2u-hp-60mm",
    "hpe-dl360g10-hp-40mm", "hpe-dl380g10-std-60mm",
    "hpe-dl380g10-hp-60mm",
]
NEW_FANS_40MM = ["dell-1u-std-40mm", "dell-1u-hp-40mm",
                 "hpe-dl360g10-hp-40mm"]
NEW_FANS_60MM = ["dell-2u-std-60mm", "dell-2u-hp-60mm",
                 "hpe-dl380g10-std-60mm", "hpe-dl380g10-hp-60mm"]
HP_STD_PAIRS = [("dell-1u-hp-40mm", "dell-1u-std-40mm"),
                ("dell-2u-hp-60mm", "dell-2u-std-60mm"),
                ("hpe-dl380g10-hp-60mm", "hpe-dl380g10-std-60mm")]


def combined_at(duty, dba_rated=BASE_FAN_DBA, n_fans=BASE_N_FANS):
    """Forward law THROUGH the production functions: per-fan dBA at duty
    via the affinity law, then the free-field combination."""
    per_fan = dba_rated + 50.0 * math.log10(duty)
    return cc.combined_noise_dba(per_fan, n_fans)


# --- solve_duty_for_dba: the inversion ---------------------------------------

@pytest.mark.parametrize("target", [30.0, 45.0, 55.0, 62.0])
def test_inversion_round_trips_through_forward_law(target):
    """Feeding the solved duty back through the forward laws must land on
    the target EXACTLY (algebraic inverse, in-band cases)."""
    s = cc.solve_duty_for_dba(target, BASE_FAN_DBA, BASE_N_FANS)
    assert s["solvable"] and s["achievable"] and s["constrained"]
    assert s["duty_cap"] == pytest.approx(s["duty_unclamped"], rel=1e-12)
    assert combined_at(s["duty_cap"]) == pytest.approx(target, abs=1e-9)


def test_target_above_full_band_imposes_no_constraint():
    """Edge case: a target above the fan's output across the WHOLE duty
    band -> no constraint (cap sits at the band top; the default duty of
    1.0 passes through untouched)."""
    loud = combined_at(cc.FAN_DUTY_MAX) + 10.0
    s = cc.solve_duty_for_dba(loud, BASE_FAN_DBA, BASE_N_FANS)
    assert s["solvable"] and s["achievable"]
    assert not s["constrained"]
    assert s["duty_cap"] == pytest.approx(cc.FAN_DUTY_MAX)
    assert s["duty_unclamped"] > cc.FAN_DUTY_MAX
    eff, info = cc.apply_dba_ceiling(1.0, loud, BASE_FAN_DBA, BASE_N_FANS)
    assert eff == 1.0
    assert not info["binding"]


def test_unachievable_target_reports_plainly_never_below_stall():
    """Edge case: a target below what even minimum duty emits ->
    achievable False, and the cap is FAN_DUTY_MIN (the quietest the model
    allows), never an impossible sub-stall duty."""
    s = cc.solve_duty_for_dba(1.0, BASE_FAN_DBA, BASE_N_FANS)
    assert s["solvable"]
    assert not s["achievable"]
    assert s["duty_unclamped"] < cc.FAN_DUTY_MIN
    assert s["duty_cap"] == pytest.approx(cc.FAN_DUTY_MIN)
    assert combined_at(s["duty_cap"]) > 1.0   # honestly still over target


def test_unrated_custom_fan_is_not_solvable():
    """Edge case: a custom fan with no max_dBA cannot be solved for - no
    guessed numbers, and the ceiling passes the requested duty through
    (the worker then refuses loudly)."""
    fan = cc.custom_fan_cfg(95, 38)
    assert "max_dBA" not in fan
    s = cc.solve_duty_for_dba(45.0, fan.get("max_dBA"), BASE_N_FANS)
    assert not s["solvable"]
    assert s["duty_cap"] is None and s["duty_unclamped"] is None
    eff, info = cc.apply_dba_ceiling(0.8, 45.0, fan.get("max_dBA"),
                                     BASE_N_FANS)
    assert eff == 0.8
    assert not info["binding"] and not info["solvable"]


def test_lower_target_never_permits_higher_duty():
    """Monotonicity: as the ceiling tightens the permitted duty must be
    non-increasing (strictly decreasing while in-band)."""
    caps = [cc.solve_duty_for_dba(t, BASE_FAN_DBA, BASE_N_FANS)["duty_cap"]
            for t in (70.0, 66.0, 60.0, 55.0, 50.0, 45.0, 40.0, 10.0, 0.0)]
    for hi, lo in zip(caps, caps[1:]):
        assert lo <= hi
    in_band = [c for c in caps
               if cc.FAN_DUTY_MIN < c < cc.FAN_DUTY_MAX]
    for hi, lo in zip(in_band, in_band[1:]):
        assert lo < hi


def test_more_fans_lower_permitted_duty():
    """The +10*log10(N) combination must be accounted for: the same
    target on more fans allows LESS duty per fan (a 4-fan wall at the cap
    of a 1-fan solve would sit ~6 dBA over the limit)."""
    one = cc.solve_duty_for_dba(45.0, BASE_FAN_DBA, 1)["duty_cap"]
    four = cc.solve_duty_for_dba(45.0, BASE_FAN_DBA, 4)["duty_cap"]
    eight = cc.solve_duty_for_dba(45.0, BASE_FAN_DBA, 8)["duty_cap"]
    assert eight < four < one
    # 4 fans at the 1-fan cap would exceed the target by exactly
    # 10*log10(4) dBA - the bug the fan-count term prevents
    assert combined_at(one, n_fans=4) == pytest.approx(
        45.0 + 10.0 * math.log10(4), abs=1e-9)


# --- composition with an explicit --fan-duty ---------------------------------

def test_ceiling_caps_a_louder_explicit_duty():
    """--fan-duty above the ceiling: the NOISE LIMIT wins (binding)."""
    cap = cc.solve_duty_for_dba(45.0, BASE_FAN_DBA,
                                BASE_N_FANS)["duty_cap"]
    eff, info = cc.apply_dba_ceiling(1.2, 45.0, BASE_FAN_DBA, BASE_N_FANS)
    assert eff == pytest.approx(cap)
    assert info["binding"]


def test_quieter_explicit_duty_is_honoured():
    """--fan-duty already below the ceiling: the explicit duty wins and
    the target is NOT binding."""
    eff, info = cc.apply_dba_ceiling(0.2, 45.0, BASE_FAN_DBA, BASE_N_FANS)
    assert eff == 0.2
    assert not info["binding"]


@pytest.mark.parametrize("req", [0.1, 0.3712, 0.5, 0.8, 1.0, 1.25, 1.5])
def test_effective_duty_never_exceeds_an_achievable_target(req):
    """For every requested duty, the combined noise at the effective duty
    stays at or under an achievable target - the ceiling is never
    silently exceeded."""
    eff, info = cc.apply_dba_ceiling(req, 45.0, BASE_FAN_DBA, BASE_N_FANS)
    assert info["achievable"]
    assert eff <= req
    assert combined_at(eff) <= 45.0 + 1e-9


def test_target_reduces_operating_cfm(cfg):
    """The point of the mode: a binding dBA target must actually lower
    the fan operating point (CFM ~ duty by the affinity algebra)."""
    s = cfg["servers"]["6029U"]
    fan = cfg["fans"]["supermicro-fan-0118l4"]
    geo = cc.build_geometry(s)
    eff, info = cc.apply_dba_ceiling(1.0, 45.0, fan["max_dBA"],
                                     s["fan_count"])
    assert info["binding"]
    rated = cc.fan_operating_point(s, fan, geo, duty=1.0)
    capped = cc.fan_operating_point(s, fan, geo, duty=eff)
    assert capped["cfm"] < rated["cfm"]
    assert capped["cfm"] == pytest.approx(rated["cfm"] * eff, rel=1e-9)
    # and the noise the capped point emits actually meets the target
    assert cc.combined_noise_dba(capped["dba"],
                                 s["fan_count"]) <= 45.0 + 1e-9


def test_forward_law_matches_fan_operating_point(cfg):
    """combined_noise_dba over fan_operating_point's per-fan dBA is the
    single forward law - no drift between the two."""
    s = cfg["servers"]["6029U"]
    fan = cfg["fans"]["supermicro-fan-0118l4"]
    geo = cc.build_geometry(s)
    r = cc.fan_operating_point(s, fan, geo, duty=0.6)
    assert cc.combined_noise_dba(r["dba"], s["fan_count"]) == \
        pytest.approx(fan["max_dBA"] + 50.0 * math.log10(0.6)
                      + 10.0 * math.log10(s["fan_count"]), abs=1e-9)
    assert cc.combined_noise_dba(None, s["fan_count"]) is None


# --- the enterprise fan library ----------------------------------------------

@pytest.mark.parametrize("fan_name", NEW_FANS)
def test_new_fan_schema_and_estimate_label(cfg, fan_name):
    """Every new entry carries the full schema of the existing six fans,
    positive values, and an explicit estimate label in its display string
    (these are class-representative curves, NOT vendor data - the label
    is the honesty contract)."""
    for source in (cfg["fans"], cc.DEFAULT_CONFIG["fans"]):
        fan = source[fan_name]
        for key in ("display", "max_cfm", "max_mmh2o", "rpm", "max_dBA",
                    "max_wattage"):
            assert key in fan, f"{fan_name} missing {key}"
        for key in ("max_cfm", "max_mmh2o", "rpm", "max_dBA",
                    "max_wattage"):
            assert fan[key] > 0
        assert "est" in fan["display"].lower()


def test_new_fans_in_sync_between_default_config_and_json(cfg):
    """DEFAULT_CONFIG mirrors server_configs.json - the fans sections
    must stay identical (load_config auto-writes DEFAULT_CONFIG when the
    JSON is missing, so drift would fork the two worlds)."""
    assert cc.DEFAULT_CONFIG["fans"] == cfg["fans"]


@pytest.mark.parametrize("profile", PROFILE_NAMES, indirect=True)
@pytest.mark.parametrize("fan_name", NEW_FANS)
def test_new_fans_sane_operating_point_on_every_profile(cfg, profile,
                                                        fan_name):
    """0 < q_op < qmax for every new fan on every profile: parses, and
    the curve/impedance intersection stays physical."""
    geo = cc.build_geometry(profile)
    r = cc.fan_operating_point(profile, cfg["fans"][fan_name], geo)
    assert 0.0 < r["q_op"] < r["qmax"]
    assert 0.0 < r["fan_vz"] < 1000.0


def test_new_fans_class_physics(cfg):
    """40 mm dual-rotor 1U class: much higher static pressure than any
    60 mm fan but less flow; 60 mm 2U class: more flow at lower
    pressure. High-Perf beats Std on every column within a family."""
    fans = cfg["fans"]
    max_cfm_40 = max(fans[k]["max_cfm"] for k in NEW_FANS_40MM)
    min_cfm_60 = min(fans[k]["max_cfm"] for k in NEW_FANS_60MM)
    assert max_cfm_40 < min_cfm_60
    min_p_40 = min(fans[k]["max_mmh2o"] for k in NEW_FANS_40MM)
    max_p_60 = max(fans[k]["max_mmh2o"] for k in NEW_FANS_60MM)
    assert min_p_40 > max_p_60
    for hp, std in HP_STD_PAIRS:
        for key in ("max_cfm", "max_mmh2o", "rpm", "max_dBA",
                    "max_wattage"):
            assert fans[hp][key] > fans[std][key], (hp, std, key)


def test_new_fans_solve_a_dba_target(cfg):
    """End-to-end on a new entry: a living-room 45 dBA target on the
    R640's 8-fan wall with the Dell 1U High-Perf fan produces a binding
    in-band duty cap and a quieter operating point."""
    s = cfg["servers"]["R640"]
    fan = cfg["fans"]["dell-1u-hp-40mm"]
    geo = cc.build_geometry(s)
    eff, info = cc.apply_dba_ceiling(1.0, 45.0, fan["max_dBA"],
                                     s["fan_count"])
    assert info["solvable"] and info["achievable"] and info["binding"]
    assert cc.FAN_DUTY_MIN < eff < 1.0
    r = cc.fan_operating_point(s, fan, geo, duty=eff)
    assert 0.0 < r["q_op"] < r["qmax"]
    assert cc.combined_noise_dba(r["dba"],
                                 s["fan_count"]) <= 45.0 + 1e-9


def test_rated_baseline_untouched(cfg):
    """The dBA machinery must not perturb the frozen duty-1.0 baseline:
    6029U + supermicro-fan-0118l4 stays at 129.0 CFM -> 1.77 m/s."""
    s = cfg["servers"]["6029U"]
    r = cc.fan_operating_point(s, cfg["fans"]["supermicro-fan-0118l4"],
                               cc.build_geometry(s), duty=1.0)
    assert r["cfm"] == pytest.approx(129.0, abs=0.05)
    assert r["fan_vz"] == pytest.approx(1.77, abs=0.005)
