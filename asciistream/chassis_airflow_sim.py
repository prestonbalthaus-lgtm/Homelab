#!/usr/bin/env python3
"""
================================================================================
 2U SERVER CHASSIS - 3D AIRFLOW / PRESSURE-GRADIENT / THERMAL-EXHAUST MODEL
 Target platform: Supermicro SuperServer 6029U-E1CR4T (2U rackmount)
================================================================================

 Coordinate system
 -----------------
   x : chassis width   [0, W]  (m)
   y : chassis height  [0, H]  (m)
   z : flow direction  [0, L]  (m)   air enters at z = 0, exhausts at z = L

 The model couples three solvers:

 (1) 0-D OPERATING POINT
     Fan static-pressure curve:   P_fan(Q) = Pmax * (1 - (Q/Qmax)^n)
     Chassis impedance curve:     dP_sys(Q) = K * Q^2
        with  K = rho * SUM(zeta_i) / (2 * A^2)        [Pa / (m^3/s)^2]
     The intersection P_fan(Q*) = dP_sys(Q*) is found with
     scipy.optimize.brentq. The bracket [0, Qmax] is guaranteed to contain
     exactly one root: the fan curve decreases monotonically while the
     system curve increases monotonically.

 (2) PRESSURE FIELD P(z)  (1-D march on the 3-D grid's z axis)
     Zone loss coefficients are distributed over their z extent as a
     per-length resistance and integrated:
         dP/dz = -k'(z) * (rho/2) * (Q/A)^2
     with a discrete static-pressure RISE at the fan wall (z = FAN_WALL_Z).
     Closure: gauge pressure must return to ~0 at the exhaust plane,
     which the operating-point solution guarantees.

 (3) TEMPERATURE FIELD T(x, y, z)  (3-D)
     Steady advection-diffusion for temperature,
         w(x,y) * dT/dz = eps_t * (d2T/dx2 + d2T/dy2) + S(x,y,z)/(rho*cp),
     marched implicitly (backward Euler) in z using a scipy sparse LU
     factorisation on a cell-centred grid with adiabatic (Neumann) walls.
     w(x,y) is a 1/7-power turbulent duct profile normalised so its area
     integral equals the operating-point flow; eps_t is a turbulent eddy
     diffusivity ~1% * U_mean * D_hydraulic. Heat is deposited at the
     drive bay, the two CPU heatsinks, the DIMM banks and the VRM/PCH
     region. The mixed-mean exhaust temperature is cross-checked against
     the exact 0-D energy balance  Q_load = m_dot * cp * dT.

 Assumptions: steady state; incompressible flow (dP << 1 atm); adiabatic
 chassis walls; fully-turbulent loss scaling (dP ~ Q^2); loss coefficients
 referenced to the mean superficial velocity Q/A; fan work is NOT added to
 the thermal budget (reported separately as an [info] line).

 Units: all internal arithmetic is SI (m, m^3/s, Pa, K). CFM and mmH2O are
 converted at the boundaries only.

 Dependencies: numpy, scipy. Console output only - no plotting, no GUI.
================================================================================
"""

import numpy as np
from scipy.optimize import brentq
from scipy.sparse import diags, identity, kron
from scipy.sparse.linalg import factorized

# ==============================================================================
#  CONFIGURATION  --  edit everything in this block
# ==============================================================================

# --- Environment --------------------------------------------------------------
AMBIENT_TEMP_C   = 22.0    # inlet / room air temperature                [degC]
RELATIVE_HUM_PCT = 50.0    # relative humidity (dew-point & density
                           #   sanity check; solver uses RHO_AIR below)  [%]
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

