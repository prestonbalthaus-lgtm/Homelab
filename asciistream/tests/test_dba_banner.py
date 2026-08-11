"""The acoustic verdict banner: noise and thermal judged SEPARATELY.

Regression cover for a real bug. `dba_overheat` is a thermal check that
fires whenever the exhaust exceeds requirements.outlet_temp_max_c - it says
nothing about whether the noise ceiling caused it. The banner used to
attribute every overheat to the ceiling, so a 90 dBA limit that never
constrained the fans got blamed for an overheat it did not cause, and told
the user to "raise the dBA limit" when the fans were already flat out.

The two questions:
  NOISE   pass when simulated combined dBA <= target. A ceiling above what
          the fan wall can even produce is NOT BINDING, which is a pass.
  THERMAL attributable to the ceiling only when `dba_target_binding`.
"""
import io

import pytest

from conftest import cc

from rich.console import Console


def render(summary, width=120):
    out = cc.dba_thermal_banner(summary)
    if out is None:
        return None
    c = Console(file=io.StringIO(), width=width, legacy_windows=False,
                no_color=True)
    c.print(out)
    return " ".join(c.file.getvalue().split())


BASE = {"outlet_temp_max_c": 40.0, "dba_exhaust_basis": "bulk",
        "fan_duty": 1.0, "dba_target_achievable": True,
        "dba_target_duty_cap": 1.5, "fan_duty_requested": 1.0}


def case(**kw):
    return dict(BASE, **kw)


# --- no target set -----------------------------------------------------------

def test_no_target_renders_nothing():
    assert cc.dba_thermal_banner({}) is None
    assert cc.dba_thermal_banner(None) is None
    assert cc.dba_thermal_banner({"dba_target": None}) is None


def test_missing_temperatures_render_nothing():
    assert cc.dba_thermal_banner({"dba_target": 45.0}) is None
    assert cc.dba_thermal_banner(
        {"dba_target": 45.0, "dba_exhaust_c": 30.0}) is None


# --- THE BUG: a high, non-binding ceiling must not be blamed -----------------

def test_high_ceiling_under_target_is_a_noise_pass():
    """90 dBA ceiling, 75 dBA simulated: the noise check PASSES. This is
    the exact scenario that used to read as a failure."""
    txt = render(case(dba_target=90.0, dba_combined=75.0,
                      dba_target_met=True, dba_target_binding=False,
                      dba_exhaust_c=30.0, dba_overheat=False))
    assert "NOISE: OK" in txt
    assert "OVER TARGET" not in txt
    assert "not a binding constraint" in txt


def test_nonbinding_ceiling_is_not_blamed_for_an_overheat():
    """Fans at full duty and it still cooks: the ceiling is innocent, and
    telling the user to raise it would be useless advice."""
    txt = render(case(dba_target=90.0, dba_combined=75.0,
                      dba_target_met=True, dba_target_binding=False,
                      dba_exhaust_c=82.0, dba_overheat=True))
    assert "NOISE: OK" in txt                     # noise still passed
    assert "THERMAL WARNING" in txt               # thermal still flagged
    assert "FULL duty" in txt
    assert "not the cause" in txt
    assert "Raise the dBA limit" not in txt, \
        "must not advise raising a ceiling that was never binding"


def test_binding_ceiling_IS_blamed_when_it_starves_the_box():
    """The mirror case: a ceiling that really did cap the fans and cause
    the overheat must say so, and raising it IS the right advice."""
    txt = render(case(dba_target=45.0, dba_combined=45.0,
                      dba_target_met=True, dba_target_binding=True,
                      dba_target_duty_cap=0.371,
                      dba_exhaust_c=82.0, dba_overheat=True))
    assert "THERMAL WARNING" in txt
    assert "starves the chassis" in txt
    assert "Raise the dBA limit" in txt
    assert "not the cause" not in txt


# --- direction of the noise comparison --------------------------------------

@pytest.mark.parametrize("target,combined,met", [
    (90.0, 75.0, True),    # comfortably under
    (75.0, 75.0, True),    # exactly at the ceiling
    (45.0, 45.0, True),
    (75.0, 76.0, False),   # over
    (45.0, 66.5, False),
])
def test_noise_verdict_follows_simulated_vs_target(target, combined, met):
    txt = render(case(dba_target=target, dba_combined=combined,
                      dba_target_met=met, dba_target_binding=False,
                      dba_exhaust_c=30.0, dba_overheat=False))
    assert ("NOISE: OK" in txt) is met
    assert ("OVER TARGET" in txt) is (not met)


def test_a_louder_ceiling_never_turns_a_pass_into_a_failure():
    """Monotonicity of the verdict: raising the ceiling with the same
    simulated noise can only keep it passing."""
    prev = None
    for target in (70.0, 80.0, 90.0, 120.0):
        met = 75.0 <= target
        txt = render(case(dba_target=target, dba_combined=75.0,
                          dba_target_met=met, dba_target_binding=False,
                          dba_exhaust_c=30.0, dba_overheat=False))
        ok = "NOISE: OK" in txt
        if prev is not None:
            assert ok >= prev, "a higher ceiling must not fail after passing"
        prev = ok


# --- unreachable ceiling ----------------------------------------------------

def test_unreachable_ceiling_is_reported_as_such():
    txt = render(case(dba_target=1.0, dba_combined=1.47,
                      dba_target_met=False, dba_target_achievable=False,
                      dba_target_binding=True,
                      dba_exhaust_c=90.0, dba_overheat=True))
    assert "UNREACHABLE" in txt
    assert "duty floor" in txt


# --- basis is always disclosed ----------------------------------------------

@pytest.mark.parametrize("basis,needle", [
    ("solved", "solved energy equation"),
    ("bulk", "bulk balance"),
])
def test_exhaust_basis_is_stated(basis, needle):
    txt = render(case(dba_target=90.0, dba_combined=75.0,
                      dba_target_met=True, dba_target_binding=False,
                      dba_exhaust_c=30.0, dba_overheat=False,
                      dba_exhaust_basis=basis))
    assert needle in txt


# --- the underlying solver flags agree with the banner ----------------------

def test_solver_marks_a_high_ceiling_as_non_binding(cfg):
    """End of the chain: for every rated fan, a 90 dBA target must come
    back unconstrained - never a failure."""
    for name, fan in cfg["fans"].items():
        if not fan.get("max_dBA"):
            continue
        for n in (1, 4, 6, 8):
            info = cc.solve_duty_for_dba(90.0, fan["max_dBA"], n)
            assert info["solvable"], name
            assert info["achievable"], name
            assert not info["constrained"], f"{name} x{n} at 90 dBA"
            eff, comp = cc.apply_dba_ceiling(1.0, 90.0, fan["max_dBA"], n)
            assert eff == pytest.approx(1.0), f"{name} x{n} duty was capped"
            assert not comp["binding"], f"{name} x{n} reported binding"
