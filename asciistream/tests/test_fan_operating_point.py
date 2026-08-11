"""Fan operating point: the quadratic fan-curve / system-impedance
intersection computed in worker_main() before the solve.

The formula lives inline in worker_main (which imports mpi4py at entry, so
it cannot run on the host); this suite replicates it VERBATIM from
chassis_cfd.py using the module's own constants, pinning the physics:

    zeta_est = drive_zeta + 0.5*cpu_zeta + baseline_zeta
               + sum(zone zeta * covered cross-section fraction)
    K_est    = RHO_AIR * zeta_est / (2 * area^2)
    qmax     = max_cfm / M3S_TO_CFM * fan_count
    pmax     = max_mmh2o * 9.80665
    q_op     = sqrt(pmax / (K_est + pmax / qmax^2))

If a worker-side refactor changes the operating-point maths, this file is
the tripwire: update BOTH or justify the physics change.
"""
import copy
import math

import pytest

from conftest import FAN_NAMES, PROFILE_NAMES, cc


def operating_point(server_cfg, fan_cfg):
    """Replica of chassis_cfd.worker_main's estimate (see module docstring)."""
    geo = cc.build_geometry(server_cfg)
    W, H, _L = geo["dims"]
    area = W * H
    zeta_est = (float(server_cfg.get("drive_zeta", 0.0))
                + 0.5 * float(server_cfg.get("cpu_zeta", 0.0))
                + float(server_cfg.get("baseline_zeta", 25.0)))
    for z in geo["extra_porous"]:
        b = z["box"]
        afrac = (b[3] - b[0]) * (b[4] - b[1]) / area
        zeta_est += z["zeta"] * min(afrac, 1.0)
    K_est = cc.RHO_AIR * zeta_est / (2.0 * area**2)
    qmax = fan_cfg["max_cfm"] / cc.M3S_TO_CFM * server_cfg["fan_count"]
    pmax = fan_cfg["max_mmh2o"] * 9.80665
    q_op = float(math.sqrt(pmax / (K_est + pmax / qmax**2)))
    return q_op, qmax, area


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
