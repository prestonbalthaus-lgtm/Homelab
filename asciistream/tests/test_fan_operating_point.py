"""Fan operating point: the quadratic fan-curve / system-impedance
intersection, plus the Fan Affinity Law RPM scaling.

These tests call the PRODUCTION function `chassis_cfd.fan_operating_point`
directly - it is module-level and pure numpy, so it imports on the host
with no solver stack. There is deliberately no replica of the formula
here: a test that re-implements the maths it is meant to guard cannot
detect a regression in the real code.

    zeta_est = drive_zeta + 0.5*cpu_zeta + baseline_zeta
               + sum(zone zeta * covered cross-section fraction)
    K_est    = RHO_AIR * zeta_est / (2 * area^2)
    qmax     = max_cfm / M3S_TO_CFM * fan_count      (x duty)
    pmax     = max_mmh2o * 9.80665                   (x duty^2)
    q_op     = sqrt(pmax / (K_est + pmax / qmax^2))

Affinity laws: flow ~ N, pressure ~ N^2, shaft power ~ N^3, and
dBA ~ dBA_rated + 50*log10(N/N_rated).
"""
import copy
import math

import pytest

from conftest import FAN_NAMES, PROFILE_NAMES, cc


def operating_point(server_cfg, fan_cfg, duty=1.0):
    """Thin adapter over the production function, preserving this suite's
    (q_op, qmax, area) shape. Single source of truth: chassis_cfd."""
    geo = cc.build_geometry(server_cfg)
    W, H, _L = geo["dims"]
    r = cc.fan_operating_point(server_cfg, fan_cfg, geo, duty=duty)
    return r["q_op"], r["qmax"], W * H


@pytest.mark.parametrize("profile", PROFILE_NAMES, indirect=True)
@pytest.mark.parametrize("fan_name", FAN_NAMES)
def test_operating_point_physically_sane(cfg, profile, fan_name):
    """0 < q_op < qmax for EVERY profile x fan combination: the fan can
    never deliver more than free-air flow, and a finite-impedance chassis
    always passes some air."""
    q_op, qmax, area = operating_point(profile, cfg["fans"][fan_name])
    assert 0.0 < q_op < qmax
    # the fan-plane velocity the workers derive from it must be finite and
    # positive too
    assert 0.0 < q_op / area < 1000.0


@pytest.mark.parametrize("profile", PROFILE_NAMES, indirect=True)
def test_operating_point_falls_as_impedance_rises(cfg, profile):
    """Scaling every impedance source up must move the operating point
    DOWN the fan curve (monotonicity of the intersection)."""
    fan = cfg["fans"]["supermicro-fan-0118l4"]
    q_ref, _, _ = operating_point(profile, fan)
    hi = copy.deepcopy(profile)
    for key in ("drive_zeta", "cpu_zeta", "baseline_zeta"):
        if key in hi:
            hi[key] = float(hi[key]) * 4.0
    for zone in hi.get("custom_zones", []):
        if zone.get("type") == "porous":
            zone["zeta"] = float(zone["zeta"]) * 4.0
    q_hi, _, _ = operating_point(hi, fan)
    assert q_hi < q_ref


def test_operating_point_approaches_free_air_at_zero_impedance(cfg):
    """As chassis impedance -> 0, q_op -> qmax (free-air delivery) from
    below - never above."""
    s = copy.deepcopy(cfg["servers"]["6029U"])
    s["drive_bay_count"] = 0
    s["cpu_sockets"] = 0
    s["total_dimm_slots"] = 0
    s["populated_pcie_slots"] = 0
    s["custom_zones"] = []
    s["drive_zeta"] = 0.0
    s["cpu_zeta"] = 0.0
    s["baseline_zeta"] = 1e-9
    fan = cfg["fans"]["supermicro-fan-0118l4"]
    q_op, qmax, _ = operating_point(s, fan)
    assert q_op < qmax
    assert q_op == pytest.approx(qmax, rel=1e-3)


def test_operating_point_custom_fan_path(cfg):
    """The wizard/CLI custom fan flows through resolve_fan into the same
    formula and must give a sane operating point too."""
    fan = cc.resolve_fan(cfg, {"fan": "custom", "fan_cfm": "95",
                               "fan_mmh2o": "38"})
    q_op, qmax, _ = operating_point(cfg["servers"]["6029U"], fan)
    assert 0.0 < q_op < qmax