# Spatial split of the heat load. Each source:
#   (name, fraction, z_start, z_end, x_center, y_center, sigma_x, sigma_y)
# x_center=None -> heat spread uniformly over the cross-section of the z band;
# otherwise a 2-D Gaussian footprint (discretely normalised, so the injected
# power is exact regardless of grid resolution). Fractions must sum to 1.
HEAT_SOURCES = [
    ("Drive bank (8x 3.5in SAS)",  0.25, 0.15, 0.30, None,  None,  None,  None),
    ("CPU1 heatsink",              0.25, 0.37, 0.47, 0.145, 0.040, 0.030, 0.018),
    ("CPU2 heatsink",              0.25, 0.37, 0.47, 0.285, 0.040, 0.030, 0.018),
    ("DIMM banks",                 0.15, 0.35, 0.50, None,  None,  None,  None),
    ("VRM / PCH / NIC / risers",   0.10, 0.50, 0.65, None,  None,  None,  None),
]

# --- Chassis impedance profile (6029U-E1CR4T) ---------------------------------
# Each zone: (name, z_start, z_end, zeta, porosity)
#   zeta     : loss coefficient, dimensionless, referenced to the mean
#              superficial duct velocity  v = Q / A, spread uniformly over
#              [z_start, z_end] when integrating the pressure profile.
#   porosity : free-area fraction, used to report the local interstitial
#              velocity  v_local = v / porosity  (diagnostic only).
# Calibration: SUM(zeta) = 177 puts the whole chassis at ~20 mmH2O @ 100 CFM,
# typical of a dense 2U storage/Ultra platform. The dominant blockage is the
# front drive bay + SAS backplane (z = 0.15..0.30) and the secondary blockage
# is the CPU heatsink + DIMM field (z = 0.35..0.50), per the platform layout.
# The rear-grille zeta includes the exit kinetic-energy loss.
IMPEDANCE_ZONES = [
    #  name                              z0 [m]  z1 [m]  zeta   porosity
    ("Front bezel / intake grille",       0.00,   0.05,  10.0,  0.65),
    ("Intake plenum",                     0.05,   0.10,   1.0,  1.00),
    # ---------------- fan wall at z = FAN_WALL_Z = 0.10 ----------------
    ("Fan-wall discharge plenum",         0.10,   0.15,   2.0,  1.00),
    ("Drive bays + SAS backplane",        0.15,   0.30,  95.0,  0.35),
    ("Mid-chassis plenum",                0.30,   0.35,   2.0,  1.00),
    ("CPU heatsinks + DIMM banks",        0.35,   0.50,  55.0,  0.45),
    ("PCIe risers / VRM field",           0.50,   0.60,   4.0,  0.85),
    ("Rear I/O + PSU exhaust grille",     0.60,   0.70,   8.0,  0.60),
]

# --- Fan catalogue (specs as provided by the manufacturer datasheets) ---------
#   max_cfm  : free-delivery airflow  (at P = 0)
#   max_mmh2o: shut-off static pressure (at Q = 0)
FANS = [
    {"name": "Supermicro FAN-0118L4",    "max_cfm": 100.0,  "max_mmh2o": 44.95},
    {"name": "ARCTIC S8038-7K",          "max_cfm":  70.0,  "max_mmh2o": 25.0},
    {"name": "ARCTIC S8038-10K",         "max_cfm": 102.0,  "max_mmh2o": 51.0},
    {"name": "SilverStone FHS80X 24V",   "max_cfm":  70.3,  "max_mmh2o": 39.5},
    {"name": "SilverStone FHS80X (12V)", "max_cfm":  83.66, "max_mmh2o": 50.77},
]
FAN_CURVE_EXPONENT = 2.0   # P(Q) = Pmax*(1-(Q/Qmax)^n); n = 2 is the standard
                           # two-point parabolic droop for axial fans
FANS_IN_PARALLEL   = 1     # identical fans side by side (the 6029U fan wall
                           # carries 4); parallel fans multiply Qmax at equal P.
                           # Left at 1 -> single-fan curve vs chassis, per spec.

# --- Numerical grid -----------------------------------------------------------
NX, NY, NZ      = 43, 16, 140  # cells across width / height / length
EDDY_DIFF_COEFF = 0.010        # eps_t = coeff * U_mean * D_hyd (turb. mixing)

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

