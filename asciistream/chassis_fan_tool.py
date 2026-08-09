#!/usr/bin/env python3
"""
================================================================================
 2U CHASSIS FAN ANALYZER - interactive P-Q operating-point tool with stall model
 Target platform: Supermicro SuperServer 6029U-E1CR4T (2U rackmount)
================================================================================

 An interactive CLI tool: enter any number of fans (name, free-delivery CFM,
 shut-off mmH2O), then the tool
   1. builds a cubic axial-fan P-Q curve for each fan that reproduces the two
      rated endpoints exactly AND carries a characteristic aerodynamic stall
      dip (local pressure minimum) between 30 % and 50 % of free delivery,
   2. intersects every fan curve with the 6029U chassis impedance curve
      dP = K * Q^2 to find the true operating point (scipy.optimize.brentq on
      every bracketed sign change; the highest-flow root is reported, i.e. the
      aerodynamically stable branch right of the stall region),
   3. prints an ASCII dashboard (operating CFM / mmH2O, fan-wall -> exhaust
      static pressure drop, exhaust temperature from Q = m_dot * cp * dT),
   4. renders the P-Q map with matplotlib: system impedance curve, all fan
      curves, and marked operating points (legend, grid, axis labels).

 Fan curve math
 --------------
 With qn = Q/Qmax, the curve is the cubic
     P(qn) = Pmax * (1 - g(qn) / g(1)),
     g(q)  = q^3/3 - (q1+q2)/2 * q^2 + q1*q2 * q
 whose derivative is proportional to -(q - q1)(q - q2). Hence by construction:
     P(0) = Pmax          (shut-off honoured exactly)
     P(Qmax) = 0          (free delivery honoured exactly)
     P'(q1) = P'(q2) = 0  (local STALL DIP exactly at q1, recovery hump at q2)
 q1 = STALL_DIP_Q (default 0.40, inside the required 30-50 % band) and
 q2 = STALL_RECOVERY_Q are config knobs; the dip depth follows from the
 endpoint constraints (a cubic has no freedom left) and is validated positive.

 Console commands at the fan prompt:
   done   run the simulation with the fans entered so far
   demo   (hidden) instantly load the 5 reference fans (Supermicro FAN-0118L4,
          ARCTIC S8038-7K/10K, SilverStone FHS80X 24V/12V)

 The companion script chassis_airflow_sim.py holds the full 3-D
 advection-diffusion thermal solver; this tool focuses on P-Q analysis.

 Dependencies: numpy, scipy, matplotlib. Runs headless too (no display ->
 the chart is saved as PNG and the interactive window is skipped).
================================================================================
"""

import os
import sys

import numpy as np
from scipy.optimize import brentq

import matplotlib
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        or sys.platform in ("win32", "darwin")):
    matplotlib.use("Agg")          # headless session: save the PNG, skip the window
import matplotlib.pyplot as plt

# ==============================================================================
#  CONFIGURATION  --  edit everything in this block
# ==============================================================================

# --- Environment --------------------------------------------------------------
AMBIENT_TEMP_C   = 22.0    # inlet / room air temperature                [degC]
RELATIVE_HUM_PCT = 50.0    # relative humidity (reported)                [%]
RHO_AIR          = 1.196   # working air density                         [kg/m^3]
CP_AIR           = 1006.0  # specific heat of air at const. pressure     [J/(kg K)]

# --- Chassis geometry: Supermicro SuperServer 6029U-E1CR4T (2U) ---------------
CHASSIS_MODEL = "Supermicro SuperServer 6029U-E1CR4T (2U Rackmount)"
CHASSIS_W  = 0.43          # x - width                                   [m]
CHASSIS_H  = 0.08          # y - height (2U)                             [m]
CHASSIS_L  = 0.70          # z - length; inlet z=0, exhaust z=L          [m]
FAN_WALL_Z = 0.10          # z position of the internal fan wall         [m]

# --- Thermal load -------------------------------------------------------------
SYSTEM_HEAT_LOAD_W = 10.0  # total heat rejected into the airstream      [W]