def test_stronger_fan_moves_more_air(cfg):
    """Same chassis, strictly stronger fan (more CFM AND more static
    pressure) => more delivered flow."""
    s = cfg["servers"]["6029U"]
    weak = cc.custom_fan_cfg(50, 20)
    strong = cc.custom_fan_cfg(100, 45)
    q_weak, _, _ = operating_point(s, weak)
    q_strong, _, _ = operating_point(s, strong)
    assert q_strong > q_weak


# --- Fan Affinity Laws -------------------------------------------------------
# Regression gate measured in-container on the pristine baseline: profile
# 6029U + supermicro-fan-0118l4 at rated RPM must stay at 129.0 CFM ->
# 1.77 m/s plane velocity. If this moves, the rated case has changed.
def test_rated_duty_matches_measured_baseline(cfg):
    s = cfg["servers"]["6029U"]
    r = cc.fan_operating_point(s, cfg["fans"]["supermicro-fan-0118l4"],
                               cc.build_geometry(s), duty=1.0)
    assert r["cfm"] == pytest.approx(129.0, abs=0.05)
    assert r["fan_vz"] == pytest.approx(1.77, abs=0.005)


@pytest.mark.parametrize("duty", [0.25, 0.5, 0.75, 1.0, 1.25])
def test_affinity_flow_scales_linearly_with_rpm(cfg, duty):
    """Q ~ N. Scaling qmax by d and pmax by d^2 makes the curve/impedance
    intersection scale EXACTLY linearly in d - the algebra cancels."""
    s = cfg["servers"]["6029U"]
    fan, geo = cfg["fans"]["arctic-s8038-10k"], cc.build_geometry(s)
    base = cc.fan_operating_point(s, fan, geo, duty=1.0)["q_op"]
    got = cc.fan_operating_point(s, fan, geo, duty=duty)["q_op"]
    assert got == pytest.approx(duty * base, rel=1e-9)


@pytest.mark.parametrize("duty", [0.5, 0.8, 1.2])
def test_affinity_power_cubes_and_dba_log_law(cfg, duty):
    """Shaft power ~ N^3 and dBA ~ dBA_rated + 50*log10(N/N_rated)."""
    s = cfg["servers"]["6029U"]
    fan, geo = cfg["fans"]["supermicro-fan-0118l4"], cc.build_geometry(s)
    rated = cc.fan_operating_point(s, fan, geo, duty=1.0)
    got = cc.fan_operating_point(s, fan, geo, duty=duty)
    assert got["watts"] == pytest.approx(rated["watts"] * duty**3, rel=1e-9)
    assert got["dba"] == pytest.approx(
        rated["dba"] + 50.0 * math.log10(duty), abs=1e-6)
    assert got["rpm"] == pytest.approx(rated["rpm_rated"] * duty, rel=1e-9)


def test_lower_duty_strictly_reduces_flow_and_noise(cfg):
    """Monotonicity: throttling a fan must not increase flow or noise."""
    s = cfg["servers"]["6029U"]
    fan, geo = cfg["fans"]["supermicro-fan-0118l4"], cc.build_geometry(s)
    prev = None
    for duty in (0.2, 0.4, 0.6, 0.8, 1.0):
        r = cc.fan_operating_point(s, fan, geo, duty=duty)
        if prev is not None:
            assert r["q_op"] > prev["q_op"]
            assert r["watts"] > prev["watts"]
            assert r["dba"] > prev["dba"]
        prev = r


def test_custom_fan_has_no_rated_telemetry(cfg):
    """An unrated custom fan cannot report scaled rpm/dBA/W - those must
    come back None rather than a fabricated number."""
    fan = cc.custom_fan_cfg(95, 38)
    s = cfg["servers"]["6029U"]
    r = cc.fan_operating_point(s, fan, cc.build_geometry(s), duty=0.6)
    assert r["q_op"] > 0.0
    for k in ("rpm_rated", "rpm", "watts", "dba"):
        assert r[k] is None, f"{k} should be None for an unrated fan"