# --- Derived geometry ---------------------------------------------------------
AREA  = CHASSIS_W * CHASSIS_H                            # duct cross-section [m^2]
D_HYD = 4.0 * AREA / (2.0 * (CHASSIS_W + CHASSIS_H))     # hydraulic diameter [m]


# ==============================================================================
#  Psychrometrics (reference checks only - solver uses configured RHO_AIR)
# ==============================================================================

def dew_point_c(t_c, rh_pct):
    """Dew point via the Magnus formula (Sonntag coefficients)."""
    g = np.log(rh_pct / 100.0) + 17.62 * t_c / (243.12 + t_c)
    return 243.12 * g / (17.62 - g)


def moist_air_density(t_c, rh_pct, p_pa=101325.0):
    """Density of humid air as an ideal gas mixture."""
    t_k  = t_c + 273.15
    p_ws = 611.21 * np.exp(17.62 * t_c / (243.12 + t_c))  # saturation vapour P
    p_v  = rh_pct / 100.0 * p_ws
    return (p_pa - p_v) / (287.058 * t_k) + p_v / (461.495 * t_k)


# ==============================================================================
#  Chassis impedance
# ==============================================================================

def split_zeta():
    """Total loss coefficient upstream / downstream of the fan wall.
    A zone straddling the fan wall is split pro-rata by length."""
    up = down = 0.0
    for _name, z0, z1, zeta, _phi in IMPEDANCE_ZONES:
        lu = max(0.0, min(z1, FAN_WALL_Z) - z0)
        ld = max(0.0, z1 - max(z0, FAN_WALL_Z))
        up   += zeta * lu / (z1 - z0)
        down += zeta * ld / (z1 - z0)
    return up, down


def impedance_K(zeta_total):
    """System constant K of dP = K*Q^2, in Pa/(m^3/s)^2."""
    return RHO_AIR * zeta_total / (2.0 * AREA**2)


def system_dp(q, K):
    """Chassis impedance curve dP(Q) [Pa]; q in m^3/s."""
    return K * q * q


# ==============================================================================
#  Fan model and operating point
# ==============================================================================

def fan_qmax_pmax(fan):
    """Effective free-delivery flow [m^3/s] and shut-off pressure [Pa].
    Parallel identical fans multiply flow at equal static pressure."""
    return to_m3s(fan["max_cfm"]) * FANS_IN_PARALLEL, to_pa(fan["max_mmh2o"])


def fan_dp(q, fan):
    """Fan static pressure at flow q [Pa]: parabolic two-point PQ curve."""
    qmax, pmax = fan_qmax_pmax(fan)
    r = np.clip(q / qmax, 0.0, 1.0)
    return pmax * (1.0 - r**FAN_CURVE_EXPONENT)


def solve_operating_point(fan, K_total):
    """Intersect the fan PQ curve with the chassis impedance curve.
    Returns (Q* [m^3/s], dP* [Pa])."""
    qmax, _ = fan_qmax_pmax(fan)
    residual = lambda q: fan_dp(q, fan) - system_dp(q, K_total)
    q_op = brentq(residual, 1e-9, qmax, xtol=1e-14)
    return q_op, system_dp(q_op, K_total)


# ==============================================================================
#  Pressure field along z
# ==============================================================================

def pressure_profile(q_op, dp_op, z_edges, i_fan):
    """Gauge static pressure at every z edge, marching inlet -> exhaust.
    Distributed zone losses are integrated slab by slab; the fan's static
    rise is injected at edge index i_fan (index-based placement so the rise
    and the DELTA-P readout always refer to the same location)."""
    qdyn = 0.5 * RHO_AIR * (q_op / AREA) ** 2       # duct dynamic pressure [Pa]
    p = np.zeros(len(z_edges))
    for k in range(1, len(z_edges)):
        z0e, z1e = z_edges[k - 1], z_edges[k]
        drop = 0.0
        for _name, za, zb, zeta, _phi in IMPEDANCE_ZONES:
            overlap = max(0.0, min(zb, z1e) - max(za, z0e))
            drop += zeta * (overlap / (zb - za)) * qdyn
        p[k] = p[k - 1] - drop
        if k == i_fan:
            p[k] += dp_op
    return p