# --- Chassis impedance profile (6029U-E1CR4T) ---------------------------------
# (name, z_start, z_end, zeta): loss coefficient referenced to the mean
# superficial duct velocity Q/A, spread uniformly over [z_start, z_end].
# SUM(zeta) = 177 puts the chassis at ~20 mmH2O @ 100 CFM (dense 2U platform).
# Dominant blockage: front drive bay + SAS backplane (z 0.15-0.30); secondary:
# CPU heatsinks + DIMM field (z 0.35-0.50). Rear grille includes exit KE loss.
IMPEDANCE_ZONES = [
    #  name                              z0 [m]  z1 [m]  zeta
    ("Front bezel / intake grille",       0.00,   0.05,  10.0),
    ("Intake plenum",                     0.05,   0.10,   1.0),
    # ---------------- fan wall at z = FAN_WALL_Z = 0.10 ----------------
    ("Fan-wall discharge plenum",         0.10,   0.15,   2.0),
    ("Drive bays + SAS backplane",        0.15,   0.30,  95.0),
    ("Mid-chassis plenum",                0.30,   0.35,   2.0),
    ("CPU heatsinks + DIMM banks",        0.35,   0.50,  55.0),
    ("PCIe risers / VRM field",           0.50,   0.60,   4.0),
    ("Rear I/O + PSU exhaust grille",     0.60,   0.70,   8.0),
]

# --- Axial-fan stall shape ----------------------------------------------------
STALL_DIP_Q      = 0.40    # local pressure MINIMUM (the stall dip) at this
                           #   fraction of Qmax; keep inside 0.30-0.50
STALL_RECOVERY_Q = 0.62    # local recovery MAXIMUM at this fraction of Qmax
                           # (0 < STALL_DIP_Q < STALL_RECOVERY_Q < 1; the dip
                           #  depth follows from the cubic's endpoint fit and
                           #  is validated to stay above P = 0)
FANS_IN_PARALLEL = 1       # identical fans side by side (6029U wall holds 4);
                           # parallel fans multiply Qmax at equal pressure

# --- Hidden 'demo' preset (loaded when 'demo' is typed at the prompt) ---------
DEMO_FANS = [
    {"name": "Supermicro FAN-0118L4",    "max_cfm": 100.0,  "max_mmh2o": 44.95},
    {"name": "ARCTIC S8038-7K",          "max_cfm":  70.0,  "max_mmh2o": 25.0},
    {"name": "ARCTIC S8038-10K",         "max_cfm": 102.0,  "max_mmh2o": 51.0},
    {"name": "SilverStone FHS80X 24V",   "max_cfm":  70.3,  "max_mmh2o": 39.5},
    {"name": "SilverStone FHS80X (12V)", "max_cfm":  83.66, "max_mmh2o": 50.77},
]

# --- Numerics -----------------------------------------------------------------
CURVE_POINTS     = 600     # samples per plotted curve
ROOT_SCAN_POINTS = 2001    # residual samples for intersection bracketing

# --- Plot styling (light-mode tokens; categorical order is CVD-validated) -----
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"]  # fixed order - never cycled
COL_SURFACE, COL_INK, COL_INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
COL_MUTED, COL_GRID, COL_AXIS  = "#898781", "#e1e0d9", "#c3c2b7"
PLOT_FILE = "pq_curves.png"

# ==============================================================================
#  END CONFIGURATION
# ==============================================================================


# --- Unit conversions (SI internally; CFM / mmH2O at the boundaries only) -----
CFM_TO_M3S  = 0.3048**3 / 60.0      # 1 CFM  = 4.719474e-4 m^3/s (exact)
MMH2O_TO_PA = 9.80665               # 1 mmH2O = 9.80665 Pa (conventional)

def to_m3s(cfm):    return cfm * CFM_TO_M3S
def to_cfm(q):      return q / CFM_TO_M3S
def to_pa(mmh2o):   return mmh2o * MMH2O_TO_PA
def to_mmh2o(pa):   return pa / MMH2O_TO_PA

AREA = CHASSIS_W * CHASSIS_H        # duct cross-section [m^2]


# ==============================================================================
#  Chassis impedance
# ==============================================================================

def split_zeta():
    """Total loss coefficient upstream / downstream of the fan wall
    (zones straddling the wall are split pro-rata by length)."""
    up = down = 0.0
    for _name, z0, z1, zeta in IMPEDANCE_ZONES:
        lu = max(0.0, min(z1, FAN_WALL_Z) - z0)
        ld = max(0.0, z1 - max(z0, FAN_WALL_Z))
        up   += zeta * lu / (z1 - z0)
        down += zeta * ld / (z1 - z0)
    return up, down


def impedance_K(zeta_total):
    """System constant K of dP = K*Q^2, in Pa/(m^3/s)^2."""
    return RHO_AIR * zeta_total / (2.0 * AREA**2)


def system_dp(q, K):
    """Chassis impedance curve dP(Q) [Pa]; q in m^3/s (scalar or array)."""
    return K * np.asarray(q, dtype=float) ** 2


# ==============================================================================
#  Cubic axial-fan curve with aerodynamic stall dip
# ==============================================================================

