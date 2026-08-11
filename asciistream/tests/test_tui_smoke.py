"""TUI smoke tests: build every dashboard panel headlessly and prove it
renders. This is the guard that keeps the terminal UI alive while the
engine underneath it changes.

Two consoles per check:
  * a plain one (no ANSI) whose output we can assert substrings against;
  * a forced-truecolor one that exercises the 24-bit CFD colormap paths.

The ASCII isometric chassis panel (build_chassis_iso_panel) gets the most
attention - it is the only renderer that can be embedded in the plain-text
report, so it must never break.
"""
import io

import numpy as np
import pytest
from rich.console import Console

from conftest import PROFILE_NAMES, cc

NCOLS = 96          # main-pane columns used by the fixtures below


def plain_render(renderable, width=118):
    con = Console(file=io.StringIO(), width=width)
    con.print(renderable)
    return con.file.getvalue()


def color_render(renderable, width=118):
    con = Console(file=io.StringIO(), width=width, force_terminal=True,
                  color_system="truecolor")
    con.print(renderable)
    return con.file.getvalue()


def render_both(renderable, width=118):
    out = plain_render(renderable, width)
    assert out.strip(), "renderable produced no output"
    assert color_render(renderable, width).strip()
    return out


def make_speed(shape, seed=11):
    """Plausible sampled |u| field: positive values with a NaN patch
    (solid regions sample NaN in the real pipeline)."""
    rng = np.random.default_rng(seed)
    speed = rng.uniform(0.05, 1.4, size=shape)
    speed[: max(1, shape[0] // 6), : max(1, shape[1] // 8)] = np.nan
    return speed


@pytest.fixture()
def geo(cfg, request):
    name = getattr(request, "param", "6029U")
    return cc.build_geometry(cfg["servers"][name])


# --- banner -------------------------------------------------------------------

def test_banner_block_art_and_narrow_fallback():
    for width in (200, 24):        # block letters, then one-line fallback
        con = Console(file=io.StringIO(), width=width, force_terminal=True,
                      color_system="truecolor")
        cc.render_banner(con)
        assert con.file.getvalue().strip()


# --- geometry canvas + main particle panel ------------------------------------

@pytest.mark.parametrize("geo", PROFILE_NAMES, indirect=True)
def test_geometry_canvas_and_main_panel(geo):
    canvas = cc.build_geometry_canvas(geo, cc.MAIN_ROWS, NCOLS)
    particles = cc.ParticleField(geo, NCOLS)
    particles.update_fields(np.full((cc.MAIN_ROWS, NCOLS), 0.1),
                            np.full((cc.MAIN_ROWS, NCOLS), 0.4),
                            make_speed((cc.MAIN_ROWS, NCOLS)), 1.2)
    for _ in range(5):
        particles.step()
    scene = {"canvas": canvas, "particles": particles,
             "display_name": "smoke-test server",
             "status": {"t": 1.2, "t_total": 30.0, "step": 12,
                        "steps": 300, "q_out": 0.05, "cells": 25_008}}
    out = render_both(cc.build_main_panel(scene))
    assert "TOP-DOWN FLOW" in out
    assert "q_out=" in out


def test_geometry_canvas_marks_fan_wall(geo):
    canvas = cc.build_geometry_canvas(geo, cc.MAIN_ROWS, NCOLS)
    text = "\n".join(str(t) for t in canvas.render_lines())
    assert "FAN WALL" in text.replace("[ ", "").replace(" ]", "")


# --- front/rear mini panels ---------------------------------------------------

def test_mini_panel_with_and_without_bays(geo):
    speed = make_speed((8, cc.MINI_COLS))
    out = render_both(cc.build_mini_panel(speed, 1.2, "FRONT INLET",
                                          n_bays=geo["n_bays"]))
    assert "FRONT INLET" in out
    if geo["n_bays"]:
        assert f"{geo['n_bays']} bays" in out
    out = render_both(cc.build_mini_panel(speed, 1.2, "REAR EXHAUST"))
    assert "REAR EXHAUST" in out
    assert "bays" not in out


def test_mini_panel_all_nan_field_renders():
    speed = np.full((8, cc.MINI_COLS), np.nan)
    render_both(cc.build_mini_panel(speed, 1.2, "FRONT INLET", n_bays=4))


# --- the isometric chassis panel (report-embeddable - must never break) -------

@pytest.mark.parametrize("geo", PROFILE_NAMES, indirect=True)
def test_iso_panel_renders_every_profile(geo):
    speed = make_speed((cc.MAIN_ROWS, NCOLS))
    panel = cc.build_chassis_iso_panel(speed, geo, 1.2, max_cols=110,
                                       max_rows=34)
    out = render_both(panel, width=118)
    assert "FAN WALL" in out
    assert "FRONT" in out and "REAR" in out
    assert "|u|" in out                      # colour-bar caption


def test_iso_panel_labels_components(geo):
    speed = make_speed((cc.MAIN_ROWS, NCOLS))
    out = plain_render(cc.build_chassis_iso_panel(speed, geo, 1.2, 140, 40),
                       width=150)
    assert "CPU" in out                      # heatsink labels stamped


def test_iso_panel_all_nan_field_renders(geo):
    speed = np.full((cc.MAIN_ROWS, NCOLS), np.nan)
    render_both(cc.build_chassis_iso_panel(speed, geo, 1.2, 110, 34))


def test_iso_panel_tiny_terminal_fallback(geo):
    out = plain_render(cc.build_chassis_iso_panel(
        make_speed((cc.MAIN_ROWS, NCOLS)), geo, 1.2, max_cols=18,
        max_rows=8), width=40)
    assert "terminal too small" in out


# --- legend, telemetry widgets, report tables ---------------------------------

def test_legend_renders():
    out = render_both(cc.build_legend())
    assert "flow:" in out


def test_sys_panel_renders_without_worker_sample():
    out = render_both(cc.build_sys_panel(None, [], [], 0, 118))
    assert "CFD WORKERS" in out


SUMMARY = {
    "q_out": 0.055, "q_front": -0.055, "q_fan": 0.055,
    "fan_op_cfm": 105.0, "fan_vz": 1.5,
    "p_min": -120.0, "p_min_at": (0.10, 0.05, 0.30),
    "dz_min": 0.20, "dz_min_at": (0.20, 0.04, 0.68),
    "dz_mean": 0.40,
    "components": [("cpu", "CPU 1", 0.60), ("cpu", "CPU 2", 0.20),
                   ("optics", "OPTICS", None)],
}


def test_requirements_table(cfg):
    reqs = cfg["servers"]["6029U"]["requirements"]
    out = render_both(cc.requirements_table(reqs, SUMMARY, 350.0))
    assert "PASS" in out or "FAIL" in out
    assert "Fan operating estimate" in out


def test_thermal_check_and_component_table(cfg):
    reqs = cfg["servers"]["6029U"]["requirements"]     # cpu min 0.5 m/s
    rows, fails = cc.thermal_check(reqs, SUMMARY)
    assert len(rows) == 3
    assert "CPU 2" in fails                 # 0.20 < 0.5 threshold
    assert "OPTICS" in fails                # None reading always fails
    assert "CPU 1" not in fails
    out = render_both(cc.component_table(rows))
    assert "PASS" in out and "FAIL" in out
    banners = cc.thermal_banners(fails)
    assert len(banners) == len(fails)
    assert "THERMAL WARNING" in render_both(banners[0])


def test_fan_telemetry_table_config_and_custom_fan(cfg):
    out = render_both(cc.fan_telemetry_table(
        cfg["fans"]["supermicro-fan-0118l4"], 4))
    assert "Supermicro" in out and "dBA" in out
    # custom fans have no rpm/dBA/wattage: the n/a branch must render
    out = render_both(cc.fan_telemetry_table(cc.custom_fan_cfg(95, 38), 4))
    assert "n/a" in out