def zone_breakdown(q_op):
    """Per-zone pressure drop and interstitial velocity at the op. point."""
    qdyn = 0.5 * RHO_AIR * (q_op / AREA) ** 2
    v_super = q_op / AREA
    return [(name, z0, z1, zeta, zeta * qdyn, v_super / phi)
            for name, z0, z1, zeta, phi in IMPEDANCE_ZONES]


# ==============================================================================
#  3-D velocity and temperature fields
# ==============================================================================

def velocity_profile_xy(q_op, xc, yc):
    """Superficial axial velocity w(x,y) [m/s]: 1/7-power turbulent duct
    profile, discretely normalised so that mean(w)*AREA = q_op exactly."""
    n = 7.0
    fx = (1.0 - np.abs(2.0 * xc / CHASSIS_W - 1.0)) ** (1.0 / n)
    fy = (1.0 - np.abs(2.0 * yc / CHASSIS_H - 1.0)) ** (1.0 / n)
    shape = np.outer(fy, fx)                       # (NY, NX)
    return (q_op / AREA) * shape / shape.mean()


def neumann_laplacian(nx, ny, dx, dy):
    """Sparse 2-D Laplacian, cell-centred, zero-flux (adiabatic) walls.
    Column sums are zero -> the implicit march conserves energy exactly."""
    def lap1d(n, h):
        main = np.full(n, -2.0)
        main[[0, -1]] = -1.0
        off = np.ones(n - 1)
        return diags([off, main, off], [-1, 0, 1]) / h**2
    return kron(identity(ny), lap1d(nx, dx)) + kron(lap1d(ny, dy), identity(nx))


def heat_deposition(xc, yc, z_edges):
    """watts[k, j, i]: heat [W] deposited in slab k, cell (j=y, i=x).
    Slab weights use exact band overlap, Gaussian footprints are discretely
    normalised -> total deposited power == SYSTEM_HEAT_LOAD_W exactly."""
    nz = len(z_edges) - 1
    watts = np.zeros((nz, len(yc), len(xc)))
    X, Y = np.meshgrid(xc, yc)                     # both (NY, NX)
    for _name, frac, z0, z1, x0, y0, sx, sy in HEAT_SOURCES:
        if x0 is None:
            wxy = np.ones_like(X)
        else:
            wxy = np.exp(-0.5 * (((X - x0) / sx) ** 2 + ((Y - y0) / sy) ** 2))
        wxy = wxy / wxy.sum()
        for k in range(nz):
            overlap = max(0.0, min(z1, z_edges[k + 1]) - max(z0, z_edges[k]))
            if overlap > 0.0:
                watts[k] += frac * SYSTEM_HEAT_LOAD_W * (overlap / (z1 - z0)) * wxy
    return watts