def _stall_g(qn):
    """g(q) = q^3/3 - (q1+q2)/2*q^2 + q1*q2*q, the antiderivative of
    (q - q1)(q - q2) -> the curve's only extrema sit exactly at q1 and q2."""
    q1, q2 = STALL_DIP_Q, STALL_RECOVERY_Q
    return qn**3 / 3.0 - 0.5 * (q1 + q2) * qn**2 + q1 * q2 * qn


def fan_qmax_pmax(fan):
    """Effective free-delivery flow [m^3/s] and shut-off pressure [Pa].
    Parallel identical fans multiply flow at equal static pressure."""
    return to_m3s(fan["max_cfm"]) * FANS_IN_PARALLEL, to_pa(fan["max_mmh2o"])


def fan_dp(q, fan):
    """Cubic fan static pressure [Pa] at flow q [m^3/s] (scalar or array):
        P(qn) = Pmax * (1 - g(qn)/g(1)),  qn = q/Qmax
    Endpoints exact (P(0)=Pmax, P(Qmax)=0); stall dip at STALL_DIP_Q and
    recovery hump at STALL_RECOVERY_Q by construction."""
    qmax, pmax = fan_qmax_pmax(fan)
    qn = np.clip(np.asarray(q, dtype=float) / qmax, 0.0, 1.0)
    return pmax * (1.0 - _stall_g(qn) / _stall_g(1.0))


def find_operating_point(fan, K_total):
    """Intersect the (non-monotone) fan curve with the system curve.
    The residual is sampled finely, every sign change is polished with brentq,
    and the HIGHEST-flow root is returned - the aerodynamically stable branch
    to the right of the stall region (a fan throttled into the stall band
    hunts between branches and must be flagged, not reported silently).
    Returns (q_op [m^3/s], dp_op [Pa], n_roots, in_stall_region)."""
    qmax, _ = fan_qmax_pmax(fan)
    residual = lambda q: fan_dp(q, fan) - system_dp(q, K_total)
    qs = np.linspace(qmax * 1e-6, qmax, ROOT_SCAN_POINTS)
    res = residual(qs)
    roots = [brentq(residual, qs[i], qs[i + 1], xtol=1e-12)
             for i in np.flatnonzero(np.sign(res[:-1]) * np.sign(res[1:]) < 0)]
    if not roots:   # impossible for valid inputs: residual(0+)>0, residual(qmax)<0
        raise RuntimeError(f"no operating point found for fan '{fan['name']}'")
    q_op = max(roots)
    in_stall = (q_op / qmax) < STALL_RECOVERY_Q
    return q_op, float(system_dp(q_op, K_total)), len(roots), in_stall


# ==============================================================================
#  Per-fan analysis
# ==============================================================================

def analyze(fan, K_total, K_down):
    """Operating point + the dashboard quantities for one fan."""
    q_op, dp_op, n_roots, in_stall = find_operating_point(fan, K_total)
    m_dot = RHO_AIR * q_op
    return {
        "fan": fan, "name": fan["name"],
        "q_op": q_op, "dp_op": dp_op,
        "dp_exh": float(K_down * q_op**2),            # fan wall -> exhaust drop
        "t_exh": AMBIENT_TEMP_C + SYSTEM_HEAT_LOAD_W / (m_dot * CP_AIR),
        "pct_free": 100.0 * q_op / fan_qmax_pmax(fan)[0],
        "n_roots": n_roots, "in_stall": in_stall,
    }


# ==============================================================================
#  Interactive CLI input
# ==============================================================================

def ask_float(prompt):
    """Prompt until a positive number is entered; None on end-of-input."""
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            return None
        try:
            val = float(raw)
        except ValueError:
            print("      not a number - try again (e.g. 76.5)")
            continue
        if val <= 0.0:
            print("      must be > 0 - try again")
            continue
        return val


def prompt_fans():
    """Collect fan specs interactively until 'done'. Returns a list of dicts."""
    print(" Enter each fan's spec, then type 'done' to run the simulation.")
    fans = []
    while True:
        try:
            name = input("\n  Fan name (or 'done'): ").strip()
        except EOFError:
            break
        if not name:
            continue
        cmd = name.lower()
        if cmd == "done":
            break
        if cmd == "demo":   # hidden: preload the reference fan set
            fans.extend(dict(f) for f in DEMO_FANS)
            print(f"  [demo] loaded {len(DEMO_FANS)} reference fans "
                  f"({len(fans)} queued). Type 'done' to run, or add more.")
            continue
        cfm = ask_float("    Max airflow  [CFM]  : ")
        if cfm is None:
            break
        mmh2o = ask_float("    Max pressure [mmH2O]: ")
        if mmh2o is None:
            break
        fans.append({"name": name, "max_cfm": cfm, "max_mmh2o": mmh2o})
        print(f"    -> queued '{name}'  ({cfm:g} CFM free delivery, "
              f"{mmh2o:g} mmH2O shut-off)")
    return fans


# ==============================================================================
#  ASCII dashboard
# ==============================================================================

def print_dashboard(results):
    zeta_up, zeta_down = split_zeta()
    K_total = impedance_K(zeta_up + zeta_down)
    K_cfm = K_total * CFM_TO_M3S**2 / MMH2O_TO_PA
    print()
    print(" " + "=" * 78)
    print(" OPERATING-POINT DASHBOARD")
    print(" " + "=" * 78)
    print(f" Chassis   : {CHASSIS_MODEL}")
    print(f" Impedance : dP = K*Q^2, K = {K_total:.3e} Pa/(m^3/s)^2 "
          f"= {K_cfm:.3e} mmH2O/CFM^2")
    print(f"             (zeta: {zeta_up:.1f} upstream of fan wall + "
          f"{zeta_down:.1f} downstream = {zeta_up + zeta_down:.1f} total)")
    print(f" Ambient   : {AMBIENT_TEMP_C:.1f} degC, RH {RELATIVE_HUM_PCT:.0f} %, "
          f"rho = {RHO_AIR:.3f} kg/m^3, cp = {CP_AIR:.0f} J/(kg K)")
    print(f" Heat load : {SYSTEM_HEAT_LOAD_W:.1f} W "
          f"(exhaust temp from Q = m_dot * cp * dT)")
    print(f" Fan model : cubic with stall dip at {STALL_DIP_Q:.0%} Qmax, "
          f"recovery at {STALL_RECOVERY_Q:.0%} Qmax; fans in parallel: "
          f"{FANS_IN_PARALLEL}")

    name_w = max(22, max(len(r["name"]) for r in results) + 2)
    cols = [("Fan", name_w, "l"), ("Op CFM", 8, "r"), ("Op mmH2O", 9, "r"),
            ("Fan->Exh dP [mmH2O]", 20, "r"), ("Exhaust T [degC]", 17, "r")]
    sep = "+" + "+".join("-" * (w + 2) for _t, w, _a in cols) + "+"

    def row(cells):
        out = "|"
        for (title, w, align), cell in zip(cols, cells):
            out += " " + (f"{cell:<{w}}" if align == "l" else f"{cell:>{w}}") + " |"
        return out

    print("\n " + sep)
    print(" " + row([t for t, _w, _a in cols]))
    print(" " + sep)
    for r in results:
        flag = " *" if r["in_stall"] else ""
        print(" " + row([r["name"] + flag,
                         f"{to_cfm(r['q_op']):.2f}",
                         f"{to_mmh2o(r['dp_op']):.2f}",
                         f"{to_mmh2o(r['dp_exh']):.2f}",
                         f"{r['t_exh']:.3f}"]))
    print(" " + sep)
    if any(r["in_stall"] for r in results):
        print("  * operating point sits at/left of the stall recovery knee "
              f"(Q < {STALL_RECOVERY_Q:.0%} of Qmax) - unstable, avoid.")
    if any(r["n_roots"] > 1 for r in results):
        print("  note: a fan curve crossed the impedance curve more than once; "
              "the high-flow (stable) branch is reported.")


# ==============================================================================
#  Matplotlib P-Q map
# ==============================================================================

def series_color(i):
    """Fixed categorical assignment; beyond the validated slots fall back to
    muted gray (identity then carried by the legend, not by a recycled hue)."""
    return SERIES_COLORS[i] if i < len(SERIES_COLORS) else COL_MUTED