def solve_temperature_3d(q_op, xc, yc, z_edges, watts):
    """March T(x,y) through every z slab (backward Euler, sparse LU).
        w(x,y) dT/dz = eps_t * Lap_xy(T) + S/(rho*cp)
    Returns the exhaust-plane field, mixed-mean T(z), and hot-spot data."""
    nx, ny = len(xc), len(yc)
    dx, dy = xc[1] - xc[0], yc[1] - yc[0]
    dz = z_edges[1] - z_edges[0]

    w = velocity_profile_xy(q_op, xc, yc)                     # (NY, NX)
    eps_t = EDDY_DIFF_COEFF * (q_op / AREA) * D_HYD           # [m^2/s]

    L = neumann_laplacian(nx, ny, dx, dy)
    A = diags(w.ravel()) - dz * eps_t * L                     # implicit operator
    solve = factorized(A.tocsc())                             # LU once, reuse per slab

    wflat = w.ravel()
    T = np.full(ny * nx, AMBIENT_TEMP_C)
    t_mix = np.empty(len(z_edges) - 1)
    t_peak, peak_loc = -np.inf, (0.0, 0.0, 0.0)
    for k in range(len(z_edges) - 1):
        # source term: watts -> K*m/s  (the dz of S*dz/(rho cp) cancels
        # against the slab volume in the per-cell deposition)
        src = watts[k].ravel() / (RHO_AIR * CP_AIR * dx * dy)
        T = solve(wflat * T + src)
        t_mix[k] = np.dot(wflat, T) / wflat.sum()             # flow-weighted mean
        m = int(T.argmax())
        if T[m] > t_peak:
            t_peak = float(T[m])
            peak_loc = (xc[m % nx], yc[m // nx],
                        0.5 * (z_edges[k] + z_edges[k + 1]))
    return {"w": w, "eps_t": eps_t, "t_mix": t_mix,
            "T_exhaust_plane": T.reshape(ny, nx),
            "t_peak": t_peak, "peak_loc": peak_loc}


# ==============================================================================
#  Analysis driver
# ==============================================================================

def analyze_fan(fan, K_total, xc, yc, z_edges, i_fan, watts):
    """Run all three solvers for one fan and collect every reported metric."""
    q_op, dp_op = solve_operating_point(fan, K_total)
    p_edges = pressure_profile(q_op, dp_op, z_edges, i_fan)
    therm = solve_temperature_3d(q_op, xc, yc, z_edges, watts)

    qmax, pmax = fan_qmax_pmax(fan)
    m_dot = RHO_AIR * q_op
    dT_bulk = SYSTEM_HEAT_LOAD_W / (m_dot * CP_AIR)           # Q = m_dot*cp*dT
    t_exh_bulk = AMBIENT_TEMP_C + dT_bulk
    t_exh_3d = therm["t_mix"][-1]
    closure_pct = (abs(t_exh_3d - t_exh_bulk) / dT_bulk * 100.0
                   if dT_bulk > 0 else 0.0)

    # hottest point on the exhaust plane
    Tp = therm["T_exhaust_plane"]
    jy, ix = np.unravel_index(int(Tp.argmax()), Tp.shape)

    # peak interstitial velocity: tightest zone porosity
    tight = min(IMPEDANCE_ZONES, key=lambda z: z[4])
    v_super = q_op / AREA

    return {
        "q_op": q_op, "dp_op": dp_op,
        "pct_free": 100.0 * q_op / qmax, "pct_shutoff": 100.0 * dp_op / pmax,
        "m_dot": m_dot, "v_super": v_super,
        "v_core": float(therm["w"].max()),
        "v_interstitial": v_super / tight[4], "tight_zone": tight,
        "air_power_w": q_op * dp_op,
        "dT_fanwork": dp_op / (RHO_AIR * CP_AIR),
        "p_edges": p_edges,
        "dp_fan_to_exhaust": p_edges[i_fan] - p_edges[-1],
        "p_after_fan": p_edges[i_fan],
        "p_before_fan": p_edges[i_fan] - dp_op,
        "p_exhaust": p_edges[-1],
        "dT_bulk": dT_bulk, "t_exh_bulk": t_exh_bulk, "t_exh_3d": t_exh_3d,
        "closure_pct": closure_pct,
        "t_mix": therm["t_mix"], "eps_t": therm["eps_t"],
        "T_plane": Tp, "hot_xy": (xc[ix], yc[jy]),
        "t_hot_plane": float(Tp.max()),
        "t_peak": therm["t_peak"], "peak_loc": therm["peak_loc"],
        "transit_s": AREA * CHASSIS_L / q_op,
    }


# ==============================================================================
#  Console reporting
# ==============================================================================

W_LINE = 86

def _rule(ch="-"):
    print(ch * W_LINE)


def print_header(K_total, zeta_up, zeta_down):
    K_cfm = K_total * CFM_TO_M3S**2 / MMH2O_TO_PA   # mmH2O per CFM^2
    _rule("=")
    print(" 2U CHASSIS AIRFLOW / PRESSURE / THERMAL SIMULATION  (steady state)")
    _rule("=")
    print(f" Chassis        : {CHASSIS_MODEL}")
    print(f" Duct (x*y*z)   : {CHASSIS_W:.3f} x {CHASSIS_H:.3f} x {CHASSIS_L:.3f} m"
          f"   | A = {AREA:.5f} m^2 | D_hyd = {D_HYD:.4f} m")
    print(f" Fan wall       : z = {FAN_WALL_Z:.3f} m"
          f"   | fans in parallel: {FANS_IN_PARALLEL}")
    print(f" Ambient        : {AMBIENT_TEMP_C:.2f} degC, RH {RELATIVE_HUM_PCT:.0f} %"
          f"  (dew point {dew_point_c(AMBIENT_TEMP_C, RELATIVE_HUM_PCT):.1f} degC)")
    print(f" Air properties : rho = {RHO_AIR:.3f} kg/m^3, cp = {CP_AIR:.0f} J/(kg K)")
    print(f"   [info] moist-air check: rho({AMBIENT_TEMP_C:.0f} degC, "
          f"{RELATIVE_HUM_PCT:.0f} % RH, 1 atm) = "
          f"{moist_air_density(AMBIENT_TEMP_C, RELATIVE_HUM_PCT):.3f} kg/m^3; "
          f"configured value is used.")
    print(f" Heat load      : {SYSTEM_HEAT_LOAD_W:.1f} W total, deposited as:")
    for name, frac, z0, z1, x0, _y0, _sx, _sy in HEAT_SOURCES:
        foot = "uniform over cross-section" if x0 is None else "gaussian footprint"
        print(f"    {frac*100:5.1f} %  {name:<28s} z {z0:.2f}-{z1:.2f} m  ({foot})")
    print(f" Fan curve      : P(Q) = Pmax * (1 - (Q/Qmax)^{FAN_CURVE_EXPONENT:.1f})"
          f"   (two-point parabolic droop)")
    print(f" Impedance      : dP_sys = K * Q^2,  K = {K_total:.4e} Pa/(m^3/s)^2"
          f"  = {K_cfm:.4e} mmH2O/CFM^2")
    print(f"                  zeta upstream of fan = {zeta_up:.1f} | downstream = "
          f"{zeta_down:.1f} | total = {zeta_up + zeta_down:.1f}")
    print(" Impedance zones (zeta referenced to superficial velocity Q/A):")
    for name, z0, z1, zeta, phi in IMPEDANCE_ZONES:
        print(f"    z {z0:.3f}-{z1:.3f} m  {name:<30s} zeta={zeta:6.1f}  "
              f"porosity={phi:.2f}")
    print(f" Grid           : {NX} x {NY} x {NZ} cells "
          f"(dx={1e3*CHASSIS_W/NX:.1f} / dy={1e3*CHASSIS_H/NY:.1f} / "
          f"dz={1e3*CHASSIS_L/NZ:.1f} mm)")
    print(f" Turbulent mix. : eps_t = {EDDY_DIFF_COEFF:.3f} * U_mean * D_hyd")


def print_fan_report(idx, fan, r, z_edges):
    dz = z_edges[1] - z_edges[0]
    nz = len(z_edges) - 1

    def t_at(z):
        k = int(np.clip(round(z / dz - 0.5), 0, nz - 1))
        return r["t_mix"][k]

    print()
    _rule("=")
    print(f" FAN {idx}/{len(FANS)} : {fan['name']}")
    print(f"   Rated: {fan['max_cfm']:.2f} CFM free delivery | "
          f"{fan['max_mmh2o']:.2f} mmH2O shut-off")
    _rule()

    print(" [1] OPERATING POINT  (fan P-Q curve x chassis impedance curve)")
    print(f"       Airflow Q*              : {to_cfm(r['q_op']):8.2f} CFM   "
          f"= {r['q_op']:.6f} m^3/s   ({r['pct_free']:.1f} % of free delivery)")
    print(f"       Static pressure dP*     : {to_mmh2o(r['dp_op']):8.2f} mmH2O "
          f"= {r['dp_op']:8.2f} Pa    ({r['pct_shutoff']:.1f} % of shut-off)")
    print(f"       Mass flow m_dot         : {r['m_dot']:.6f} kg/s")
    print(f"       Duct velocity           : {r['v_super']:8.3f} m/s superficial"
          f"   (turbulent core peak {r['v_core']:.3f} m/s)")
    print(f"       Peak interstitial vel.  : {r['v_interstitial']:8.3f} m/s "
          f"in '{r['tight_zone'][0]}' (porosity {r['tight_zone'][4]:.2f})")
    print(f"       [info] air power Q*xdP* : {r['air_power_w']:8.2f} W dissipated "
          f"in-stream (+{r['dT_fanwork']:.3f} K if counted; excluded below)")

    print(" [2] PRESSURE FIELD ALONG z  (gauge static pressure)")
    print(f"       {'z [m]':>7s}  {'station':<36s} {'P [Pa]':>9s}  "
          f"{'P [mmH2O]':>9s}  {'T_mix [degC]':>12s}")
    stations = sorted({0.0, CHASSIS_L}
                      | {z0 for _n, z0, _z1, _zt, _ph in IMPEDANCE_ZONES}
                      | {z1 for _n, _z0, z1, _zt, _ph in IMPEDANCE_ZONES})
    for z in stations:
        i_edge = int(np.argmin(np.abs(z_edges - z)))
        if abs(z - FAN_WALL_Z) < 1e-12:
            for lbl, p in (("fan wall - suction side", r["p_before_fan"]),
                           ("fan wall - discharge side", r["p_after_fan"])):
                print(f"       {z:7.3f}  {lbl:<36s} {p:9.2f}  "
                      f"{to_mmh2o(p):9.3f}  {t_at(z):12.3f}")
            continue
        if abs(z - CHASSIS_L) < 1e-12:
            lbl = "exhaust -> room"
        else:
            lbl = next(("enter: " + n for n, z0, _z1, _zt, _ph in IMPEDANCE_ZONES
                        if abs(z0 - z) < 1e-9), "")
        p_here = r["p_edges"][i_edge]
        print(f"       {z:7.3f}  {lbl:<36s} {p_here:9.2f}  "
              f"{to_mmh2o(p_here):9.3f}  {t_at(z):12.3f}")
    print(f"       DELTA-P (fan wall z={FAN_WALL_Z:.2f} -> exhaust "
          f"z={CHASSIS_L:.2f}) : {r['dp_fan_to_exhaust']:.2f} Pa = "
          f"{to_mmh2o(r['dp_fan_to_exhaust']):.2f} mmH2O")
    print(f"       Closure check |P_exhaust|              : "
          f"{abs(r['p_exhaust']):.2e} Pa (must be ~0)")
    print("       Zone-by-zone drop at Q*:")
    for name, z0, z1, zeta, dp, v_int in zone_breakdown(r["q_op"]):
        print(f"         z {z0:.2f}-{z1:.2f}  {name:<30s} zeta={zeta:6.1f}  "
              f"dP={dp:7.2f} Pa ({to_mmh2o(dp):6.3f} mmH2O)  v_int={v_int:6.3f} m/s")

    print(f" [3] THERMAL STATE  ({SYSTEM_HEAT_LOAD_W:.1f} W into the airstream, "
          f"Q = m_dot * cp * dT)")
    print(f"       Bulk temperature rise dT               : {r['dT_bulk']:.4f} K")
    print(f"       Exhaust temperature (0-D energy bal.)  : "
          f"{r['t_exh_bulk']:.3f} degC")
    print(f"       Exhaust temperature (3-D mixed mean)   : "
          f"{r['t_exh_3d']:.3f} degC   (closure err {r['closure_pct']:.2e} %)")
    print(f"       Exhaust plane spread (min/max/sigma)   : "
          f"{r['T_plane'].min():.3f} / {r['T_plane'].max():.3f} / "
          f"{r['T_plane'].std():.4f} degC")
    print(f"       Hottest exhaust-plane point            : "
          f"{r['t_hot_plane']:.3f} degC at (x={r['hot_xy'][0]:.3f}, "
          f"y={r['hot_xy'][1]:.3f}) m")
    px, py, pz = r["peak_loc"]
    print(f"       Hottest point in volume (plume core)   : "
          f"{r['t_peak']:.3f} degC at (x={px:.3f}, y={py:.3f}, z={pz:.3f}) m")
    print(f"       Chassis transit time                   : {r['transit_s']:.2f} s")
    print(f"       Turbulent eddy diffusivity eps_t       : {r['eps_t']:.3e} m^2/s")


def print_summary(results):
    print()
    _rule("=")
    print(" SUMMARY - all fans vs chassis impedance (ranked: coolest exhaust first)")
    _rule("=")
    print(f" {'#':>2s}  {'fan':<26s} {'Q* [CFM]':>9s} {'dP* [mmH2O]':>12s} "
          f"{'dP fan->exh [mmH2O]':>20s} {'T_exh [degC]':>13s}")
    _rule()
    ranked = sorted(results, key=lambda fr: fr[1]["t_exh_bulk"])
    for rank, (fan, r) in enumerate(ranked, 1):
        print(f" {rank:2d}  {fan['name']:<26s} {to_cfm(r['q_op']):9.2f} "
              f"{to_mmh2o(r['dp_op']):12.2f} "
              f"{to_mmh2o(r['dp_fan_to_exhaust']):20.2f} {r['t_exh_bulk']:13.3f}")
    _rule()
    best = ranked[0][0]["name"]
    print(f" Best thermal performer at this impedance/load: {best}")


# ==============================================================================
#  Configuration validation
# ==============================================================================

def validate_config():
    frac_sum = sum(s[1] for s in HEAT_SOURCES)
    if abs(frac_sum - 1.0) > 1e-6:
        raise ValueError(f"HEAT_SOURCES fractions sum to {frac_sum}, expected 1.0")
    if not (0.0 < FAN_WALL_Z < CHASSIS_L):
        raise ValueError("FAN_WALL_Z must lie strictly inside (0, CHASSIS_L)")
    for name, z0, z1, zeta, phi in IMPEDANCE_ZONES:
        if not (0.0 <= z0 < z1 <= CHASSIS_L):
            raise ValueError(f"zone '{name}' z-range [{z0}, {z1}] is invalid")
        if zeta < 0.0 or not (0.0 < phi <= 1.0):
            raise ValueError(f"zone '{name}' has invalid zeta/porosity")
    for fan in FANS:
        if fan["max_cfm"] <= 0.0 or fan["max_mmh2o"] <= 0.0:
            raise ValueError(f"fan '{fan['name']}' has non-positive ratings")
    if min(NX, NY) < 3 or NZ < 10:
        raise ValueError("grid too coarse: need NX, NY >= 3 and NZ >= 10")


# ==============================================================================
#  Main
# ==============================================================================

def main():
    validate_config()

    zeta_up, zeta_down = split_zeta()
    K_total = impedance_K(zeta_up + zeta_down)

    # cell-centred grid
    dx, dy = CHASSIS_W / NX, CHASSIS_H / NY
    xc = (np.arange(NX) + 0.5) * dx
    yc = (np.arange(NY) + 0.5) * dy
    z_edges = np.linspace(0.0, CHASSIS_L, NZ + 1)
    i_fan = int(np.argmin(np.abs(z_edges - FAN_WALL_Z)))   # fan-wall edge index

    watts = heat_deposition(xc, yc, z_edges)               # fan-independent

    print_header(K_total, zeta_up, zeta_down)
    results = []
    for idx, fan in enumerate(FANS, 1):
        r = analyze_fan(fan, K_total, xc, yc, z_edges, i_fan, watts)
        print_fan_report(idx, fan, r, z_edges)
        results.append((fan, r))
    print_summary(results)


if __name__ == "__main__":
    main()