def plot_results(results, K_total):
    fig, ax = plt.subplots(figsize=(9.5, 5.8), dpi=120)
    fig.patch.set_facecolor(COL_SURFACE)
    ax.set_facecolor(COL_SURFACE)

    # chassis impedance curve (reference series: neutral, dashed)
    q_end = 1.06 * max(fan_qmax_pmax(r["fan"])[0] for r in results)
    qs = np.linspace(0.0, q_end, CURVE_POINTS)
    ax.plot(to_cfm(qs), to_mmh2o(system_dp(qs, K_total)), color=COL_INK2,
            lw=2.0, ls="--", label="Chassis impedance (6029U)", zorder=2)

    # fan curves in fixed categorical order
    for i, r in enumerate(results):
        qf = np.linspace(0.0, fan_qmax_pmax(r["fan"])[0], CURVE_POINTS)
        ax.plot(to_cfm(qf), to_mmh2o(fan_dp(qf, r["fan"])), color=series_color(i),
                lw=2.0, ls="-" if i < len(SERIES_COLORS) else ":",
                label=r["name"], zorder=3)

    # operating points: marker with surface-colored ring + compact annotation;
    # labels alternate corners by flow rank so neighbouring points never share
    # a side (adjacent operating points sit close along the impedance curve)
    order = np.argsort([r["q_op"] for r in results])
    for rank, idx in enumerate(order):
        r = results[idx]
        x, y = to_cfm(r["q_op"]), to_mmh2o(r["dp_op"])
        ax.plot([x], [y], marker="o", ms=8, mfc=series_color(idx),
                mec=COL_SURFACE, mew=2.0, ls="none", zorder=5)
        if rank % 2:
            offset, ha = (-7, 8), "right"     # upper-left of the marker
        else:
            offset, ha = (7, -14), "left"     # lower-right of the marker
        ax.annotate(f"({x:.1f}, {y:.2f})", (x, y), textcoords="offset points",
                    xytext=offset, ha=ha, fontsize=8, color=COL_INK2, zorder=6)

    ax.set_xlabel("Airflow [CFM]", color=COL_INK2)
    ax.set_ylabel("Static pressure [mmH2O]", color=COL_INK2)
    ax.set_title("Fan P-Q curves vs chassis impedance - " + CHASSIS_MODEL,
                 color=COL_INK, fontsize=11)
    ax.set_xlim(0.0, to_cfm(q_end))
    ax.set_ylim(0.0, 1.08 * max(to_mmh2o(fan_qmax_pmax(r["fan"])[1])
                                for r in results))
    ax.grid(True, color=COL_GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(COL_AXIS)
    ax.tick_params(colors=COL_MUTED, labelsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=COL_INK, loc="upper right")
    fig.tight_layout()

    fig.savefig(PLOT_FILE, dpi=150, facecolor=COL_SURFACE)
    print(f"\n Plot saved to {os.path.abspath(PLOT_FILE)}")
    if matplotlib.get_backend().lower().startswith("agg"):
        print(" (no GUI display detected - interactive window skipped)")
    else:
        plt.show()


# ==============================================================================
#  Configuration validation
# ==============================================================================

def validate_config():
    q1, q2 = STALL_DIP_Q, STALL_RECOVERY_Q
    if not (0.0 < q1 < q2 < 1.0):
        raise ValueError("need 0 < STALL_DIP_Q < STALL_RECOVERY_Q < 1")
    if _stall_g(1.0) <= 0.0:
        raise ValueError("stall extrema too far apart: g(1) <= 0 inverts the "
                         "curve - move STALL_DIP_Q / STALL_RECOVERY_Q closer")
    if _stall_g(q1) >= _stall_g(1.0):
        raise ValueError("stall dip would drive fan pressure below zero - "
                         "move STALL_DIP_Q and STALL_RECOVERY_Q closer together")
    if not (0.0 < FAN_WALL_Z < CHASSIS_L):
        raise ValueError("FAN_WALL_Z must lie strictly inside (0, CHASSIS_L)")
    for name, z0, z1, zeta in IMPEDANCE_ZONES:
        if not (0.0 <= z0 < z1 <= CHASSIS_L) or zeta < 0.0:
            raise ValueError(f"impedance zone '{name}' is invalid")
    for fan in DEMO_FANS:
        if fan["max_cfm"] <= 0.0 or fan["max_mmh2o"] <= 0.0:
            raise ValueError(f"demo fan '{fan['name']}' has non-positive ratings")
    if FANS_IN_PARALLEL < 1:
        raise ValueError("FANS_IN_PARALLEL must be >= 1")
    if SYSTEM_HEAT_LOAD_W < 0.0:
        raise ValueError("SYSTEM_HEAT_LOAD_W must be >= 0")


# ==============================================================================
#  Main
# ==============================================================================

def main():
    validate_config()
    print(" " + "=" * 78)
    print(" 2U CHASSIS FAN ANALYZER - " + CHASSIS_MODEL)
    print(" cubic axial-fan curves with stall dip | operating points | "
          "P-Q map plot")
    print(" " + "=" * 78)

    fans = prompt_fans()
    if not fans:
        print("\n No fans entered - nothing to simulate.")
        return

    zeta_up, zeta_down = split_zeta()
    K_total = impedance_K(zeta_up + zeta_down)
    K_down = impedance_K(zeta_down)

    results = [analyze(fan, K_total, K_down) for fan in fans]
    print_dashboard(results)          # ASCII table first...
    plot_results(results, K_total)    # ...then the matplotlib window


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted.")
