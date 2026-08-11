#!/usr/bin/env python3
"""
================================================================================
 ASCIISTREAM v0.8 - terminal CFD for server chassis (launcher/worker + TUI)
 parametric gmsh meshing from server_configs.json + FEniCSx/dolfinx transient
 incremental pressure-correction (Chorin/IPCS family) + MPI worker pool +
 socket-streamed live ASCII particle dashboard and a CAD-style isometric
 3-D chassis view (rich), with a btop-style braille telemetry strip scoped
 to the solver's own processes
================================================================================

 ARCHITECTURE (Launcher-Worker)
   LAUNCHER (single-threaded, `python3 chassis_cfd.py`, no mpiexec):
     - imports ONLY stdlib + numpy + rich. It never imports mpi4py, dolfinx
       or gmsh, so it carries no MPI state and can safely spawn the workers.
     - reads server_configs.json (auto-written with the built-in example on
       first run), boots with the ASCIISTREAM banner (CFD-colormap gradient)
       and asks: target server profile (or "Custom Server Configuration" -
       type a name; unknown names append a generic-2U template to the JSON
       and are then modeled), the HARDWARE prompts (drive type 2.5in
       NVMe/SAS vs 3.5in HDD -> drive-cage impedance factor; total system
       wattage; ambient intake and desired exhaust temperature; GPU
       presence with count + wattage -> meshed PCIe cards + heat load; NIC
       presence -> one more populated slot), fan model (a config fan OR
       "Custom fan" with user-entered max CFM / max mmH2O), MPI ranks (ANY
       integer, no ceiling - default is this machine's hardware thread
       count, oversubscription supported), simulated time span, the time
       step dt, and the MESH RESOLUTION (four presets with RAM guidance
       per level PLUS a custom element size accepted down to the 0.5 mm
       floor; a MemTotal safeguard asks for confirmation - EXCEPT the
       "ultra" preset, which on a machine under 32 GB raises an unhandled
       MemoryError and crashes, by design). Hardware answers are RUNTIME overrides carried to the
       workers in a temp overlay config - the JSON on disk is not edited
       (only new custom-server templates are appended). Then it opens a
       localhost TCP socket and
       spawns:  mpiexec [openmpi flags] -n {cores} python3 chassis_cfd.py
                  --worker --profile K --fan K --sim-time T --dt DT
                  --mesh L --callback-port P
       [openmpi flags]: when `mpiexec --version` identifies Open MPI (incl.
       PRRTE/OpenRTE), the launcher adds --oversubscribe (lets ranks exceed
       the detected slot count - Open MPI undercounts usable slots on Intel
       hybrid P/E-core parts) and --use-hwthread-cpus (count hardware
       threads as slots), plus --allow-run-as-root inside root containers.
       MPICH needs and gets none of these flags.
     - stays alive as the render process: a reader thread receives sampled
       field frames from the worker while the main thread runs the rich.live
       dashboard (particle animation over the newest received field).
   WORKERS (`--worker`, usually under mpiexec):
     - bypass the UI completely; rank 0 builds the parametric gmsh mesh from
       the config profile, the mesh is distributed, and all ranks solve the
       TRANSIENT Navier-Stokes equations with an incremental pressure-
       correction projection scheme (dt from config, default 0.1 s).
     - every few steps the field is sampled onto the dashboard grids AND
       the bottom/mid/top volumetric slice stack (parallel gather; the mid
       slice doubles as the classic 2-D plane) and rank 0 streams it to
       the launcher as a length-prefixed JSON-header + npz-payload message. If the socket dies
       the solve continues and still writes the VTU output files.
     - runnable standalone for scripting (--mesh takes a preset name or a
       literal element size in mm, e.g. --mesh 0.8, default "coarse";
       --dt defaults to 0.1 s; for a custom fan pass --fan custom
       --fan-cfm 95 --fan-mmh2o 38):
         mpiexec --oversubscribe --use-hwthread-cpus -n 12 python3 \
             chassis_cfd.py --worker --profile 6029U \
             --fan supermicro-fan-0118l4 --sim-time 10 --mesh medium --dt 0.05
       (the Open MPI flags are optional there and MPICH rejects them - the
       launcher adds them for you only when Open MPI is detected)
     - mesh presets (config mesh_settings, per profile): coarse 15 mm /
       medium 8 mm / fine 4 mm / ultra 2.5 mm element size; any literal
       size down to the 0.5 mm floor is also accepted. The chosen lc is
       the fine-band lc, the bulk meshes at min(2.2*lc, 35 mm).

 PARAMETRIC MESH ENGINE
   No hardcoded chassis boxes: geometry is generated from the config numbers.
     - drive cage      : porous slab over drive_zone_z, C2 = drive_zeta/L;
                         OPTIONAL - drive_bay_count 0 / no drive_zone_z
                         skips it (switches, routers)
     - CPU heatsinks   : `cpu_sockets` porous blocks spread across the width
                         (0 allowed); cpu_label renames them (e.g. "ASIC")
     - RAM banks       : solid banks flanking the CPUs, sized from
                         total_dimm_slots (~9 mm per slot, clamped to gaps)
     - PCIe cards      : exactly `populated_pcie_slots` solid blocks drawn
                         across the rear zone (4 slots -> 4 flow chokes)
     - custom_zones    : per-profile list of named boxes [x0,y0,z0,x1,y1,z1]
                         (metres). type "solid" = full blockage (PSU blocks,
                         card cages); type "porous" = impedance zone with
                         zeta (over the zone z-length) + permeability (GPU
                         heatsinks, optics cages, filters, line-card bays).
                         Optional label (canvas text) + telemetry kind
                         ("cpu"/"gpu"/"optics") joining the thermal checks;
                         optional fan_rpm + fan_size_mm mark a zone that
                         carries its own fan (PSUs) - drawn as the gold fan
                         marker in the views, not solved as a momentum
                         source. Zones may sit on either side of the fan
                         wall but must not straddle it or overlap another
                         porous/solid box (validated; touching faces fine).
     - optics_zone_z   : moves the optics telemetry slab off the default
                         rear-I/O position (switches: the front cage)
     - fan wall        : velocity plane at fan_wall_z, imposed on TWO
                         coincident boundary copies (the mesh deliberately
                         splits there: an interior Dirichlet plane on
                         continuous elements leaks flux - verified earlier)
   Mesh volumes are classified through the OCC fragment parent->child map
   (exact), not centre-of-mass containment - a centred single sink (switch
   ASIC) used to capture the surrounding open-air volume's centroid.
   The fan model + fan_count vs an impedance ESTIMATE from the zeta values
   sets the fan-plane velocity: quadratic curve P = Pmax(1-(Q/Qmax)^2) in
   parallel x fan_count against K = rho*zeta_est/(2A^2), custom porous
   zones contributing zeta x their cross-section fraction. The meshed CFD
   impedance is the truth; the estimate only chooses the inlet BC level.
   PROFILES (server_configs.json): Supermicro 6029U, Dell R640 (10-bay
   front, zero rear) / R740xd, HPE DL360/DL380, and the Stage 2 set - Dell
   C4130 (4 passive rear GPU zones + centre PSU bank), Arista 7050CX3-32S
   and Mellanox SN2700 (QSFP cage + ASIC + rear PSUs), Cisco ASR 1006-X
   (6RU: filter, front PSU bank, line-card/RP-ESP bays) and a generic ATX
   mid-tower mapped side-on (chassis_width = tower height). Outer dims per
   vendor specs; internal layouts, zetas and fan counts are documented
   engineering estimates. Generic 40/60/120 mm class fan curves accompany
   the 80 mm server fans.
   PCIe RISER CAGES (config key pcie_risers): STATIC solid cage blocks at
   pcie_zone_z that model the riser mechanics rather than the plug-in
   cards - they keep standing in the flow when populated_pcie_slots is 0
   (schema reference: the build_geometry docstring).

 DUAL ENGINE (worker args key "engine": "3d" default | "2d")
   3d: the full chassis volume - tetrahedra, P2 velocity in R^3 (the
   original path, unchanged). 2d: a TRUE planar formulation on the chassis
   MID-HEIGHT x-z plane (y = H/2) - triangles, P2 velocity in R^2, the
   SAME IPCS scheme / three linear solves / semi-implicit convection, the
   same split-mesh fan plane trick (in 2-D the fan plane is a LINE, still
   imposed on two coincident boundary copies - an interior Dirichlet line
   on continuous elements leaks flux exactly as the 3-D plane does), and
   the same implicit Darcy-Forchheimer sinks in 2-D vector form. Every
   component box that straddles y = H/2 is projected to its x-z footprint
   rectangle; a box that does not reach mid-height is absent from the
   slice. Line fluxes [m^2/s] are scaled by H into volumetric equivalents,
   and the streamed frames/summary keep the exact 3-D header keys and
   array shapes (the mid slice replicated over the volumetric stack), so
   the dashboard cannot tell which engine ran.
   FAN DUTY (worker args key "fan_duty": fraction of rated RPM, 1.0 =
   rated): the fan curve is scaled by the Fan Affinity Laws BEFORE the
   operating-point intersection - Qmax ~ N, dPmax ~ N^2 - and the summary
   carries affinity-scaled telemetry (power ~ N^3, dBA + 50*log10(N/N0)).
   ACOUSTIC TARGET (worker args key "dba_target", CLI --dba-target,
   default off = byte-identical): a COMBINED free-field dBA ceiling for
   the whole fan wall. solve_duty_for_dba inverts the same affinity +
   10*log10(fan_count) laws into a maximum permitted duty; the QUIETER
   of that ceiling and the explicit --fan-duty wins, and the summary
   ADDS dba_* keys (target, duty cap, binding/achievable flags, the
   resulting combined dBA, and an overheat flag judged against
   requirements.outlet_temp_max_c - solved exhaust with --thermal on,
   bulk balance estimate otherwise).
   VIZ EXPORT (worker args key "viz_every": every N steps, 0 = off,
   default off): atomic mid-run VTU snapshots in per-step directories
   plus viz_manifest.json (replaced atomically LAST) for a host-side
   viewer polling the shared working directory.

 PHYSICS - transient incompressible Navier-Stokes, incremental pressure-
 correction (Chorin/IPCS family), backward-Euler in time with SEMI-IMPLICIT
 convection (linearised about u_n) so the configured dt stays stable even
 when the convective CFL exceeds 1 (at dt = 0.1 s and ~10 mm cells the CFL
 is O(10) - accuracy-limited, unconditionally stable; classic fully explicit
 Chorin would diverge here). Three linear solves per step:

   1. tentative velocity u*:
      (rho/dt)(u*,v) + rho(u_n.grad u*, v) + rho*nu_eff(grad u*, grad v)
        + [mu/K + rho*C2/2*|u_n|](u*,v)|porous  =  (rho/dt)(u_n,v)
        - (grad p_n, v)                        [GMRES + block-Jacobi ILU]
      The porous Darcy-Forchheimer sink is IMPLICIT with the |u| factor
      lagged: its time scale 2/(C2*|u|) ~ 3 ms << dt, explicit treatment
      would blow up. Fan/wall Dirichlet BCs on u*; fan velocity ramps
      linearly over the first RAMP_STEPS steps to avoid a startup spike.
   2. pressure increment phi = p_{n+1} - p_n:
      (grad phi, grad q) = -(rho/dt)(div u*, q)          [CG + BoomerAMG]
      phi = 0 on the OPEN boundaries (front face, outlet - the pressure
      anchors of the two mesh components); homogeneous Neumann elsewhere
      including the fan plane (velocity-Dirichlet boundary). Do not move
      these BCs: each mesh component needs exactly its open-boundary anchor.
   3. velocity correction (mass solve, porous drag NOT applied here):
      rho(u_{n+1},v) = rho(u*,v) - dt(grad phi, v)          [CG + Jacobi]

   Turbulence: constant effective eddy viscosity NU_EFFECTIVE (~0.01*U*D_h,
   block-level electronics-cooling practice) - bulk paths and pressure drops
   at engineering accuracy, not resolved turbulence. Outlet temperature is
   the bulk balance T_in + heat_load/(rho*Q*cp) over the computed flow -
   unless the ENERGY EQUATION is on (worker args key "thermal", CLI
   --thermal on; default off = byte-identical legacy behaviour): then a
   4th linear solve per step advects-diffuses temperature T [degC], same
   backward-Euler/semi-implicit treatment (advecting velocity lagged to
   u_n; eddy diffusivity NU_EFFECTIVE/PRANDTL_TURB; P1 space, GMRES +
   block-Jacobi ILU - advection makes it nonsymmetric). heat_load [W]
   becomes volumetric sources via thermal_heat_plan: porous zones with an
   explicit config heat_w first, wizard GPUs (SOLID PCIe cards - cut out
   of the fluid, no interior cells) into the 1 cm washing shell of fluid
   around each card, the remainder over CPU sinks + cpu/gpu-telemetry
   zones (whole fluid domain if none exist). Inlet air enters at
   requirements.inlet_temp_c, walls are adiabatic, and the split fan
   plane carries bulk enthalpy across as a MIXING PLANE (downstream copy
   held at the upstream mean each step). The summary then ADDS
   t_exhaust_c (solved outlet mean weighted by the OUTGOING mass flow)
   next to t_exhaust_bulk_c (the bulk estimate) for comparison, frames
   ADD t_out_c, and the VTU/viz exports gain the "temperature" field.

 DASHBOARD (launcher side, rich.layout + rich.live)
   Live ASCII particle animation over the streamed field: glyphs by local
   speed ( . stagnant | ~ slow | - moderate | * fast ), coloured GREEN =
   fast/optimal -> YELLOW = moderate -> RED = stagnation/0 m/s. Explicit
   labels typed over the geometry: [ CPU 1 ], [ CPU 2 ], [ RAM ], [ PCIe ],
   [ DRIVES ], [ FAN WALL ]. Side panes: FRONT (inlet, with bay ticks) and
   REAR (exhaust) cross-sections. A full-width bottom CFD WORKERS strip
   (btop-style braille graphs + meters, psutil) tracks ONLY the spawned
   solver tree: USS memory summed over those PIDs and CPU load normalised
   to their affinity pool - never global system telemetry. Particles advect
   in the mid-height (u_x,u_z) plane - 2-D streaklines of that slice, not
   3-D pathlines. The status line shows simulated time, step, live outlet
   flow and cell count.
   Pressing [v] (or 2/3) toggles the main pane to the ASCII ISOMETRIC
   CHASSIS VIEW (CAD-style extruded component boxes, top faces coloured by
   local air speed, gold fan wall + PSU markers) - the same renderer that
   prints post-run and embeds in the text report, since raster images
   cannot live in a .txt file. Pressing [p] pops out the OPTIONAL
   interactive PyVista/Qt 3-D window: run.sh launches viewer_sidecar.py on
   the HOST (a GUI cannot cross the container boundary), the sidecar drops
   VIEWER_READY_FILE in the shared work dir - which switches the solver's
   periodic viz export on - and [p] writes VIEWER_TRIGGER_FILE, which the
   sidecar answers by opening the window and live-refreshing it from the
   atomically-published viz_manifest.json exports. Without the sidecar
   (no host venv, SSH-only session, ASCIISTREAM_VIEWER=0) [p] prints a
   one-line reason and the ASCII isometric view remains the 3-D renderer.
   100+ column terminal recommended; Ctrl+C stops the dashboard (worker too).

 IT TELEMETRY & REPORTING (post-processing; config-driven)
   After the run: component airflow checks against the per-server thresholds
   cpu_min_airflow_ms / gpu_min_airflow_ms / optics_min_airflow_ms (proxies:
   CPU = mean |u| inside each porous sink; GPU = mean |u| in a 1 cm shell
   around each solid PCIe card; Optics = a defined rear I/O slab). Failures
   flash "[THERMAL WARNING: <Component> Airflow Critical]" in bold red
   (blink where the terminal supports it). A fan acoustics/power table uses
   the config's max_dBA / max_wattage at rated RPM (100 % duty assumption;
   N fans combine as +10*log10(N), free-field estimate). Finally the user
   is offered a timestamped plain-text run report (profile, fan, telemetry,
   warnings and a non-ANSI copy of the dashboard cross-section), written to
   the working directory.

 RUNNING (launcher and worker must share one container for localhost):
   podman run -it --rm -v "$PWD":/work:z -w /work -e COLORTERM=truecolor \
     docker.io/dolfinx/dolfinx:stable python3 chassis_cfd.py
   (docker: docker run -it --rm -v "$PWD":/work:z -w /work \
      dolfinx/dolfinx:stable python3 chassis_cfd.py)
   The launcher installs 'rich' + 'psutil' into the container on first start
   (no shell wrapper needed - nested quoting broke for some terminal
   frontends); if psutil cannot be installed the dashboard runs without the
   CFD WORKERS telemetry strip.
   Utility flags: --write-config (dump the example config and exit),
   --config PATH, --worker ... as above (see ARCHITECTURE).

 Written and tested against dolfinx 0.11.0 (dolfinx/dolfinx:stable image);
 the steady SNES solver of the previous version is fully replaced by the
 transient scheme.
================================================================================
"""

import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

import numpy as np

try:
    from rich import box
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
    from rich.table import Table
    from rich.text import Text
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False

# ==============================================================================
#  CONFIGURATION (numerics + rendering; chassis geometry lives in the JSON)
# ==============================================================================

CONFIG_FILE_DEFAULT = "server_configs.json"

# --- Fluid --------------------------------------------------------------------
NU_AIR  = 1.5e-5     # molecular kinematic viscosity of air          [m^2/s]
RHO_AIR = 1.196      # air density                                   [kg/m^3]
CP_AIR  = 1006.0     # specific heat of air (const. pressure)        [J/(kg K)]
NU_EFFECTIVE = 3.4e-3          # constant eddy viscosity for the stress term
PRANDTL_TURB = 0.9   # turbulent Prandtl number: the energy equation uses the
                     # eddy heat diffusivity NU_EFFECTIVE / PRANDTL_TURB
                     # (Reynolds analogy - same block-level practice as the
                     # constant eddy viscosity; molecular diffusion is two
                     # orders down and folded into it)
GPU_SHELL_M = 0.01   # SOLID PCIe cards are cut OUT of the fluid domain, so
                     # their wattage cannot live inside them: it is released
                     # into this thick "washing shell" of fluid around each
                     # card - the same 1 cm shell the GPU airflow telemetry
                     # proxy samples (worker args key "thermal")

# --- PSU internal fan momentum source (zone keys fan_rpm + fan_momentum) ------
# A POROUS custom zone flagged "fan_momentum": true is driven by its own
# fan (R640-style PSUs): the worker adds a uniform +z body force
# f = dp_fan / L_z [N/m^3] over the zone's cells. dp_fan comes from the
# fan scaling laws: dp = PSI * rho * u_tip^2 with u_tip = pi*D*(rpm/60).
# PSI = 0.10 is a documented engineering estimate for small high-static
# axial fans (a 40 mm / 15 krpm unit lands at ~118 Pa - the range of
# published 40x40x28 server PSU fan curves). Zones WITHOUT the flag are
# untouched (fan_rpm alone stays a drawn annotation), so every legacy
# profile solves bit-identically.
PSU_FAN_PRESSURE_COEFF = 0.10

# --- PCIe card band -----------------------------------------------------------
PCIE_CARD_MIN_W = 0.005  # narrowest meshable PCIe card [m]: a pcie_x_band
                         # squeezing the requested cards below 5 mm each is
                         # a config error, not a sliver mesh
PCIE_MAX_SLOTS_DEFAULT = 8   # historical clamp on the populated card count
                             # (profile key pcie_max_slots overrides)

# --- Transient scheme ---------------------------------------------------------
SIM_DT      = 0.1    # default time step [s]; tunable in the TUI / --dt
SIM_DT_MIN  = 0.001  # TUI clamp: below this the run length explodes
SIM_DT_MAX  = 2.0    # TUI clamp: above this even the semi-implicit scheme
                     # is pure smearing
RAMP_STEPS  = 5      # fan velocity ramps 0 -> Vz over this many steps
SEND_EVERY  = 2      # stream a sampled frame every N steps
KSP_RTOL    = 1.0e-8

# --- MPI launch ---------------------------------------------------------------
# --- Mesh sizing --------------------------------------------------------------
# Element size comes from the selected mesh preset (server_configs.json ->
# mesh_settings) OR from a literal millimetre value - the wizard's custom
# option and a numeric --mesh both accept any size down to MESH_MM_FLOOR.
# The chosen lc is the FINE band around the components; the drive/fan band
# meshes at 1.3*lc and the bulk at min(2.2*lc, 35 mm).
MESH_LEVEL_ORDER = ["coarse", "medium", "fine", "ultra"]
MESH_LEVEL_LABEL = {"coarse": "Coarse", "medium": "Medium",
                    "fine": "Fine", "ultra": "Ultra"}
DEFAULT_MESH_SETTINGS = {"coarse": {"element_size_mm": 15.0},
                         "medium": {"element_size_mm": 8.0},
                         "fine": {"element_size_mm": 4.0},
                         "ultra": {"element_size_mm": 2.5}}
# RAM guidance printed verbatim in the wizard menu next to each preset
MESH_RAM_NOTES = {"coarse": "~1-2 GB (Ultra-fast)",
                  "medium": "~4-6 GB (Balanced)",
                  "fine": "~10-14 GB (High-Detail, resolves heatsink fins)",
                  "ultra": "~18-24 GB (Extreme fidelity, requires 32GB system)"}
MESH_RAM_HIGH_GB = {"coarse": 2, "medium": 6, "fine": 14, "ultra": 24}
MESH_MM_FLOOR    = 0.5   # hard floor [mm] for ANY source of element size -
                         # below this, gmsh runtimes and cell counts stop
                         # being meaningful on any machine (NaN also fails)
MESH_EST_KB_CELL = 5.0   # rough solver RAM per tet [KB], used for the soft
                         # RAM warning on custom sizes (calibration: ultra
                         # ~6.2M est. cells <-> ~24 GB)

# --- Output -------------------------------------------------------------------
OUT_VELOCITY = "velocity.vtu"
OUT_PRESSURE = "pressure.vtu"
OUT_ZONES    = "zones.vtu"
OUT_TEMPERATURE = "temperature.vtu"   # written only when the energy
                                      # equation ran (args key "thermal")

# --- Periodic mid-run viz export (worker args key "viz_every", 0 = off) -------
# Every export lands in its OWN viz_step_NNNNNN/ directory and the manifest
# is atomically REPLACED LAST (os.replace of a temp file), so a host-side
# reader polling the shared working directory never observes a torn file:
# it only learns of a directory after every dataset inside it is complete.
OUT_VIZ_MANIFEST = "viz_manifest.json"
VIZ_DIR_PREFIX   = "viz_step_"
VIZ_KEEP_DIRS    = 2     # current + previous (a slow reader may still hold
                         # the previous directory open); older ones removed

# The host-side PyVista sidecar drops this marker in the shared work dir
# while it is alive. The launcher only turns mid-run viz export ON when it
# is present, so a run with no viewer attached pays zero export I/O and
# stays byte-identical to the pre-dual-engine behaviour.
VIEWER_READY_FILE = ".asciistream_viewer_ready"
VIZ_EVERY_DEFAULT = 5    # steps between exports when a viewer IS attached

# --- Fan affinity laws (worker args key "fan_duty" = N/N_rated) ---------------
# Q ~ N, dP ~ N^2, shaft power ~ N^3, dBA ~ dBA_rated + 50*log10(N/N_rated).
# Duty is clamped to a sane PWM-style band: below 5 % a server fan stalls,
# far above rated RPM the affinity extrapolation stops being honest.
# solve_duty_for_dba inverts the noise law (with the +10*log10(fan_count)
# free-field combination) into a duty ceiling for the --dba-target mode,
# clamped into the SAME band.
FAN_DUTY_MIN = 0.05
FAN_DUTY_MAX = 1.5

# --- Dashboard ----------------------------------------------------------------
ANIM_FPS         = 12
N_PARTICLES      = 380
PARTICLE_MAX_AGE = 300
INLET_SPAWN_FRAC = 0.70
MAIN_ROWS        = 24
MAIN_COLS_MAX    = 66    # scripted-worker default when --cols is absent;
                         # the live launcher fills the real terminal width
MINI_COLS        = 28
FIRST_FRAME_TIMEOUT = 420    # mesh + JIT before the first frame [s]

FLOW_CHARS = [(0.12, "."), (0.40, "~"), (0.80, "-"), (9e9, "*")]
# status colours: GREEN = fast/optimal, YELLOW = moderate, RED = stagnant
STATUS_RED, STATUS_YEL, STATUS_GRN = (215, 66, 56), (232, 178, 45), (76, 208, 102)
COL_BORDER = (150, 150, 156)
COL_FILL   = (105, 105, 112)
COL_FANLN  = (255, 214, 10)
COL_LABEL  = "bold bright_white"

# 24-bit CFD "velocity heatmap" gradient (banner + 3-D chassis view)
CFD_CMAP_STOPS = ((0.00, (30, 62, 255)),      # blue
                  (0.25, (0, 199, 230)),      # cyan
                  (0.50, (57, 214, 92)),      # green
                  (0.75, (240, 211, 56)),     # yellow
                  (1.00, (235, 58, 42)))      # red

# --- 3-D isometric chassis view -----------------------------------------------
# Oblique/isometric affine projection of the PHYSICAL component boxes (CAD
# style): chassis length z runs along the columns, width x recedes at 45
# degrees on screen (+2 cols +1 row per step - character cells are ~2:1, so
# slope 1/2 LOOKS like 45), height y is vertical. Height rows are clamped so
# a 6U router cannot stretch the grid apart (ISO_HGT_*).
ISO_HGT_MIN = 3      # min rows of extruded chassis height
ISO_HGT_MAX = 7      # max rows of extruded chassis height (clamp)
ISO_HGT_PER_M = 26   # rows per metre of chassis height before the clamp

# --- CFD WORKERS telemetry strip (btop-style, privacy-scoped) -----------------
SYS_STRIP_ROWS = 6   # full-width bottom strip height, borders included:
                     # 1 header + SYS_GRAPH_ROWS history + 1 meter line
SYS_GRAPH_ROWS = 2   # braille history rows per metric (4 dot-levels each)
TELEM_TREE_SEC = 2.0 # how often the mpiexec process tree is re-walked
TELEM_USS_SEC  = 1.0 # USS cadence - smaps_rollup reads for ~40 ranks are
                     # deliberately slower than the 0.5 s CPU sampling so
                     # they can never stall the 12 fps render loop
# Braille dot bitmasks: cumulative bottom-up fills (0..4 dots) per column.
# chr(0x2800 + BRAILLE_L[i] + BRAILLE_R[j]) is a cell with the left column
# filled i dots high and the right column j dots high ([4]+[4] = U+28FF).
BRAILLE_L = (0x00, 0x40, 0x44, 0x46, 0x47)
BRAILLE_R = (0x00, 0x80, 0xA0, 0xB0, 0xB8)

# --- Volumetric slice stack (worker -> dashboard stream) ----------------------
VOL_SLICE_FRACS  = (0.15, 0.50, 0.85)   # y/H of the horizontal slices the
                                        # worker exports (bottom/mid/top).
                                        # MUST contain 0.50 - the mid slice
                                        # is the classic 2-D plane the
                                        # dashboard + report use.
VOL_MID_IDX      = VOL_SLICE_FRACS.index(0.50)   # loud failure if edited out

M3S_TO_CFM = 60.0 / 0.3048**3

# ==============================================================================
#  BUILT-IN EXAMPLE CONFIG (written to server_configs.json when missing)
#  Dimensions/layouts: standard industry assumptions; zetas: engineering
#  estimates from typical loss data (C2 = zeta / zone length).
# ==============================================================================

DEFAULT_CONFIG = {
    "fans": {
        # max_dBA / max_wattage: per-fan estimates at rated RPM (100 % duty)
        "supermicro-fan-0118l4": {"display": "Supermicro FAN-0118L4",
                                  "max_cfm": 100.0, "max_mmh2o": 44.95,
                                  "rpm": 9500, "max_dBA": 60.5,
                                  "max_wattage": 10.8},
        "arctic-s8038-10k": {"display": "ARCTIC S8038-10K",
                             "max_cfm": 102.0, "max_mmh2o": 51.0,
                             "rpm": 10000, "max_dBA": 48.0,
                             "max_wattage": 7.2},
        "silverstone-fhs80x": {"display": "SilverStone FHS80X (12V)",
                               "max_cfm": 83.66, "max_mmh2o": 50.77,
                               "rpm": 9500, "max_dBA": 62.5,
                               "max_wattage": 12.0},
        # class-typical GENERIC curves (deliberately not tied to a real
        # part number) so the non-80mm chassis profiles have sane fans
        "generic-40mm-dual": {"display": "Generic 40x56mm dual-rotor "
                                         "(1U/switch class)",
                              "max_cfm": 22.0, "max_mmh2o": 90.0,
                              "rpm": 21000, "max_dBA": 57.0,
                              "max_wattage": 9.6},
        "generic-60mm": {"display": "Generic 60x38mm high-static "
                                    "(router class)",
                         "max_cfm": 38.0, "max_mmh2o": 30.0,
                         "rpm": 13000, "max_dBA": 54.0,
                         "max_wattage": 7.2},
        "generic-120mm": {"display": "Generic 120x25mm case fan "
                                     "(ATX class)",
                          "max_cfm": 72.0, "max_mmh2o": 3.0,
                          "rpm": 1800, "max_dBA": 27.0,
                          "max_wattage": 3.0},
        # Dell PowerEdge / HPE ProLiant OEM fan CLASSES. Every number
        # below is a CLASS-REPRESENTATIVE engineering estimate kept
        # consistent with the generic 40/60 mm curves above - NOT a
        # measured vendor curve (Dell/HPE do not publish fan datasheets;
        # see the README accuracy disclaimer). A part reference in a
        # display string identifies which fan CLASS the profile ships
        # with, never a verified spec for that part number. Physics of
        # the classes: 40 mm dual-rotor 1U fans trade flow for very high
        # static pressure and RPM; 60 mm 2U fans move more air at lower
        # static pressure and RPM; High-Perf grades beat Std on every
        # column within a family.
        "dell-1u-std-40mm": {"display": "Dell PowerEdge 1U Std 40mm "
                                        "(class est.)",
                             "max_cfm": 19.0, "max_mmh2o": 65.0,
                             "rpm": 16000, "max_dBA": 52.0,
                             "max_wattage": 6.0},
        "dell-1u-hp-40mm": {"display": "Dell PowerEdge 1U High-Perf "
                                       "Gold 40mm dual-rotor "
                                       "(class est.)",
                            "max_cfm": 26.0, "max_mmh2o": 120.0,
                            "rpm": 25000, "max_dBA": 63.0,
                            "max_wattage": 18.0},
        "dell-2u-std-60mm": {"display": "Dell PowerEdge 2U Std 60mm "
                                        "(class est.)",
                             "max_cfm": 40.0, "max_mmh2o": 28.0,
                             "rpm": 12000, "max_dBA": 52.0,
                             "max_wattage": 7.0},
        "dell-2u-hp-60mm": {"display": "Dell PowerEdge 2U High-Perf "
                                       "60mm (class est.)",
                            "max_cfm": 55.0, "max_mmh2o": 45.0,
                            "rpm": 16000, "max_dBA": 60.0,
                            "max_wattage": 14.4},
        "hpe-dl360g10-hp-40mm": {"display": "HPE DL360 Gen10 High-Perf "
                                            "40mm dual-rotor, "
                                            "875284-001 class (est.)",
                                 "max_cfm": 24.0, "max_mmh2o": 110.0,
                                 "rpm": 23000, "max_dBA": 61.0,
                                 "max_wattage": 16.0},
        "hpe-dl380g10-std-60mm": {"display": "HPE DL380 Gen10 Std 60mm "
                                             "(class est.)",
                                  "max_cfm": 42.0, "max_mmh2o": 30.0,
                                  "rpm": 12500, "max_dBA": 53.0,
                                  "max_wattage": 7.5},
        "hpe-dl380g10-hp-60mm": {"display": "HPE DL380 Gen10 High-Perf "
                                            "60mm (class est.)",
                                 "max_cfm": 58.0, "max_mmh2o": 48.0,
                                 "rpm": 16500, "max_dBA": 61.5,
                                 "max_wattage": 15.0},
    },
    "servers": {
        "6029U": {
            "display_name": "Supermicro SuperServer 6029U-E1CR4T",
            "form_factor": "2U",
            "chassis_width": 0.430, "chassis_height": 0.080,
            "chassis_length": 0.700,
            "fan_wall_z": 0.25, "fan_count": 4,
            "drive_bay_count": 8, "drive_bay_type": "3.5in SAS",
            "drive_bays_front": 8, "drive_bays_rear": 0,
            "drive_zone_z": [0.05, 0.20], "drive_zeta": 95.0,
            "drive_permeability": 5e-7,
            "cpu_sockets": 2, "cpu_zone_z": [0.35, 0.45],
            "cpu_zeta": 55.0, "cpu_permeability": 2e-7,
            "total_dimm_slots": 24,
            "populated_pcie_slots": 2, "pcie_zone_z": [0.55, 0.65],
            # riser cages hug the side walls, clear of the card band
            # (cards span x 0.02..W-0.02): static mechanics that stay
            # in the flow even with populated_pcie_slots 0
            "pcie_risers": [{"name": "riser_left", "x": [0.004, 0.016]},
                            {"name": "riser_right", "x": [0.414, 0.426]}],
            "heat_load": 350.0, "baseline_zeta": 25.0,
            # rear PSU bank: dense high-impedance porous blocks (each PSU is
            # a packed brick the air must be pulled through by its own 40 mm
            # fan - modeled as impedance; the PSU fan is drawn, not solved)
            "custom_zones": [
                {"name": "psu_1", "label": "PSU 1", "type": "porous",
                 "box": [0.005, 0.006, 0.652, 0.085, 0.074, 0.696],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "porous",
                 "box": [0.345, 0.006, 0.652, 0.425, 0.074, 0.696],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                              "medium": {"element_size_mm": 8.0},
                              "fine": {"element_size_mm": 4.0},
                              "ultra": {"element_size_mm": 2.5}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 35.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.15,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.2},
        },
        "R640": {
            "display_name": "Dell PowerEdge R640 (10-bay)",
            "form_factor": "1U",
            "chassis_width": 0.434, "chassis_height": 0.043,
            "chassis_length": 0.734,
            "fan_wall_z": 0.18, "fan_count": 8,
            "drive_bay_count": 10, "drive_bay_type": "2.5in (8 NVMe + 2 SATA)",
            "drive_bays_front": 10, "drive_bays_rear": 0,
            "drive_zone_z": [0.02, 0.13], "drive_zeta": 110.0,
            "drive_permeability": 3e-7,
            "cpu_sockets": 2, "cpu_zone_z": [0.30, 0.40],
            "cpu_zeta": 70.0, "cpu_permeability": 2e-7,
            "total_dimm_slots": 24,
            "populated_pcie_slots": 2, "pcie_zone_z": [0.55, 0.65],
            "pcie_risers": [{"name": "riser_left", "x": [0.004, 0.016]},
                            {"name": "riser_right", "x": [0.418, 0.430]}],
            "heat_load": 300.0, "baseline_zeta": 25.0,
            "custom_zones": [
                {"name": "psu_1", "label": "PSU 1", "type": "porous",
                 "box": [0.005, 0.004, 0.655, 0.080, 0.039, 0.729],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "porous",
                 "box": [0.354, 0.004, 0.655, 0.429, 0.039, 0.729],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                              "medium": {"element_size_mm": 8.0},
                              "fine": {"element_size_mm": 4.0},
                              "ultra": {"element_size_mm": 2.5}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 35.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.15,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.2},
        },
        "R740xd": {
            "display_name": "Dell PowerEdge R740xd (24-bay)",
            "form_factor": "2U",
            "chassis_width": 0.434, "chassis_height": 0.087,
            "chassis_length": 0.715,
            "fan_wall_z": 0.20, "fan_count": 6,
            "drive_bay_count": 24, "drive_bay_type": "2.5in SFF",
            "drive_bays_front": 24, "drive_bays_rear": 0,
            "drive_zone_z": [0.02, 0.14], "drive_zeta": 140.0,
            "drive_permeability": 2.5e-7,
            "cpu_sockets": 2, "cpu_zone_z": [0.34, 0.46],
            "cpu_zeta": 55.0, "cpu_permeability": 2e-7,
            "total_dimm_slots": 24,
            "populated_pcie_slots": 3, "pcie_zone_z": [0.55, 0.66],
            "pcie_risers": [{"name": "riser_left", "x": [0.004, 0.016]},
                            {"name": "riser_right", "x": [0.418, 0.430]}],
            "heat_load": 400.0, "baseline_zeta": 25.0,
            "custom_zones": [
                {"name": "psu_1", "label": "PSU 1", "type": "porous",
                 "box": [0.005, 0.006, 0.665, 0.080, 0.081, 0.710],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "porous",
                 "box": [0.354, 0.006, 0.665, 0.429, 0.081, 0.710],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                              "medium": {"element_size_mm": 8.0},
                              "fine": {"element_size_mm": 4.0},
                              "ultra": {"element_size_mm": 2.5}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 35.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.15,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.2},
        },
        "DL360": {
            "display_name": "HPE ProLiant DL360 Gen10",
            "form_factor": "1U",
            "chassis_width": 0.434, "chassis_height": 0.043,
            "chassis_length": 0.700,
            "fan_wall_z": 0.17, "fan_count": 7,
            "drive_bay_count": 8, "drive_bay_type": "2.5in SFF",
            "drive_bays_front": 8, "drive_bays_rear": 0,
            "drive_zone_z": [0.02, 0.12], "drive_zeta": 80.0,
            "drive_permeability": 4e-7,
            "cpu_sockets": 2, "cpu_zone_z": [0.30, 0.40],
            "cpu_zeta": 70.0, "cpu_permeability": 2e-7,
            "total_dimm_slots": 24,
            "populated_pcie_slots": 2, "pcie_zone_z": [0.52, 0.62],
            "pcie_risers": [{"name": "riser_left", "x": [0.004, 0.016]},
                            {"name": "riser_right", "x": [0.418, 0.430]}],
            "heat_load": 290.0, "baseline_zeta": 25.0,
            "custom_zones": [
                {"name": "psu_1", "label": "PSU 1", "type": "porous",
                 "box": [0.005, 0.004, 0.628, 0.080, 0.039, 0.695],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "porous",
                 "box": [0.354, 0.004, 0.628, 0.429, 0.039, 0.695],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                              "medium": {"element_size_mm": 8.0},
                              "fine": {"element_size_mm": 4.0},
                              "ultra": {"element_size_mm": 2.5}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 35.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.15,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.2},
        },
        "DL380": {
            "display_name": "HPE ProLiant DL380 Gen10",
            "form_factor": "2U",
            "chassis_width": 0.434, "chassis_height": 0.087,
            "chassis_length": 0.710,
            "fan_wall_z": 0.19, "fan_count": 6,
            "drive_bay_count": 8, "drive_bay_type": "2.5in SFF",
            "drive_bays_front": 8, "drive_bays_rear": 0,
            "drive_zone_z": [0.02, 0.13], "drive_zeta": 70.0,
            "drive_permeability": 4e-7,
            "cpu_sockets": 2, "cpu_zone_z": [0.33, 0.44],
            "cpu_zeta": 55.0, "cpu_permeability": 2e-7,
            "total_dimm_slots": 24,
            "populated_pcie_slots": 4, "pcie_zone_z": [0.54, 0.66],
            "pcie_risers": [{"name": "riser_left", "x": [0.004, 0.016]},
                            {"name": "riser_right", "x": [0.418, 0.430]}],
            "heat_load": 380.0, "baseline_zeta": 25.0,
            "custom_zones": [
                {"name": "psu_1", "label": "PSU 1", "type": "porous",
                 "box": [0.005, 0.006, 0.665, 0.080, 0.081, 0.706],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "porous",
                 "box": [0.354, 0.006, 0.665, 0.429, 0.081, 0.706],
                 "zeta": 200.0, "permeability": 1e-7,
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                              "medium": {"element_size_mm": 8.0},
                              "fine": {"element_size_mm": 4.0},
                              "ultra": {"element_size_mm": 2.5}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 35.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.15,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.2},
        },
        # --- Stage 2 profiles: outer dimensions from vendor specs, internal
        # --- layouts/zetas/fan counts are documented engineering estimates
        "C4130": {
            "display_name": "Dell PowerEdge C4130 (4x passive GPU)",
            "form_factor": "1U",
            "chassis_width": 0.434, "chassis_height": 0.0431,
            "chassis_length": 0.886,
            "fan_wall_z": 0.22, "fan_count": 8,
            "drive_bay_count": 2, "drive_bay_type": "2.5in SATA SSD",
            "drive_bays_front": 2, "drive_bays_rear": 0,
            "drive_zone_z": [0.02, 0.10], "drive_zeta": 30.0,
            "drive_permeability": 6e-7,
            "cpu_sockets": 2, "cpu_zone_z": [0.30, 0.42],
            "cpu_zeta": 60.0, "cpu_permeability": 2e-7,
            "total_dimm_slots": 16,
            "populated_pcie_slots": 0, "pcie_zone_z": [0.55, 0.65],
            # GPU riser blades in the 6 mm lanes between the passive GPU
            # bays and the centre PSU bank (touching faces are fine); the
            # side-wall positions of the rack servers would overlap the
            # GPU porous zones here
            "pcie_risers": [{"name": "riser_left", "x": [0.176, 0.182]},
                            {"name": "riser_right", "x": [0.252, 0.258]}],
            "heat_load": 1500.0, "baseline_zeta": 25.0,
            # heat_w: 300 W TDP per passive GPU (K80/M60 class) - pinned
            # explicitly so the energy equation localises 1200 of the
            # 1500 W heat_load in the GPU bays; the CPU sinks share the
            # remainder
            "custom_zones": [
                {"name": "gpu_1", "label": "GPU 1", "type": "porous",
                 "box": [0.010, 0.006, 0.56, 0.090, 0.037, 0.83],
                 "zeta": 170.0, "permeability": 1.5e-7, "telemetry": "gpu",
                 "heat_w": 300.0},
                {"name": "gpu_2", "label": "GPU 2", "type": "porous",
                 "box": [0.096, 0.006, 0.56, 0.176, 0.037, 0.83],
                 "zeta": 170.0, "permeability": 1.5e-7, "telemetry": "gpu",
                 "heat_w": 300.0},
                {"name": "gpu_3", "label": "GPU 3", "type": "porous",
                 "box": [0.258, 0.006, 0.56, 0.338, 0.037, 0.83],
                 "zeta": 170.0, "permeability": 1.5e-7, "telemetry": "gpu",
                 "heat_w": 300.0},
                {"name": "gpu_4", "label": "GPU 4", "type": "porous",
                 "box": [0.344, 0.006, 0.56, 0.424, 0.037, 0.83],
                 "zeta": 170.0, "permeability": 1.5e-7, "telemetry": "gpu",
                 "heat_w": 300.0},
                {"name": "psu_bank", "label": "PSU 1+1", "type": "solid",
                 "box": [0.182, 0.006, 0.60, 0.252, 0.037, 0.876],
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                              "medium": {"element_size_mm": 9.0},
                              "fine": {"element_size_mm": 4.5},
                              "ultra": {"element_size_mm": 2.8}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 40.0,
                             "pressure_min_pa": -300.0,
                             "deadzone_speed_min_ms": 0.10,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 1.0,
                             "optics_min_airflow_ms": 0.2},
        },
        "A7050X3": {
            "display_name": "Arista 7050CX3-32S (32x QSFP100 1U)",
            "form_factor": "1U",
            "chassis_width": 0.439, "chassis_height": 0.0445,
            "chassis_length": 0.406,
            "fan_wall_z": 0.36, "fan_count": 4,
            "drive_bay_count": 0, "drive_bay_type": None,
            "drive_bays_front": 0, "drive_bays_rear": 0,
            "cpu_sockets": 1, "cpu_zone_z": [0.15, 0.24],
            "cpu_zeta": 40.0, "cpu_permeability": 3e-7,
            "cpu_label": "ASIC",
            "total_dimm_slots": 4,
            "populated_pcie_slots": 0,
            "pcie_risers": [],       # switch: no riser hardware exists
            "heat_load": 350.0, "baseline_zeta": 20.0,
            "optics_zone_z": [0.005, 0.05],
            # optics heat_w: 32 QSFP100 modules x ~3.5 W (engineering
            # estimate) - the ASIC sink takes the rest of the heat_load
            "custom_zones": [
                {"name": "optics_cage", "label": "QSFP CAGE",
                 "type": "porous",
                 "box": [0.0, 0.0, 0.005, 0.439, 0.0445, 0.05],
                 "zeta": 55.0, "permeability": 5e-7,
                 "telemetry": "optics", "heat_w": 115.0},
                {"name": "psu_1", "label": "PSU 1", "type": "solid",
                 "box": [0.006, 0.004, 0.365, 0.081, 0.0405, 0.400],
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "solid",
                 "box": [0.358, 0.004, 0.365, 0.433, 0.0405, 0.400],
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 10.0},
                              "medium": {"element_size_mm": 6.0},
                              "fine": {"element_size_mm": 3.0},
                              "ultra": {"element_size_mm": 1.9}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 45.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.10,
                             "cpu_min_airflow_ms": 0.8,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.8},
        },
        "SN2700": {
            "display_name": "Mellanox SN2700 (32x QSFP28 1U)",
            "form_factor": "1U",
            "chassis_width": 0.427, "chassis_height": 0.0432,
            "chassis_length": 0.686,
            "fan_wall_z": 0.58, "fan_count": 4,
            "drive_bay_count": 0, "drive_bay_type": None,
            "drive_bays_front": 0, "drive_bays_rear": 0,
            "cpu_sockets": 1, "cpu_zone_z": [0.24, 0.34],
            "cpu_zeta": 42.0, "cpu_permeability": 3e-7,
            "cpu_label": "ASIC",
            "total_dimm_slots": 4,
            "populated_pcie_slots": 0,
            "pcie_risers": [],       # switch: no riser hardware exists
            "heat_load": 400.0, "baseline_zeta": 20.0,
            "optics_zone_z": [0.005, 0.05],
            # optics heat_w: 32 QSFP28 modules x ~3.5 W (engineering
            # estimate) - the ASIC sink takes the rest of the heat_load
            "custom_zones": [
                {"name": "optics_cage", "label": "QSFP CAGE",
                 "type": "porous",
                 "box": [0.0, 0.0, 0.005, 0.427, 0.0432, 0.05],
                 "zeta": 55.0, "permeability": 5e-7,
                 "telemetry": "optics", "heat_w": 115.0},
                {"name": "psu_1", "label": "PSU 1", "type": "solid",
                 "box": [0.005, 0.004, 0.59, 0.080, 0.039, 0.675],
                 "fan_rpm": 15000, "fan_size_mm": 40},
                {"name": "psu_2", "label": "PSU 2", "type": "solid",
                 "box": [0.347, 0.004, 0.59, 0.422, 0.039, 0.675],
                 "fan_rpm": 15000, "fan_size_mm": 40},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 12.0},
                              "medium": {"element_size_mm": 7.0},
                              "fine": {"element_size_mm": 3.5},
                              "ultra": {"element_size_mm": 2.2}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 45.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.10,
                             "cpu_min_airflow_ms": 0.8,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.8},
        },
        "ASR1006X": {
            "display_name": "Cisco ASR 1006-X (6RU aggregation router)",
            "form_factor": "6U",
            "chassis_width": 0.4374, "chassis_height": 0.2659,
            "chassis_length": 0.461,
            "fan_wall_z": 0.10, "fan_count": 6,
            "drive_bay_count": 0, "drive_bay_type": None,
            "drive_bays_front": 0, "drive_bays_rear": 0,
            "cpu_sockets": 0,
            "total_dimm_slots": 0,
            "populated_pcie_slots": 0,
            "pcie_risers": [],       # router: line cards, not PCIe risers
            "heat_load": 1800.0, "baseline_zeta": 30.0,
            "optics_zone_z": [0.13, 0.17],
            "custom_zones": [
                {"name": "air_filter", "label": "AIR FILTER",
                 "type": "porous",
                 "box": [0.0, 0.0, 0.005, 0.4374, 0.2659, 0.042],
                 "zeta": 20.0, "permeability": 8e-7},
                {"name": "psu_bank", "label": "PSU BANK (6x)",
                 "type": "solid",
                 "box": [0.02, 0.005, 0.050, 0.417, 0.070, 0.095],
                 "fan_rpm": 15000, "fan_size_mm": 40},
                # heat_w: the router has no CPU sinks or telemetry zones,
                # so the 1800 W heat_load is pinned where it is made -
                # the line-card and RP/ESP bays (engineering split)
                {"name": "linecard_bay", "label": "LINE CARDS",
                 "type": "porous",
                 "box": [0.02, 0.090, 0.13, 0.4174, 0.170, 0.40],
                 "zeta": 110.0, "permeability": 3e-7, "heat_w": 1250.0},
                {"name": "rp_esp_bay", "label": "RP / ESP",
                 "type": "porous",
                 "box": [0.02, 0.180, 0.13, 0.4174, 0.255, 0.40],
                 "zeta": 90.0, "permeability": 3e-7, "heat_w": 550.0},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 21.0},
                              "medium": {"element_size_mm": 12.5},
                              "fine": {"element_size_mm": 6.5},
                              "ultra": {"element_size_mm": 4.0}},
            "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 45.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.08,
                             "cpu_min_airflow_ms": 0.5,
                             "gpu_min_airflow_ms": 0.3,
                             "optics_min_airflow_ms": 0.4},
        },
        # side-view mapping: chassis_width = tower HEIGHT (x is drawn
        # top-to-bottom in the dashboard), chassis_height = tower width,
        # chassis_length = front-to-back depth (flow direction)
        "ATX-MID": {
            "display_name": "Generic ATX Mid-Tower (side view)",
            "form_factor": "MT",
            "chassis_width": 0.44, "chassis_height": 0.20,
            "chassis_length": 0.46,
            "fan_wall_z": 0.05, "fan_count": 3,
            "drive_bay_count": 2, "drive_bay_type": "3.5in HDD",
            "drive_bays_front": 2, "drive_bays_rear": 0,
            "cpu_sockets": 1, "cpu_zone_z": [0.20, 0.29],
            "cpu_zeta": 22.0, "cpu_permeability": 8e-7,
            "cpu_label": "CPU TOWER",
            "total_dimm_slots": 4,
            "populated_pcie_slots": 0,
            "pcie_risers": [],       # tower: cards mount straight to the
                                     # board, no riser cage (GPU is a
                                     # custom zone)
            "heat_load": 450.0, "baseline_zeta": 15.0,
            "custom_zones": [
                {"name": "front_mesh", "label": "FRONT MESH",
                 "type": "porous",
                 "box": [0.0, 0.0, 0.005, 0.44, 0.20, 0.022],
                 "zeta": 12.0, "permeability": 1e-6},
                {"name": "hdd_cage", "label": "HDD CAGE",
                 "type": "porous",
                 "box": [0.30, 0.01, 0.06, 0.43, 0.19, 0.17],
                 "zeta": 70.0, "permeability": 4e-7},
                # heat_w: ~250 W desktop GPU board power (engineering
                # estimate); the CPU tower takes the rest of the 450 W
                {"name": "gpu_card", "label": "GPU", "type": "porous",
                 "box": [0.262, 0.08, 0.18, 0.300, 0.196, 0.41],
                 "zeta": 55.0, "permeability": 5e-7, "telemetry": "gpu",
                 "heat_w": 250.0},
                {"name": "psu_shroud", "label": "PSU", "type": "solid",
                 "box": [0.375, 0.005, 0.28, 0.435, 0.195, 0.455],
                 "fan_rpm": 2000, "fan_size_mm": 120},
            ],
            "mesh_settings": {"coarse": {"element_size_mm": 20.0},
                              "medium": {"element_size_mm": 12.0},
                              "fine": {"element_size_mm": 6.0},
                              "ultra": {"element_size_mm": 3.7}},
            "requirements": {"inlet_temp_c": 24.0, "outlet_temp_max_c": 40.0,
                             "pressure_min_pa": -250.0,
                             "deadzone_speed_min_ms": 0.08,
                             "cpu_min_airflow_ms": 0.3,
                             "gpu_min_airflow_ms": 0.5,
                             "optics_min_airflow_ms": 0.0},
        },
    },
}


def load_config(path):
    """Read server_configs.json; auto-write the built-in example if absent."""
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f" [note] wrote example config -> {path}")
    with open(path) as f:
        return json.load(f)


def custom_fan_cfg(cfm, mmh2o):
    """Fan dict for user-entered specs. No rpm/dBA/wattage: the telemetry
    table prints n/a for those. 'custom' is a reserved fan key."""
    return {"display": f"Custom Fan ({cfm:g} CFM / {mmh2o:g} mmH2O)",
            "max_cfm": float(cfm), "max_mmh2o": float(mmh2o)}


def resolve_fan(cfg, args):
    """Config fan by key, or the CLI custom fan:
    --fan custom --fan-cfm X --fan-mmh2o Y."""
    if args.get("fan") == "custom":
        try:
            cfm = float(args["fan_cfm"])
            mmh2o = float(args["fan_mmh2o"])
        except (KeyError, TypeError, ValueError):
            raise SystemExit("--fan custom needs numeric --fan-cfm and "
                             "--fan-mmh2o")
        if cfm <= 0 or mmh2o <= 0:
            raise SystemExit("custom fan CFM and mmH2O must be > 0")
        return custom_fan_cfg(cfm, mmh2o)
    return cfg["fans"][args["fan"]]


# Blank template appended to server_configs.json by the wizard's "Custom
# Server Configuration" menu entry: a plain generic 2U the user then shapes
# by editing the JSON (and through the hardware prompts at run time).
CUSTOM_SERVER_TEMPLATE = {
    "display_name": "Custom Server",
    "form_factor": "2U",
    "chassis_width": 0.430, "chassis_height": 0.080,
    "chassis_length": 0.700,
    "fan_wall_z": 0.25, "fan_count": 4,
    "drive_bay_count": 8, "drive_bay_type": "3.5in HDD",
    "drive_bays_front": 8, "drive_bays_rear": 0,
    "drive_zone_z": [0.05, 0.20], "drive_zeta": 95.0,
    "drive_permeability": 5e-7,
    "cpu_sockets": 2, "cpu_zone_z": [0.35, 0.45],
    "cpu_zeta": 55.0, "cpu_permeability": 2e-7,
    "total_dimm_slots": 16,
    "populated_pcie_slots": 2, "pcie_zone_z": [0.55, 0.65],
    "pcie_risers": [{"name": "riser_left", "x": [0.004, 0.016]},
                    {"name": "riser_right", "x": [0.414, 0.426]}],
    "heat_load": 300.0, "baseline_zeta": 25.0,
    "mesh_settings": {"coarse": {"element_size_mm": 15.0},
                      "medium": {"element_size_mm": 8.0},
                      "fine": {"element_size_mm": 4.0},
                      "ultra": {"element_size_mm": 2.5}},
    "requirements": {"inlet_temp_c": 22.0, "outlet_temp_max_c": 35.0,
                     "pressure_min_pa": -250.0,
                     "deadzone_speed_min_ms": 0.15,
                     "cpu_min_airflow_ms": 0.5,
                     "gpu_min_airflow_ms": 0.3,
                     "optics_min_airflow_ms": 0.2},
}


def ensure_custom_server(cfg, name, config_path):
    """Wizard 'Custom Server Configuration': look the typed name up in the
    config; when absent, append the blank template under that name and
    PERSIST it to server_configs.json. Returns True when a new profile was
    written, False when the name already existed (it is then just used)."""
    if name in cfg["servers"]:
        return False
    tpl = json.loads(json.dumps(CUSTOM_SERVER_TEMPLATE))   # deep copy
    tpl["display_name"] = name
    cfg["servers"][name] = tpl
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2)
    return True


# Documented engineering estimates for the drive-type prompt: 2.5" bays
# leave more open backplane area than a dense 3.5" HDD wall.
DRIVE_TYPE_ZETA = {"2.5in NVMe/SAS": 0.75, "3.5in HDD": 1.15}


def apply_hw_overrides(s, hw):
    """Fold the wizard's hardware prompts into a RUNTIME copy of the profile
    (never persisted - the temp overlay config carries it to the workers).

    hw keys (all optional): drive_type ('2.5in NVMe/SAS' | '3.5in HDD'),
    heat_load_w, inlet_temp_c, exhaust_temp_c, gpu_count + gpu_watts,
    nic (bool), pcie_card_count (int >= 0). GPU/NIC/card-count need the
    profile's pcie_zone_z to mesh cards.

    pcie_card_count is the wizard's EXPLICIT population answer: when
    present it OWNS populated_pcie_slots (clamped to the profile's
    pcie_max_slots, default 8 - cards only spawn into slots that exist),
    superseding the GPU+NIC derivation below. GPU wattage still folds
    into heat_load from gpu_count/gpu_watts regardless. Absent = the
    legacy behaviour, bit for bit."""
    # Explicit bay count wins over the type prompt: 0 removes the cage
    # (build_geometry then emits no drive zone at all and the front bay is
    # open air). Clamped at 0 - a negative count is meaningless.
    if hw.get("drive_bay_count") is not None:
        s["drive_bay_count"] = max(0, int(hw["drive_bay_count"]))
    dt = hw.get("drive_type")
    if dt and s.get("drive_zone_z") and int(s.get("drive_bay_count", 0)) > 0:
        s["drive_zeta"] = float(s["drive_zeta"]) * DRIVE_TYPE_ZETA[dt]
        s["drive_bay_type"] = dt
    if hw.get("heat_load_w") is not None:
        s["heat_load"] = float(hw["heat_load_w"])
    reqs = s.setdefault("requirements", {})
    if hw.get("inlet_temp_c") is not None:
        reqs["inlet_temp_c"] = float(hw["inlet_temp_c"])
    if hw.get("exhaust_temp_c") is not None:
        reqs["outlet_temp_max_c"] = float(hw["exhaust_temp_c"])
    max_slots = int(s.get("pcie_max_slots", PCIE_MAX_SLOTS_DEFAULT))
    if s.get("pcie_zone_z") and ("nic" in hw or "gpu_count" in hw):
        # Once the wizard has asked, its answers OWN the PCIe population -
        # a card exists only because the user fitted a GPU or a NIC, so the
        # count is computed from the answers alone and whatever the profile
        # shipped with is superseded. Two bugs lived in doing this
        # incrementally: answering "no" to both left the profile default
        # standing (ghost cards in the mesh and every renderer), and "no
        # GPU + yes NIC" incremented that default instead of meaning one
        # card. Slots left empty are open air; only the static risers stay.
        # ("nic" is always written by the wizard when pcie_zone_z exists,
        # so its presence marks "the user was actually asked". Scripted
        # --worker runs never reach here and keep their JSON defaults.)
        n_gpu = int(hw.get("gpu_count") or 0)
        s["populated_pcie_slots"] = min(n_gpu + (1 if hw.get("nic") else 0),
                                        max_slots)
        s["nic_slot"] = bool(hw.get("nic"))
        if n_gpu > 0:
            gpu_w = n_gpu * float(hw.get("gpu_watts") or 0.0)
            s["heat_load"] = float(s["heat_load"]) + gpu_w
            # remembered SEPARATELY from the folded total: the energy
            # equation maps exactly this share onto the meshed cards'
            # washing shells (thermal_heat_plan) - the cards are solids
            # with no interior cells to source into
            s["gpu_heat_w"] = gpu_w
    if s.get("pcie_zone_z") and hw.get("pcie_card_count") is not None:
        # explicit population from the wizard's card-count prompt: this
        # answer OWNS the slot count (applied AFTER the GPU/NIC block so
        # it supersedes the derived count when both are supplied)
        n_cards = int(hw["pcie_card_count"])
        if n_cards < 0:
            raise ValueError("pcie_card_count must be >= 0")
        s["populated_pcie_slots"] = min(n_cards, max_slots)
    return s


def enforce_ultra_ram(mesh_level):
    """Stage 3 strict gate: 'ultra' stays selectable for everyone, but on a
    machine with less than 32 GB of physical RAM the selection must crash
    hard - a deliberate, unhandled MemoryError (spec: no greying out, no
    graceful fallback). Threshold is 30 GiB because the kernel reserves
    memory: a real 32 GB box reports ~31.3 GiB total, and it must PASS."""
    if mesh_level != "ultra":
        return
    total = None
    if HAVE_PSUTIL:
        total = psutil.virtual_memory().total
    else:
        try:                          # psutil failed to install: same gate
            with open("/proc/meminfo") as f:
                total = int(f.readline().split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return                    # RAM unknowable: cannot enforce
    if total < 30 * 2**30:
        raise MemoryError("Insufficient RAM for Ultra mesh. System halting.")


# ==============================================================================
#  PARAMETRIC GEOMETRY ENGINE (pure numpy/python - no gmsh imports here)
# ==============================================================================

# Physical group tags
VOL_OPEN, VOL_DRIVES, VOL_CPUS = 1, 2, 3
VOL_EXTRA0 = 20             # custom porous zones tag upward from here
SURF_FRONT, SURF_FAN, SURF_OUTLET, SURF_WALLS = 11, 12, 13, 14
GEOM_TOL = 1e-7


def psu_fan_dp(rpm, size_mm):
    """Static pressure estimate [Pa] for a zone's internal axial fan (the
    momentum-source strength of "fan_momentum" zones). Fan scaling laws:
    dp = PSI * rho * u_tip^2, u_tip = pi * D * (rpm/60). PSI is the
    documented PSU_FAN_PRESSURE_COEFF engineering estimate. Pure numpy -
    host-testable, and the single source of this math for the worker,
    build_geometry and the tests."""
    tip = np.pi * (float(size_mm) / 1000.0) * (float(rpm) / 60.0)
    return PSU_FAN_PRESSURE_COEFF * RHO_AIR * tip ** 2


def build_geometry(server_cfg):
    """Turn the config numbers into concrete boxes.

    Returns a dict:
      dims (W,H,L), fan_z, drives (name,box,K,C2) or None, cpus
      [(name,box,K,C2)], solids [(name,box)], extra_porous [zone dicts],
      labels {name: canvas/telemetry label}, solid_telem {name: kind},
      optics_box (+ optics_custom), n_bays.
    Rules (documented engineering assumptions):
      - drive cage: porous slab over drive_zone_z; SKIPPED when
        drive_bay_count is 0 or drive_zone_z is absent (switches, routers).
      - CPU sinks: cpu_sockets blocks (0 allowed), width 19 % of W, centred
        at W*(i+1)/(sockets+1), y from 6 % to 70 % of H, z = cpu_zone_z;
        optional cpu_label renames them (e.g. "ASIC" on switches).
      - RAM banks: sockets+1 solid banks in the gaps left/between/right of
        the sinks; width ~9 mm per DIMM slot (slots split evenly across
        banks), clamped to 80 % of the local gap; y 6 %..55 % of H.
      - PCIe: populated_pcie_slots solid cards spread across the CARD BAND
        of pcie_zone_z with 20 mm gaps; y 12 %..82 % of H. The band is
        the optional profile key "pcie_x_band": [x0, x1] in METRES -
        cards are laid out inside it edge to edge. Absent = the legacy
        full-width band [0.02, W - 0.02], bit-identical for every profile
        that does not set it. Use it to keep the runtime-generated cards
        clear of hardware that flanks the PCIe zone: PSUs beside the
        riser cage (R640), mid-width PSU banks (C4130). Validated: the
        band must satisfy 0 <= x0 < x1 <= W, and it must be wide enough
        that every requested card keeps >= PCIE_CARD_MIN_W (5 mm) of
        width - else a clear ValueError. Related optional profile key
        "pcie_max_slots" (default 8): the clamp apply_hw_overrides puts
        on the RUNTIME card population (wizard GPU/NIC answers and the
        explicit pcie_card_count) - cards only spawn into slots that
        physically exist. build_geometry itself does not clamp: a JSON
        that hardcodes more populated_pcie_slots than fit its band dies
        in the band-width validation above.
      - pcie_risers: STATIC riser cages at pcie_zone_z that persist when
        the cards are gone - populated_pcie_slots 0 still meshes them
        (the riser is chassis mechanics, not a plug-in card). A list of
        cage specs, each: "x": [x0, x1] cage x-range in METRES (required);
        optional "y_frac": [f0, f1] vertical extent as fractions of H
        (default [0.06, 0.90] - a little taller than the cards it holds);
        optional "z_frac": [f0, f1] sub-range of pcie_zone_z (default
        [0.0, 1.0] = the full zone); optional "name" (default
        pcie_riser_N; must NOT start with "pcie_card_") and "label"
        (canvas text, default "RISER"). Risers are SOLID blocks (sheet-
        metal cage + riser PCB = full blockage) appended to geo["solids"],
        so the gmsh cut, _validate_geometry and every renderer consume
        them from the one source. A riser overlapping a generated PCIe
        card raises ValueError (cards are runtime-dependent via the GPU/
        NIC prompts; touching faces are fine); riser-vs-porous overlap,
        chassis escape and fan-wall straddling are rejected by
        _validate_geometry like any other solid. An empty list means
        "no riser hardware" (switches, routers, towers).
      - custom_zones: list of named boxes [x0,y0,z0,x1,y1,z1] in metres.
        type "solid" (PSU blocks, cards) blocks flow entirely; type
        "porous" is an impedance zone (zeta over the zone's z-length +
        permeability - GPU heatsinks, optics cages, filters, card bays).
        Optional "label" (canvas text) and "telemetry" ("cpu"/"gpu"/
        "optics") hook a zone into the thermal threshold checks; optional
        "fan_rpm" + "fan_size_mm" mark the zone as carrying its own fan
        (PSUs) - drawn as the gold fan marker. By itself that stays an
        ANNOTATION (legacy profiles solve bit-identically); adding
        "fan_momentum": true to a POROUS fan zone makes the worker solve
        it as a real momentum source: a uniform +z body force
        f = dp / L_z [N/m^3] over the zone's cells, dp from psu_fan_dp()
        (fan scaling laws on fan_rpm/fan_size_mm - see
        PSU_FAN_PRESSURE_COEFF). The zone dict then carries "fan_dp_pa" /
        "fan_force" (plus fan_rpm/fan_size_mm) for the worker and the
        summary. fan_momentum requires fan_rpm, and on a SOLID zone it is
        rejected - solids are cut out of the fluid domain, so there are
        no cells to push on. Optional "heat_w" (POROUS zones only, >= 0)
        pins that many
        watts of the profile heat_load onto the zone as a volumetric
        source when the energy equation runs (thermal_heat_plan); a solid
        zone cannot carry it - solids are cut out of the fluid domain, so
        there are no cells to source into (declare the zone porous, or
        model it as a PCIe card and use the washing-shell path).
      - optics_zone_z [z0,z1]: moves the optics telemetry slab from the
        default rear-I/O position (switches: the front cage).
    """
    W = float(server_cfg["chassis_width"])
    H = float(server_cfg["chassis_height"])
    L = float(server_cfg["chassis_length"])
    fz = float(server_cfg["fan_wall_z"])
    labels = {}

    drives = None
    if (int(server_cfg.get("drive_bay_count", 0)) > 0
            and server_cfg.get("drive_zone_z")):
        dz0, dz1 = server_cfg["drive_zone_z"]
        drives = ("drive_array",
                  (0.0, 0.0, float(dz0), W, H, float(dz1)),
                  float(server_cfg["drive_permeability"]),
                  float(server_cfg["drive_zeta"]) / (dz1 - dz0))

    n_cpu = int(server_cfg.get("cpu_sockets", 0))
    cpu_label = server_cfg.get("cpu_label", "CPU")
    cpus, cpu_edges = [], []
    if n_cpu > 0:
        cz0, cz1 = server_cfg["cpu_zone_z"]
        sink_w = 0.19 * W
        cpu_c2 = float(server_cfg["cpu_zeta"]) / (cz1 - cz0)
        for i in range(n_cpu):
            xc = W * (i + 1) / (n_cpu + 1)
            b = (xc - sink_w / 2, 0.06 * H, float(cz0),
                 xc + sink_w / 2, 0.70 * H, float(cz1))
            name = f"cpu{i+1}_heatsink"
            cpus.append((name, b,
                         float(server_cfg["cpu_permeability"]), cpu_c2))
            cpu_edges.append((b[0], b[3]))
            labels[name] = cpu_label if n_cpu == 1 else f"{cpu_label} {i+1}"

    solids = []
    slots = int(server_cfg.get("total_dimm_slots", 0))
    if n_cpu > 0 and slots > 0:
        # DIMM banks DELIBERATELY share the CPU zone's z-range (they flank
        # the sockets). Bound explicitly here: inheriting cz0/cz1 from the
        # CPU block above was a scope leak that only worked because both
        # blocks are guarded by n_cpu > 0.
        cz0, cz1 = server_cfg["cpu_zone_z"]
        n_banks = n_cpu + 1
        spb = max(1, int(round(slots / n_banks)))
        gaps = []
        margin = 0.012
        prev = margin
        for x0, x1 in cpu_edges:
            gaps.append((prev, x0 - 0.005))
            prev = x1 + 0.005
        gaps.append((prev, W - margin))
        for i, (g0, g1) in enumerate(gaps[:n_banks]):
            gw = min(0.009 * spb, 0.8 * (g1 - g0))
            if gw <= 0.01:
                continue
            xc = 0.5 * (g0 + g1)
            solids.append((f"dimm_bank_{i}",
                           (xc - gw / 2, 0.06 * H, float(cz0),
                            xc + gw / 2, 0.55 * H, float(cz1))))

    n_pcie = int(server_cfg.get("populated_pcie_slots", 0))
    band = server_cfg.get("pcie_x_band")
    if band is not None:
        # validated even with 0 cards: a typo'd band must die at config
        # time, not on the first wizard run that populates a slot
        try:
            bx0, bx1 = (float(v) for v in band)
        except (TypeError, ValueError):
            raise ValueError('pcie_x_band must be "pcie_x_band": [x0, x1] '
                             "in metres")
        if not (-1e-9 <= bx0 < bx1 <= W + 1e-9):
            raise ValueError(
                f"pcie_x_band [{bx0:g}, {bx1:g}] must satisfy "
                f"0 <= x0 < x1 <= chassis width ({W:g})")
    else:
        bx0, bx1 = 0.02, W - 0.02      # legacy full-width card band
    if n_pcie > 0:
        pz0, pz1 = server_cfg["pcie_zone_z"]
        gap = 0.02
        # band absent: the ORIGINAL full-width expression, kept verbatim so
        # legacy card coordinates stay BIT-identical (float associativity -
        # (W-0.02)-0.02 != W-2*0.02 in the last ulp, and the meshed cell
        # count is a pinned regression gate)
        card_w = ((W - 2 * 0.02 - (n_pcie - 1) * gap) / n_pcie
                  if band is None
                  else ((bx1 - bx0) - (n_pcie - 1) * gap) / n_pcie)
        if card_w < PCIE_CARD_MIN_W:
            raise ValueError(
                f"pcie_x_band [{bx0:g}, {bx1:g}] is too narrow for "
                f"{n_pcie} card(s): each card would be {card_w * 1000:.1f} "
                f"mm wide (minimum {PCIE_CARD_MIN_W * 1000:g} mm) - widen "
                "the band or populate fewer slots")
        for i in range(n_pcie):
            x0 = bx0 + i * (card_w + gap)
            solids.append((f"pcie_card_{i+1}",
                           (x0, 0.12 * H, float(pz0),
                            x0 + card_w, 0.82 * H, float(pz1))))
        if server_cfg.get("nic_slot"):     # wizard: last populated slot is
            labels[f"pcie_card_{n_pcie}"] = "NIC"    # the networking card

    # PCIe riser cages: STATIC chassis mechanics at pcie_zone_z. Unlike the
    # cards above they do NOT depend on populated_pcie_slots - pulling every
    # card (or the wizard shipping 0 GPUs) leaves the cages standing in the
    # flow. Solid blocks (cage sheet metal + riser PCB = full blockage) so
    # the mesh cut and all renderers pick them up from geo["solids"].
    risers = server_cfg.get("pcie_risers") or []
    if risers:
        if not server_cfg.get("pcie_zone_z"):
            raise ValueError("pcie_risers requires pcie_zone_z")
        rz0, rz1 = (float(v) for v in server_cfg["pcie_zone_z"])
        card_boxes = [(n, b) for n, b in solids
                      if n.startswith("pcie_card_")]
        for j, spec in enumerate(risers):
            name = spec.get("name") or f"pcie_riser_{j + 1}"
            if name.startswith("pcie_card_"):
                raise ValueError(f"riser '{name}': names must not start "
                                 "with 'pcie_card_' (reserved for cards)")
            try:
                rx0, rx1 = (float(v) for v in spec["x"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"riser '{name}': needs \"x\": [x0, x1] "
                                 "in metres")
            yf0, yf1 = (float(v) for v in spec.get("y_frac", (0.06, 0.90)))
            zf0, zf1 = (float(v) for v in spec.get("z_frac", (0.0, 1.0)))
            b = (rx0, yf0 * H, rz0 + zf0 * (rz1 - rz0),
                 rx1, yf1 * H, rz0 + zf1 * (rz1 - rz0))
            # cards are generated from the RUNTIME populated_pcie_slots, so
            # a riser/card collision is a config error caught here (solid-
            # vs-solid overlap is not otherwise validated; touching is ok)
            for cn, cb in card_boxes:
                if all(min(b[i + 3], cb[i + 3]) - max(b[i], cb[i]) > 1e-6
                       for i in range(3)):
                    raise ValueError(
                        f"riser '{name}' overlaps '{cn}' - keep riser "
                        "x-ranges clear of the card band")
            labels[name] = spec.get("label", "RISER")
            solids.append((name, b))

    solid_telem = {}
    extra_porous = []
    fan_marks = []          # zones carrying their own fan (PSUs): rendered
    for k, zone in enumerate(server_cfg.get("custom_zones", [])):
        name = zone.get("name") or f"zone_{k}"
        b = tuple(float(v) for v in zone["box"])
        if len(b) != 6:
            raise ValueError(f"custom zone '{name}': box must be "
                             "[x0,y0,z0,x1,y1,z1] in metres")
        kind = zone.get("type", "solid")
        labels[name] = zone.get("label", _block_label(name))
        telem = zone.get("telemetry")
        heat_w = zone.get("heat_w")
        if heat_w is not None and not (float(heat_w) >= 0.0):
            raise ValueError(f"custom zone '{name}': heat_w must be a "
                             "wattage >= 0")
        if zone.get("fan_rpm"):
            fan_marks.append({"name": name, "box": b,
                              "rpm": int(zone["fan_rpm"]),
                              "size_mm": int(zone.get("fan_size_mm", 40))})
        if kind == "porous":
            if "zeta" not in zone or "permeability" not in zone:
                raise ValueError(f"porous zone '{name}' needs zeta and "
                                 "permeability")
            zd = {
                "name": name, "box": b, "zeta": float(zone["zeta"]),
                "C2": float(zone["zeta"]) / max(b[5] - b[2], 1e-9),
                "K": float(zone["permeability"]),
                "tag": VOL_EXTRA0 + len(extra_porous),
                "telemetry": telem,
                "heat_w": float(heat_w) if heat_w is not None else None}
            if zone.get("fan_momentum"):
                # solved momentum source (see docstring): keys added ONLY
                # on flagged zones, so unflagged profiles' geometry dicts
                # (and their solves) stay byte-identical
                if not zone.get("fan_rpm"):
                    raise ValueError(f"custom zone '{name}': fan_momentum "
                                     "requires fan_rpm (and optionally "
                                     "fan_size_mm)")
                rpm = int(zone["fan_rpm"])
                size = int(zone.get("fan_size_mm", 40))
                dp = float(psu_fan_dp(rpm, size))
                zd.update(fan_rpm=rpm, fan_size_mm=size, fan_dp_pa=dp,
                          fan_force=dp / max(b[5] - b[2], 1e-9))
            extra_porous.append(zd)
        elif kind == "solid":
            if zone.get("fan_momentum"):
                raise ValueError(
                    f"custom zone '{name}': fan_momentum is only supported "
                    "on porous zones - a solid is CUT OUT of the fluid "
                    "domain, so there are no cells for the body force to "
                    "act on (declare the zone porous like the R640 PSUs)")
            if heat_w is not None:
                raise ValueError(
                    f"custom zone '{name}': heat_w is only supported on "
                    "porous zones - a solid is CUT OUT of the fluid "
                    "domain, so a volumetric source inside it would land "
                    "in a region with no cells")
            solids.append((name, b))
            if telem:
                solid_telem[name] = telem
        else:
            raise ValueError(f"custom zone '{name}': type must be "
                             "'solid' or 'porous'")

    oz = server_cfg.get("optics_zone_z")
    optics_box = ((0.05 * W, 0.10 * H, float(oz[0]),
                   0.95 * W, 0.90 * H, float(oz[1])) if oz
                  else (0.05 * W, 0.10 * H, L - 0.03,
                        0.95 * W, 0.90 * H, L - 0.005))

    geo = {
        "dims": (W, H, L), "fan_z": fz,
        "drives": drives, "cpus": cpus, "solids": solids,
        "extra_porous": extra_porous, "labels": labels,
        "solid_telem": solid_telem, "fan_marks": fan_marks,
        "optics_box": optics_box, "optics_custom": bool(oz),
        "n_bays": int(server_cfg.get("drive_bay_count", 0)),
    }
    _validate_geometry(geo)
    return geo


def mesh_level_lc(server_cfg, level):
    """Element size [m] for a profile: a preset name from mesh_settings, or
    a literal millimetre value ("0.8") - the wizard's custom option and a
    numeric --mesh both land here. Enforces the MESH_MM_FLOOR sanity floor
    on EVERY source (a typo'd config or flag must not hang gmsh; the
    `not >=` form also rejects NaN). Falls back to the built-in presets
    when the JSON predates the mesh_settings block."""
    try:
        mm = float(level)
    except (TypeError, ValueError):
        ms = server_cfg.get("mesh_settings") or DEFAULT_MESH_SETTINGS
        if level not in ms:
            raise SystemExit(f"--mesh must be a preset ({', '.join(ms)}) "
                             "or an element size in mm")
        mm = float(ms[level]["element_size_mm"])
    if not (mm >= MESH_MM_FLOOR):
        raise SystemExit(f"element size {mm:g} mm is below the "
                         f"{MESH_MM_FLOOR:g} mm floor")
    return mm / 1000.0


def mesh_desc(level, lc):
    """'coarse preset' or '0.8 mm custom' - worker log + run report."""
    try:
        float(level)
    except (TypeError, ValueError):
        return f"{level} preset"
    return f"{lc * 1000:g} mm custom"


def _midplane_hit(b, H):
    """True when 3-D box b PROPERLY straddles the chassis mid-height plane
    y = H/2 - the membership test of the 2-D planar engine (a box merely
    touching the plane is out). Strictness matters: two boxes stacked in y
    can then never both land in the plane with overlapping footprints,
    because both containing an open interval around H/2 means they overlap
    in y, and the 3-D overlap validation already forbids that combination
    - so projected 2-D footprints are guaranteed overlap-free."""
    return b[1] + GEOM_TOL < 0.5 * H < b[4] - GEOM_TOL


def est_cells(geo, lc, engine="3d"):
    """Cell count estimate - guidance for the RAM warning and progress
    panel only.
    3d: tetrahedra ~ 3.6*V_fluid/lc^3 for the graded fields (fine band lc,
    bulk 2.2*lc). Calibrated on the 6029U: coarse 25,008 actual vs 22.7k
    estimated, medium 139,097 vs 150k - within ~10 %.
    2d: triangles ~ 1.5*A_fluid/lc^2 over the mid-height footprint (solids
    straddling y = H/2 subtracted). UNCALIBRATED engineering estimate
    sitting between the fine-band (~2.3/lc^2) and bulk (~0.5/lc^2)
    triangle densities of the same grading."""
    W, H, L = geo["dims"]
    if str(engine).lower() == "2d":
        area = W * L - sum((b[3] - b[0]) * (b[5] - b[2])
                           for _n, b in geo["solids"]
                           if _midplane_hit(b, H))
        return 1.5 * area / lc**2
    vol = W * H * L - sum((b[3] - b[0]) * (b[4] - b[1]) * (b[5] - b[2])
                          for _n, b in geo["solids"])
    return 3.6 * vol / lc**3


def fan_operating_point(server_cfg, fan_cfg, geo, duty=1.0):
    """Fan operating point ESTIMATE -> inlet BC level, plus the affinity-
    scaled telemetry numbers. Pure numpy - importable and host-testable
    with no solver stack (the single source of truth for this math; the
    worker and the tests both call it).

    The quadratic fan curve P = Pmax(1-(Q/Qmax)^2), fan_count fans in
    parallel, is intersected with the impedance estimate K = rho*zeta_est/
    (2A^2) (custom porous zones contribute zeta x their covered cross-
    section fraction). The meshed CFD impedance is the truth; this
    estimate only chooses the fan-plane velocity.

    duty = N/N_rated (1.0 = rated RPM = legacy behaviour, bit-identical).
    Fan Affinity Laws applied BEFORE the intersection: Q ~ N (qmax*duty),
    dP ~ N^2 (pmax*duty^2); telemetry: shaft power ~ N^3, dBA ~ dBA_rated
    + 50*log10(duty). rpm/watts/dba are None when the fan config carries
    no rating (custom wizard fans).

    Returns dict: q_op [m^3/s], fan_vz [m/s], cfm (= q_op in CFM),
    zeta_est, K_est, qmax [m^3/s, all fans, duty-scaled], pmax [Pa,
    duty-scaled], duty, rpm_rated, rpm (duty-scaled), cfm_max / mmh2o_max
    (PER-FAN curve maxima, duty-scaled), watts, dba (per-fan,
    duty-scaled)."""
    W, H, _L = geo["dims"]
    area = W * H
    zeta_est = (float(server_cfg.get("drive_zeta", 0.0))
                + 0.5 * float(server_cfg.get("cpu_zeta", 0.0))
                + float(server_cfg.get("baseline_zeta", 25.0)))
    for z in geo["extra_porous"]:
        b = z["box"]
        afrac = (b[3] - b[0]) * (b[4] - b[1]) / area
        zeta_est += z["zeta"] * min(afrac, 1.0)
    K_est = RHO_AIR * zeta_est / (2.0 * area**2)
    duty = float(duty)
    qmax = (fan_cfg["max_cfm"] / M3S_TO_CFM * server_cfg["fan_count"]
            * duty)
    pmax = fan_cfg["max_mmh2o"] * 9.80665 * duty ** 2
    q_op = float(np.sqrt(pmax / (K_est + pmax / qmax**2)))
    rpm = fan_cfg.get("rpm")
    dba = fan_cfg.get("max_dBA")
    watts = fan_cfg.get("max_wattage")
    return {
        "q_op": q_op, "fan_vz": q_op / area, "cfm": q_op * M3S_TO_CFM,
        "zeta_est": zeta_est, "K_est": K_est, "qmax": qmax, "pmax": pmax,
        "duty": duty,
        "rpm_rated": rpm,
        "rpm": rpm * duty if rpm else None,
        "cfm_max": fan_cfg["max_cfm"] * duty,
        "mmh2o_max": fan_cfg["max_mmh2o"] * duty ** 2,
        "watts": watts * duty ** 3 if watts else None,
        "dba": dba + 50.0 * float(np.log10(duty)) if dba else None,
    }


def combined_noise_dba(dba_per_fan, n_fans):
    """Combined free-field noise of n_fans equal sources: per-fan dBA +
    10*log10(N) - the SAME engineering estimate the launcher's acoustics
    table prints. Pure numpy - host-testable. None in, None out (unrated
    custom fans carry no dBA)."""
    if dba_per_fan is None:
        return None
    return float(dba_per_fan) + 10.0 * float(np.log10(max(int(n_fans), 1)))


def solve_duty_for_dba(target_dba, dba_rated, n_fans,
                       duty_min=FAN_DUTY_MIN, duty_max=FAN_DUTY_MAX):
    """Invert the acoustic affinity law: the maximum fan duty (N/N_rated)
    at which n_fans together stay at or under target_dba [combined
    free-field dBA]. The EXACT algebraic inverse of the laws used
    everywhere else in this file (single source of truth - see
    fan_operating_point and combined_noise_dba):

        per-fan   dBA(duty)  = dBA_rated + 50*log10(duty)
        combined  dBA_total  = per-fan + 10*log10(n_fans)
        =>  duty  = 10 ** ((target - dBA_rated - 10*log10(n_fans)) / 50)

    Pure numpy - importable and host-testable with no solver stack.

    Returns dict:
      solvable        False when dba_rated is None (custom/unrated fan:
                      the target CANNOT be solved and is not guessed at);
                      duty_cap/duty_unclamped are then None.
      duty_cap        permitted duty, clamped into [duty_min, duty_max].
                      When the target is unachievable this is duty_min -
                      the quietest the model allows - NEVER an impossible
                      value below the stall floor.
      duty_unclamped  the raw algebraic inverse, pre-clamp.
      achievable      False when even duty_min is louder than the target
                      (duty_unclamped < duty_min): the fan cannot meet
                      this target at any permitted speed.
      constrained     True when the target caps duty anywhere below
                      duty_max; a target above the whole duty band
                      imposes no constraint at all."""
    if dba_rated is None:
        return {"solvable": False, "duty_cap": None,
                "duty_unclamped": None, "achievable": False,
                "constrained": False}
    raw = float(10.0 ** ((float(target_dba) - float(dba_rated)
                          - 10.0 * float(np.log10(max(int(n_fans), 1))))
                         / 50.0))
    return {"solvable": True,
            "duty_cap": float(min(max(raw, duty_min), duty_max)),
            "duty_unclamped": raw,
            "achievable": raw >= duty_min,
            "constrained": raw < duty_max}


def apply_dba_ceiling(requested_duty, target_dba, dba_rated, n_fans,
                      duty_min=FAN_DUTY_MIN, duty_max=FAN_DUTY_MAX):
    """Compose an explicit --fan-duty with a --dba-target ceiling: the
    QUIETER of the two wins - effective duty = min(requested, duty_cap) -
    so a noise ceiling is never silently exceeded by an explicit duty,
    while an explicit duty already AT or UNDER the ceiling is honoured
    unchanged. Pure numpy - host-testable.

    Returns (effective_duty, info): info is the solve_duty_for_dba dict
    plus
      binding  True when the ceiling actually capped the requested duty
               (duty_cap < requested); False when the explicit/default
               duty was already at or under it.
    For an unrated fan (solvable False) the requested duty passes through
    untouched - the CALLER decides whether that is an error (the worker
    refuses loudly rather than silently ignoring the target)."""
    req = float(requested_duty)
    info = dict(solve_duty_for_dba(target_dba, dba_rated, n_fans,
                                   duty_min=duty_min, duty_max=duty_max))
    if not info["solvable"]:
        info["binding"] = False
        return req, info
    info["binding"] = info["duty_cap"] < req
    return min(req, info["duty_cap"]), info


def thermal_enabled(args):
    """Parse the worker args key "thermal" (CLI --thermal): the gate of the
    energy equation. OFF by default - a run without it is byte-identical
    to the pre-thermal solver. Accepts on/1/true/yes and off/0/false/no
    (missing/empty = off); anything else is a loud SystemExit like the
    other worker flags. Pure stdlib - host-testable."""
    val = args.get("thermal")
    sval = str(val).strip().lower() if val is not None else ""
    if sval in ("", "0", "off", "false", "no"):
        return False
    if sval in ("1", "on", "true", "yes"):
        return True
    raise SystemExit('--thermal must be "on" or "off"')


def thermal_heat_plan(server_cfg, geo, engine="3d"):
    """Map the profile's TOTAL heat_load [W] onto concrete source regions
    for the energy equation. Pure numpy/python - importable and
    host-testable with no solver stack (the worker measures the meshed
    region volumes and turns these watts into densities [W/m^3]).

    Distribution rules, in order:
      1. explicit: porous custom zones with "heat_w" get exactly that many
         watts (C4130 GPU bays, router line cards, switch optics cages -
         documented engineering estimates in the config).
      2. cards: GPUs fitted through the wizard are meshed as SOLID PCIe
         cards - cut OUT of the fluid domain, no cells inside them - so
         their wattage (runtime key gpu_heat_w, split evenly over the
         non-NIC cards) is earmarked for the 1 cm washing SHELL of fluid
         around each card (GPU_SHELL_M; the same shell the airflow
         telemetry samples). See the worker for the full justification.
      3. rest_w = heat_load minus the above goes as ONE uniform volumetric
         density over the union of the implicit heat regions: the CPU
         heatsink cells plus porous zones with telemetry "cpu"/"gpu" that
         carry no explicit heat_w. Drive cages, filters and PSU impedance
         zones deliberately get nothing (their dissipation is small or
         unmodelled; heat_w pins it explicitly where a config wants it).
      4. uniform_fallback: no implicit region exists at all (no CPU sinks,
         no telemetry zones) -> rest_w spreads over the WHOLE fluid
         domain: energy-conservative, just not localised.
    engine "2d": a region absent from the mid-height slice (_midplane_hit)
    cannot receive watts there - its share folds back into rest_w so the
    planar energy balance still carries the full load.

    Returns {"total_w", "explicit" [(tag, name, W)], "cards"
    [(name, box, W)], "implicit_tags" [(tag, name)], "rest_w",
    "uniform_fallback"}."""
    two_d = str(engine).lower() == "2d"
    H = geo["dims"][1]

    def in_slice(b):
        return not two_d or _midplane_hit(b, H)

    total = float(server_cfg.get("heat_load", 0.0))
    assigned = 0.0
    explicit, implicit = [], []
    for z in geo["extra_porous"]:
        if not in_slice(z["box"]):
            continue
        if z.get("heat_w") is not None:
            explicit.append((z["tag"], z["name"], float(z["heat_w"])))
            assigned += float(z["heat_w"])
        elif z["telemetry"] in ("cpu", "gpu"):
            implicit.append((z["tag"], z["name"]))
    cards = []
    gpu_w = float(server_cfg.get("gpu_heat_w") or 0.0)
    if gpu_w > 0.0:
        gpu_cards = [(n, b) for n, b in geo["solids"]
                     if n.startswith("pcie_card_")
                     and geo["labels"].get(n) != "NIC" and in_slice(b)]
        if gpu_cards:
            per_card = gpu_w / len(gpu_cards)
            cards = [(n, b, per_card) for n, b in gpu_cards]
            assigned += gpu_w
    if any(in_slice(b) for _n, b, _k, _c in geo["cpus"]):
        implicit.insert(0, (VOL_CPUS, "cpu_heatsinks"))
    rest_w = max(0.0, total - assigned)
    return {"total_w": total, "explicit": explicit, "cards": cards,
            "implicit_tags": implicit, "rest_w": rest_w,
            "uniform_fallback": rest_w > 0.0 and not implicit}


def _validate_geometry(geo):
    W, H, L = geo["dims"]
    fz = geo["fan_z"]
    if not (0.0 < fz < L):
        raise ValueError("fan_wall_z outside the chassis")

    def ok(b):
        return (-1e-9 <= b[0] < b[3] <= W + 1e-9
                and -1e-9 <= b[1] < b[4] <= H + 1e-9
                and -1e-9 <= b[2] < b[5] <= L + 1e-9)

    porous = ([(geo["drives"][0], geo["drives"][1])] if geo["drives"] else [])
    porous += [(n, b) for n, b, _k, _c in geo["cpus"]]
    porous += [(z["name"], z["box"]) for z in geo["extra_porous"]]
    # the optics telemetry slab joins the envelope/straddle checks (a
    # malformed optics_zone_z used to pass silently) but NOT the overlap
    # checks below: it is a sampling region, not meshed geometry, and it
    # deliberately overlaps porous cages (switch QSFP zones)
    optics = ([("optics_box", tuple(geo["optics_box"]))]
              if geo.get("optics_box") else [])
    for nm, b in porous + geo["solids"] + optics:
        if not ok(b):
            raise ValueError(f"box '{nm}' escapes the chassis: {b}")
        if b[2] < fz < b[5]:
            raise ValueError(f"box '{nm}' straddles the fan wall")

    # porous zones must not overlap solids or each other: the mesh builder
    # assigns volumes through the fragment map, which requires every porous
    # box to lie fully in fluid (touching faces are fine)
    def overlap(a, b):
        return all(min(a[i + 3], b[i + 3]) - max(a[i], b[i]) > 1e-6
                   for i in range(3))

    for i, (nm_a, a) in enumerate(porous):
        for nm_b, b in geo["solids"]:
            if overlap(a, b):
                raise ValueError(f"porous zone '{nm_a}' overlaps solid "
                                 f"'{nm_b}'")
        for nm_b, b in porous[i + 1:]:
            if overlap(a, b):
                raise ValueError(f"porous zones '{nm_a}' and '{nm_b}' "
                                 "overlap")


# ==============================================================================
#  WORKER: mesh, transient solve, sampling, streaming  (dolfinx/gmsh inside)
# ==============================================================================

def _gmsh_build_model(geo, lc):
    """Build the gmsh OCC model, physical groups and mesh for a geometry
    dict. Pure gmsh - no dolfinx/MPI - so it is host-testable; the caller
    owns gmsh.initialize()/finalize() and the model is gmsh global state.

    Two mesh components meet at the fan wall WITHOUT sharing it (two
    coincident boundary copies of the plane): a single conforming mesh with
    an interior velocity-Dirichlet plane leaks most of the flux through a
    divergence sheet invisible to the P1 pressure space (verified in the
    steady version - 25x deficit). Both copies get the fan velocity BC.
    Solids are cut from, and porous zones fragmented into, whichever side
    of the fan wall they sit on (validation forbids straddling), so custom
    zones work in the drive-side component too (switch optics cages,
    front PSU banks).
    """
    import gmsh

    occ = gmsh.model.occ
    W, H, L = geo["dims"]
    fz = geo["fan_z"]
    lc_bulk = min(2.2 * lc, 0.035)

    def add_box(b):
        return occ.addBox(b[0], b[1], b[2],
                          b[3] - b[0], b[4] - b[1], b[5] - b[2])

    porous = []
    if geo["drives"]:
        porous.append(("drives", geo["drives"][1]))
    for _n, b, _k, _c in geo["cpus"]:
        porous.append(("cpus", b))
    for z in geo["extra_porous"]:
        porous.append((z["tag"], z["box"]))

    # Volumes are classified through the fragment parent->child map, NOT by
    # centre-of-mass containment: the open region's centroid can land inside
    # a centred porous box (single mid-chassis ASIC sink), which silently
    # mistagged it. A porous zone's fluid volume is a child of BOTH its tool
    # box and the side slab; side-only children are open air. Validation
    # forbids solid/porous overlap, so tool-only children cannot occur.
    zone_vols = {key: [] for key, _b in porous}
    open_vols = []
    for z0, z1 in ((0.0, fz), (fz, L)):        # the two mesh components
        side = [(3, occ.addBox(0, 0, z0, W, H, z1 - z0))]
        cut = [(3, add_box(b)) for _n, b in geo["solids"]
               if z0 - GEOM_TOL <= b[2] and b[5] <= z1 + GEOM_TOL]
        if cut:
            side, _ = occ.cut(side, cut)
        tools = [(key, add_box(b)) for key, b in porous
                 if z0 - GEOM_TOL <= b[2] and b[5] <= z1 + GEOM_TOL]
        if not tools:
            open_vols.extend(tag for _d, tag in side)
            continue
        _out, out_map = occ.fragment(side, [(3, t) for _k, t in tools])
        side_children = set()
        for children in out_map[:len(side)]:
            side_children.update(tag for _d, tag in children)
        claimed = set()
        for (key, _t), children in zip(tools, out_map[len(side):]):
            for _d, vtag in children:
                if vtag not in side_children:
                    raise RuntimeError(
                        f"porous zone piece {vtag} fell outside the fluid "
                        "region - overlap validation should have caught "
                        "this config")
                if vtag not in claimed:
                    zone_vols[key].append(vtag)
                    claimed.add(vtag)
        open_vols.extend(t for t in sorted(side_children)
                         if t not in claimed)
    occ.synchronize()

    gmsh.model.addPhysicalGroup(3, open_vols, VOL_OPEN, name="open_air")
    if zone_vols.get("drives"):
        gmsh.model.addPhysicalGroup(3, zone_vols["drives"], VOL_DRIVES,
                                    name="drives")
    if zone_vols.get("cpus"):
        gmsh.model.addPhysicalGroup(3, zone_vols["cpus"], VOL_CPUS,
                                    name="cpus")
    for z in geo["extra_porous"]:
        if zone_vols.get(z["tag"]):
            gmsh.model.addPhysicalGroup(3, zone_vols[z["tag"]], z["tag"],
                                        name=z["name"])

    front_s, fan_s, outlet_s, wall_s = [], [], [], []
    for dim, tag in gmsh.model.getEntities(2):
        vols_up, _ = gmsh.model.getAdjacencies(dim, tag)
        if len(vols_up) != 1:
            continue
        c = occ.getCenterOfMass(dim, tag)
        if abs(c[2]) < GEOM_TOL:
            front_s.append(tag)
        elif abs(c[2] - L) < GEOM_TOL:
            outlet_s.append(tag)
        elif abs(c[2] - fz) < GEOM_TOL:
            fan_s.append(tag)
        else:
            wall_s.append(tag)
    gmsh.model.addPhysicalGroup(2, front_s, SURF_FRONT, name="front")
    gmsh.model.addPhysicalGroup(2, fan_s, SURF_FAN, name="fan_wall")
    gmsh.model.addPhysicalGroup(2, outlet_s, SURF_OUTLET, name="outlet")
    gmsh.model.addPhysicalGroup(2, wall_s, SURF_WALLS, name="walls")

    # mesh-size fields derived from the geometry bands
    comp_z = ([b[2] for _n, b in geo["solids"]]
              + [b[2] for _n, b, _k, _c in geo["cpus"]]
              + [z["box"][2] for z in geo["extra_porous"]])
    comp_z0 = min(comp_z) if comp_z else fz
    front_z0 = geo["drives"][1][2] if geo["drives"] else 0.0
    f_fine = gmsh.model.mesh.field.add("Box")
    for k, v in (("VIn", lc), ("VOut", lc_bulk),
                 ("XMin", -0.01), ("XMax", W + 0.01), ("YMin", -0.01),
                 ("YMax", H + 0.01), ("ZMin", max(fz, comp_z0 - 0.03)),
                 ("ZMax", L - 0.02)):
        gmsh.model.mesh.field.setNumber(f_fine, k, v)
    f_med = gmsh.model.mesh.field.add("Box")
    for k, v in (("VIn", 1.3 * lc), ("VOut", lc_bulk),
                 ("XMin", -0.01), ("XMax", W + 0.01), ("YMin", -0.01),
                 ("YMax", H + 0.01),
                 ("ZMin", max(0.0, front_z0 - 0.02)),
                 ("ZMax", min(L, fz + 0.03))):
        gmsh.model.mesh.field.setNumber(f_med, k, v)
    f_min = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(f_min, "FieldsList", [f_fine, f_med])
    gmsh.model.mesh.field.setAsBackgroundMesh(f_min)
    gmsh.option.setNumber("Mesh.MeshSizeMax", lc_bulk)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.model.mesh.generate(3)


def _gmsh_build_model_2d(geo, lc):
    """TRUE 2-D planar engine: gmsh model of the chassis MID-HEIGHT plane
    y = H/2. Model coordinates are (chassis x, chassis z) - gmsh's native
    x-y plane carries the footprints, so meshed coordinate 1 IS the flow
    direction z. Pure gmsh - no dolfinx/MPI - host-testable like the 3-D
    builder; the caller owns gmsh.initialize()/finalize().

    Same architecture as _gmsh_build_model:
      - TWO mesh components meeting at the fan LINE (gmsh y = fan_z)
        WITHOUT sharing it (two coincident boundary copies): an interior
        velocity-Dirichlet line on continuous elements leaks flux through
        a divergence sheet exactly as the 3-D plane did, so the split-mesh
        trick is kept in 2-D.
      - Every solid/porous box that PROPERLY straddles y = H/2
        (_midplane_hit) is projected to its x-z footprint rectangle and
        cut from / fragmented into whichever side of the fan line it sits
        on - the identical CSG + fragment parent->child classification.
        Boxes that do not reach mid-height DO NOT EXIST in this domain (a
        component absent at that height is absent from the slice); 3-D
        overlap validation guarantees in-plane footprints never overlap.
      - Same physical tags: dim-2 groups for open/porous regions, dim-1
        groups for front / fan / outlet / wall boundary lines (cut solid
        outlines land in walls, giving the cards their no-slip edges).
    """
    import gmsh

    occ = gmsh.model.occ
    W, H, L = geo["dims"]
    fz = geo["fan_z"]
    lc_bulk = min(2.2 * lc, 0.035)

    def add_rect(b):       # x-z footprint of a 3-D box (gmsh y = chassis z)
        return occ.addRectangle(b[0], b[2], 0.0, b[3] - b[0], b[5] - b[2])

    solids = [(n, b) for n, b in geo["solids"] if _midplane_hit(b, H)]
    porous = []
    if geo["drives"] and _midplane_hit(geo["drives"][1], H):
        porous.append(("drives", geo["drives"][1]))
    for _n, b, _k, _c in geo["cpus"]:
        if _midplane_hit(b, H):
            porous.append(("cpus", b))
    for z in geo["extra_porous"]:
        if _midplane_hit(z["box"], H):
            porous.append((z["tag"], z["box"]))

    zone_vols = {key: [] for key, _b in porous}
    open_vols = []
    for z0, z1 in ((0.0, fz), (fz, L)):        # the two mesh components
        side = [(2, occ.addRectangle(0.0, z0, 0.0, W, z1 - z0))]
        cut = [(2, add_rect(b)) for _n, b in solids
               if z0 - GEOM_TOL <= b[2] and b[5] <= z1 + GEOM_TOL]
        if cut:
            side, _ = occ.cut(side, cut)
        tools = [(key, add_rect(b)) for key, b in porous
                 if z0 - GEOM_TOL <= b[2] and b[5] <= z1 + GEOM_TOL]
        if not tools:
            open_vols.extend(tag for _d, tag in side)
            continue
        _out, out_map = occ.fragment(side, [(2, t) for _k, t in tools])
        side_children = set()
        for children in out_map[:len(side)]:
            side_children.update(tag for _d, tag in children)
        claimed = set()
        for (key, _t), children in zip(tools, out_map[len(side):]):
            for _d, vtag in children:
                if vtag not in side_children:
                    raise RuntimeError(
                        f"porous footprint piece {vtag} fell outside the "
                        "fluid region - overlap validation should have "
                        "caught this config")
                if vtag not in claimed:
                    zone_vols[key].append(vtag)
                    claimed.add(vtag)
        open_vols.extend(t for t in sorted(side_children)
                         if t not in claimed)
    occ.synchronize()

    gmsh.model.addPhysicalGroup(2, open_vols, VOL_OPEN, name="open_air")
    if zone_vols.get("drives"):
        gmsh.model.addPhysicalGroup(2, zone_vols["drives"], VOL_DRIVES,
                                    name="drives")
    if zone_vols.get("cpus"):
        gmsh.model.addPhysicalGroup(2, zone_vols["cpus"], VOL_CPUS,
                                    name="cpus")
    for z in geo["extra_porous"]:
        if zone_vols.get(z["tag"]):
            gmsh.model.addPhysicalGroup(2, zone_vols[z["tag"]], z["tag"],
                                        name=z["name"])

    front_s, fan_s, outlet_s, wall_s = [], [], [], []
    for dim, tag in gmsh.model.getEntities(1):
        surfs_up, _ = gmsh.model.getAdjacencies(dim, tag)
        if len(surfs_up) != 1:
            continue              # interior open/porous interface line
        c = occ.getCenterOfMass(dim, tag)
        if abs(c[1]) < GEOM_TOL:               # gmsh y == chassis z
            front_s.append(tag)
        elif abs(c[1] - L) < GEOM_TOL:
            outlet_s.append(tag)
        elif abs(c[1] - fz) < GEOM_TOL:
            fan_s.append(tag)
        else:
            wall_s.append(tag)    # side walls + cut solid outlines
    gmsh.model.addPhysicalGroup(1, front_s, SURF_FRONT, name="front")
    gmsh.model.addPhysicalGroup(1, fan_s, SURF_FAN, name="fan_wall")
    gmsh.model.addPhysicalGroup(1, outlet_s, SURF_OUTLET, name="outlet")
    gmsh.model.addPhysicalGroup(1, wall_s, SURF_WALLS, name="walls")

    # same graded size bands as the 3-D builder; gmsh Y is chassis z here
    comp_z = ([b[2] for _n, b in solids]
              + [b[2] for key, b in porous if key != "drives"])
    comp_z0 = min(comp_z) if comp_z else fz
    front_z0 = geo["drives"][1][2] if geo["drives"] else 0.0
    f_fine = gmsh.model.mesh.field.add("Box")
    for k, v in (("VIn", lc), ("VOut", lc_bulk),
                 ("XMin", -0.01), ("XMax", W + 0.01),
                 ("YMin", max(fz, comp_z0 - 0.03)), ("YMax", L - 0.02),
                 ("ZMin", -0.01), ("ZMax", 0.01)):
        gmsh.model.mesh.field.setNumber(f_fine, k, v)
    f_med = gmsh.model.mesh.field.add("Box")
    for k, v in (("VIn", 1.3 * lc), ("VOut", lc_bulk),
                 ("XMin", -0.01), ("XMax", W + 0.01),
                 ("YMin", max(0.0, front_z0 - 0.02)),
                 ("YMax", min(L, fz + 0.03)),
                 ("ZMin", -0.01), ("ZMax", 0.01)):
        gmsh.model.mesh.field.setNumber(f_med, k, v)
    f_min = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(f_min, "FieldsList", [f_fine, f_med])
    gmsh.model.mesh.field.setAsBackgroundMesh(f_min)
    gmsh.option.setNumber("Mesh.MeshSizeMax", lc_bulk)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.model.mesh.generate(2)


def worker_build_mesh(geo, comm, lc, engine="3d"):
    """gmsh parametric mesh -> distributed dolfinx mesh (rank 0 builds via
    _gmsh_build_model / _gmsh_build_model_2d, which hold the geometry and
    meshing logic). engine "2d" meshes the mid-height x-z plane with
    gdim=2; the default "3d" path is untouched."""
    import gmsh
    from dolfinx.io import gmsh as gio

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    try:
        if comm.rank == 0:
            if engine == "2d":
                _gmsh_build_model_2d(geo, lc)
            else:
                _gmsh_build_model(geo, lc)
        mesh_data = gio.model_to_mesh(gmsh.model, comm, 0,
                                      gdim=2 if engine == "2d" else 3)
    finally:
        gmsh.finalize()
    return mesh_data.mesh, mesh_data.cell_tags, mesh_data.facet_tags


def eval_at_points_parallel(msh, fun, pts, ncomp):
    """Point evaluation that works on a distributed mesh: each rank fills the
    points it owns, rank 0 receives the combined array (others get None).
    Works for BOTH engines: dolfinx always takes (n, 3) query points, so on
    the 2-D planar mesh callers pass [x, z, 0] with the third column zero
    (mesh coordinate 1 is the chassis flow direction z) and ncomp=2 for
    the velocity."""
    from dolfinx import geometry

    local = np.full((len(pts), ncomp), np.nan)
    tree = geometry.bb_tree(msh, msh.topology.dim)
    cand = geometry.compute_collisions_points(tree, pts)
    coll = geometry.compute_colliding_cells(msh, cand, pts)
    for i in range(len(pts)):
        cells = coll.links(i)
        if len(cells) > 0:
            local[i] = fun.eval(pts[i], cells[0])
    gathered = msh.comm.gather(local, root=0)
    if msh.comm.rank != 0:
        return None
    out = gathered[0]
    for arr in gathered[1:]:
        take = np.isnan(out[:, 0]) & ~np.isnan(arr[:, 0])
        out[take] = arr[take]
    return out


def _viz_field_file(vdir, field):
    """Relative path of the dataset file that actually CARRIES a field's
    array inside a dolfinx VTKFile export directory - the manifest names it
    explicitly so the host viewer never has to guess.

    dolfinx's VTKFile writes a PVD-style collection index named
    '<field>.vtu' (misleading extension - it carries NO data) plus numbered
    .pvtu piece sets; when write_mesh() was also called the FIRST numbered
    set is mesh-only (vtkGhostType & friends, no field array) and the LAST
    one is the write_function() output that holds the array - so the
    highest-numbered set is returned. Falls back to plain numbered .vtu
    pieces for a serial writer that emits no .pvtu. Returns None when
    nothing matches (the reader then skips that field)."""
    try:
        names = sorted(n for n in os.listdir(vdir)
                       if n.startswith(field) and n.endswith(".pvtu"))
        if not names:
            names = sorted(n for n in os.listdir(vdir)
                           if n.startswith(field) and n.endswith(".vtu")
                           and n != field + ".vtu")
        if not names:
            return None
        return os.path.relpath(os.path.join(vdir, names[-1]))
    except OSError:
        return None


def _viz_geometry(geo):
    """Labelled component boxes for the host viewer's 3-D annotations.

    zones*.pvtu carries only numeric cell TAGS - no names - so a viewer
    reading it cannot know which cells are "CPU 1" and which are "RAM".
    Rather than have it guess, the manifest ships the geometry directly:
    chassis extents, the fan plane, and every component box carrying the
    SAME label the ASCII renderer uses. One source, so the terminal view
    and the pop-out window can never disagree about what the hardware is
    called or where it sits. Pure data - no solver types cross the wire."""
    W, H, L = geo["dims"]
    labels = geo.get("labels") or {}

    def entry(name, box, kind):
        return {"name": name,
                "label": labels.get(name) or _block_label(name),
                "box": [float(v) for v in box], "kind": kind}

    comps = []
    if geo.get("drives"):
        dname, dbox = geo["drives"][0], geo["drives"][1]
        d = entry(dname, dbox, "porous")
        d["label"] = "DRIVES"      # matches the ASCII canvas, which
        comps.append(d)            # hardcodes "[ DRIVES ]" for the cage
    for cname, cbox, _K, _C2 in geo.get("cpus", []):
        comps.append(entry(cname, cbox, "porous"))
    for sname, sbox in geo.get("solids", []):
        comps.append(entry(sname, sbox, "solid"))
    for z in geo.get("extra_porous", []):
        comps.append(entry(z["name"], z["box"], "porous"))
    return {
        "dims": [float(W), float(H), float(L)],
        "fan_z": float(geo["fan_z"]),
        "components": comps,
        "fan_marks": [{"name": m["name"],
                       "box": [float(v) for v in m["box"]],
                       "rpm": m.get("rpm"), "size_mm": m.get("size_mm")}
                      for m in geo.get("fan_marks", [])],
    }


def worker_main(args):
    """--worker mode: parametric mesh + transient IPCS solve + streaming."""
    from mpi4py import MPI
    from petsc4py import PETSc
    import basix.ufl
    import ufl
    from dolfinx import default_scalar_type, fem
    from dolfinx.fem import petsc as fp

    comm = MPI.COMM_WORLD
    rank = comm.rank
    t_wall = time.time()

    # rank 0 reads (and possibly auto-writes) the config; others receive it
    cfg = comm.bcast(load_config(args["config"]) if rank == 0 else None, root=0)
    server_cfg = cfg["servers"][args["profile"]]
    fan_cfg = resolve_fan(cfg, args)
    geo = build_geometry(server_cfg)
    W, H, L = geo["dims"]
    area = W * H

    # --- engine select: "3d" full chassis volume (default, unchanged) or
    # "2d" TRUE planar solve on the mid-height x-z plane (y = H/2)
    engine = str(args.get("engine") or "3d").lower()
    if engine not in ("2d", "3d"):
        raise SystemExit('--engine must be "2d" or "3d"')
    two_d = engine == "2d"

    # --- fan duty: fraction of rated RPM (1.0 = rated = legacy behaviour)
    try:
        fan_duty = float(args.get("fan_duty") or 1.0)
    except (TypeError, ValueError):
        raise SystemExit("--fan-duty must be a number "
                         "(fraction of rated RPM)")
    if not (FAN_DUTY_MIN <= fan_duty <= FAN_DUTY_MAX):
        raise SystemExit(f"--fan-duty must be within [{FAN_DUTY_MIN:g}, "
                         f"{FAN_DUTY_MAX:g}] (fraction of rated RPM)")

    # --- acoustic ceiling (worker args key "dba_target", CLI --dba-target):
    # target COMBINED free-field dBA for the whole fan wall (all
    # fan_count fans, +10*log10(N)). Absent/empty = off = byte-identical
    # legacy behaviour. When set, solve_duty_for_dba inverts the affinity
    # noise law into a duty ceiling that caps fan_duty BEFORE the
    # operating point: the QUIETER of the ceiling and the explicit
    # --fan-duty wins, so a noise limit is never silently exceeded.
    dba_target_raw = args.get("dba_target")
    dba_target = None
    dba_info = None
    fan_duty_requested = fan_duty
    if dba_target_raw not in (None, ""):
        try:
            dba_target = float(dba_target_raw)
        except (TypeError, ValueError):
            raise SystemExit("--dba-target must be a number (combined "
                             "free-field dBA for all fans)")
        eff_duty, dba_info = apply_dba_ceiling(
            fan_duty, dba_target, fan_cfg.get("max_dBA"),
            server_cfg["fan_count"])
        if not dba_info["solvable"]:
            raise SystemExit("--dba-target needs a fan with a rated "
                             "max_dBA - the custom fan carries none, so "
                             "an acoustic target cannot be solved for it")
        fan_duty = eff_duty

    # --- mid-run viz export cadence: every N steps, 0 = off (default: the
    # host viewer is optional and the legacy single final export stands)
    try:
        viz_every = max(0, int(args.get("viz_every") or 0))
    except (TypeError, ValueError):
        raise SystemExit("--viz-every must be an integer step count")

    # --- energy equation gate (worker args key "thermal", default OFF):
    # everything thermal below is guarded by this flag, so a run without
    # it is byte-identical to the pre-thermal solver
    thermal = thermal_enabled(args)
    t_inlet = float((server_cfg.get("requirements") or {})
                    .get("inlet_temp_c", 22.0))

    # fan operating point ESTIMATE -> inlet BC level (the meshed CFD
    # impedance is the truth; this just sets the plane velocity). The
    # math - impedance estimate, quadratic-curve intersection AND the
    # affinity-law duty scaling - lives in the pure, host-testable
    # fan_operating_point(); this is its only production call site.
    fan_op = fan_operating_point(server_cfg, fan_cfg, geo, duty=fan_duty)
    q_op = fan_op["q_op"]
    fan_vz = fan_op["fan_vz"]

    mesh_level = args.get("mesh") or "coarse"
    lc = mesh_level_lc(server_cfg, mesh_level)   # enforces MESH_MM_FLOOR
    n_est = est_cells(geo, lc, engine=engine)

    sim_time = float(args["sim_time"])
    dt = float(args.get("dt") or SIM_DT)
    n_steps = max(1, int(round(sim_time / dt)))

    sock = None
    if args.get("callback_port") and rank == 0:
        for attempt in range(5):
            try:
                sock = socket.create_connection(
                    ("127.0.0.1", int(args["callback_port"])), timeout=10)
                break
            except OSError:
                time.sleep(0.5 * (attempt + 1))
        if sock is None:
            print(" [worker] could not reach launcher - continuing headless")

    def send(header, arrays=None):
        if sock is None:
            return
        try:
            payload = b""
            if arrays:
                buf = io.BytesIO()
                np.savez_compressed(buf, **arrays)
                payload = buf.getvalue()
            h = json.dumps(header).encode()
            sock.sendall(struct.pack(">I", len(h)) + h
                         + struct.pack(">I", len(payload)) + payload)
        except OSError:
            pass                      # UI died; the solve carries on

    if rank == 0:
        print(f" [worker] profile={args['profile']} fan={fan_cfg['display']} "
              f"ranks={comm.size} dt={dt}s steps={n_steps}")
        print(f" [worker] mesh {mesh_desc(mesh_level, lc)} "
              f"(lc={lc * 1000:g} mm, ~{n_est:,.0f} elements estimated)")
        print(f" [worker] fan operating estimate: {q_op * M3S_TO_CFM:.1f} CFM "
              f"-> plane velocity {fan_vz:.2f} m/s")
        if two_d:
            print(" [worker] engine=2d: TRUE planar solve on the mid-height "
                  f"x-z slice (y = H/2 = {500.0 * H:g} mm), "
                  "triangles + P2^2/P1")
        if fan_duty != 1.0:
            print(f" [worker] fan duty {100.0 * fan_duty:g}% of rated RPM "
                  f"(affinity: Qmax x{fan_duty:g}, "
                  f"dPmax x{fan_duty ** 2:g})")
        if dba_info is not None:
            line = (f" [worker] dBA target {dba_target:g} dBA combined "
                    f"({server_cfg['fan_count']} fans): duty ceiling "
                    f"{100.0 * dba_info['duty_cap']:.1f}% of rated")
            if dba_info["binding"]:
                line += (f" - CAPS requested "
                         f"{100.0 * fan_duty_requested:g}%")
            if not dba_info["achievable"]:
                line += (f" - UNACHIEVABLE even at minimum duty "
                         f"({100.0 * FAN_DUTY_MIN:g}%)")
            print(line)
        if viz_every:
            print(f" [worker] mid-run viz export every {viz_every} steps -> "
                  f"{VIZ_DIR_PREFIX}NNNNNN/ + {OUT_VIZ_MANIFEST}")
        if thermal:
            print(f" [worker] thermal: energy equation ON - "
                  f"{float(server_cfg.get('heat_load', 0.0)):g} W system "
                  f"load, inlet {t_inlet:g} degC")

    msh, cell_tags, facet_tags = worker_build_mesh(geo, comm, lc,
                                                   engine=engine)
    n_cells = msh.topology.index_map(msh.topology.dim).size_global
    fdim = msh.topology.dim - 1
    msh.topology.create_connectivity(fdim, msh.topology.dim)

    V = fem.functionspace(msh, basix.ufl.element(
        "Lagrange", msh.basix_cell(), 2, shape=(2 if two_d else 3,)))
    Q = fem.functionspace(msh, basix.ufl.element(
        "Lagrange", msh.basix_cell(), 1))
    u, v = ufl.TrialFunction(V), ufl.TestFunction(V)
    p, q = ufl.TrialFunction(Q), ufl.TestFunction(Q)
    u_n, u_s = fem.Function(V), fem.Function(V)
    p_n, phi = fem.Function(Q), fem.Function(Q)

    rho_c = fem.Constant(msh, default_scalar_type(RHO_AIR))
    nu_c = fem.Constant(msh, default_scalar_type(NU_EFFECTIVE))
    dt_c = fem.Constant(msh, default_scalar_type(dt))
    mu_c = fem.Constant(msh, default_scalar_type(RHO_AIR * NU_AIR))

    dx = ufl.Measure("dx", domain=msh, subdomain_data=cell_tags)
    ds = ufl.Measure("ds", domain=msh, subdomain_data=facet_tags)
    n_f = ufl.FacetNormal(msh)
    umag_n = ufl.sqrt(ufl.dot(u_n, u_n) + 1e-8)

    # step 1: tentative velocity (semi-implicit convection, implicit porous
    # with lagged |u_n| - drag time scale 2/(C2|u|) ~ 3 ms << dt)
    a1 = ((rho_c / dt_c) * ufl.dot(u, v) * dx
          + rho_c * ufl.dot(ufl.dot(u_n, ufl.nabla_grad(u)), v) * dx
          + rho_c * nu_c * ufl.inner(ufl.grad(u), ufl.grad(v)) * dx
          - 0.5 * rho_c * ufl.min_value(ufl.dot(u_n, n_f), 0.0)
          * ufl.dot(u, v) * ds(SURF_FRONT))
    if geo["drives"]:
        d = geo["drives"]
        a1 += (mu_c / d[2] + 0.5 * rho_c * d[3] * umag_n) * ufl.dot(u, v) \
            * dx(VOL_DRIVES)
    if geo["cpus"]:
        kc, cc = geo["cpus"][0][2], geo["cpus"][0][3]
        a1 += (mu_c / kc + 0.5 * rho_c * cc * umag_n) * ufl.dot(u, v) \
            * dx(VOL_CPUS)
    for z in geo["extra_porous"]:     # custom impedance zones, own tag each
        if two_d and not _midplane_hit(z["box"], H):
            continue                  # zone absent from the mid-height plane
        a1 += (mu_c / z["K"] + 0.5 * rho_c * z["C2"] * umag_n) \
            * ufl.dot(u, v) * dx(z["tag"])
    L1 = ((rho_c / dt_c) * ufl.dot(u_n, v) * dx
          - ufl.dot(ufl.grad(p_n), v) * dx)

    # PSU internal-fan momentum sources ("fan_momentum" porous zones, e.g.
    # the R640's 40 mm high-static PSU fans): a uniform +z body force
    # f = dp_fan / L_z [N/m^3] over the zone's cells - a streamline through
    # the whole zone picks up the full fan pressure rise dp_fan (the
    # standard fan-zone momentum-source model; dp_fan precomputed by
    # build_geometry via psu_fan_dp). Stable at the default dt = 0.1 s
    # because (1) the force is a bounded CONSTANT on the RHS, (2) the
    # zone's own quadratic porous drag sits IMPLICITLY in a1 (lagged |u_n|,
    # drag time scale 2/(C2|u|) ~ ms << dt), so drag equilibrates the force
    # within a step instead of feeding back, and (3) psu_ramp_c ramps the
    # force over RAMP_STEPS in lockstep with the fan-wall BC - no impulsive
    # start. Zones without the flag carry no term: every other profile's
    # solve is byte-identical.
    psu_ramp_c = fem.Constant(msh, default_scalar_type(0.0))
    psu_fan_srcs = []
    for z in geo["extra_porous"]:
        if not z.get("fan_force"):
            continue
        if two_d and not _midplane_hit(z["box"], H):
            continue              # zone absent from the mid-height plane
        f_vec = (ufl.as_vector((0.0, z["fan_force"])) if two_d
                 else ufl.as_vector((0.0, 0.0, z["fan_force"])))
        L1 += psu_ramp_c * ufl.dot(f_vec, v) * dx(z["tag"])
        psu_fan_srcs.append(z)
    if rank == 0:
        for z in psu_fan_srcs:
            print(f" [worker] PSU fan momentum source: {z['name']} "
                  f"{z['fan_rpm']} rpm / {z['fan_size_mm']} mm -> "
                  f"{z['fan_dp_pa']:.0f} Pa over "
                  f"{z['box'][5] - z['box'][2]:.3f} m (+z)")

    # step 2: pressure increment; phi = 0 on the OPEN boundaries only (front
    # + outlet: each mesh component's pressure anchor); natural Neumann on
    # walls AND the fan plane (velocity-Dirichlet boundary)
    a2 = ufl.dot(ufl.grad(p), ufl.grad(q)) * dx
    L2 = -(rho_c / dt_c) * ufl.div(u_s) * q * dx

    # step 3: velocity correction (pure mass solve - no porous drag here)
    a3 = rho_c * ufl.dot(u, v) * dx
    L3 = (rho_c * ufl.dot(u_s, v) * dx
          - dt_c * ufl.dot(ufl.grad(phi), v) * dx)

    a1f, L1f = fem.form(a1), fem.form(L1)
    a2f, L2f = fem.form(a2), fem.form(L2)
    a3f, L3f = fem.form(a3), fem.form(L3)

    # BCs: fan plane (both coincident copies) + no-slip walls (listed after
    # the fan BC so shared rim dofs resolve to no-slip)
    u_fan = fem.Function(V)
    if two_d:      # planar mesh coordinate 1 IS the flow direction z
        u_fan.interpolate(lambda x: np.vstack((0 * x[0],
                                               np.full_like(x[0], fan_vz))))
    else:
        u_fan.interpolate(lambda x: np.vstack((0 * x[0], 0 * x[0],
                                               np.full_like(x[0], fan_vz))))
    fan_base = u_fan.x.array.copy()
    bc_fan = fem.dirichletbc(u_fan, fem.locate_dofs_topological(
        V, fdim, facet_tags.find(SURF_FAN)))
    bc_wall = fem.dirichletbc(fem.Function(V), fem.locate_dofs_topological(
        V, fdim, facet_tags.find(SURF_WALLS)))
    bcs_u = [bc_fan, bc_wall]
    open_facets = np.concatenate([facet_tags.find(SURF_FRONT),
                                  facet_tags.find(SURF_OUTLET)])
    bc_p = fem.dirichletbc(default_scalar_type(0.0),
                           fem.locate_dofs_topological(Q, fdim, open_facets),
                           Q)
    bcs_p = [bc_p]

    A1 = fp.create_matrix(a1f)
    A2 = fp.assemble_matrix(a2f, bcs=bcs_p); A2.assemble()
    A3 = fp.assemble_matrix(a3f); A3.assemble()
    b1, b2, b3 = fp.create_vector(V), fp.create_vector(Q), fp.create_vector(V)

    s1 = PETSc.KSP().create(comm); s1.setOperators(A1)
    s1.setType("gmres"); s1.getPC().setType("bjacobi")
    s1.setTolerances(rtol=KSP_RTOL)
    s2 = PETSc.KSP().create(comm); s2.setOperators(A2)
    s2.setType("cg"); s2.getPC().setType("hypre")
    s2.getPC().setHYPREType("boomeramg"); s2.setTolerances(rtol=1e-10)
    s3 = PETSc.KSP().create(comm); s3.setOperators(A3)
    s3.setType("cg"); s3.getPC().setType("jacobi"); s3.setTolerances(rtol=1e-10)

    flux_front = fem.form(ufl.dot(u_n, n_f) * ds(SURF_FRONT))
    flux_out = fem.form(ufl.dot(u_n, n_f) * ds(SURF_OUTLET))

    # ---- energy equation (worker args key "thermal"; OFF = pre-thermal
    # behaviour, bit for bit) --------------------------------------------------
    # Advection-diffusion of temperature T [degC] in the SAME backward-
    # Euler / semi-implicit style as the momentum step: the advecting
    # velocity is the lagged u_n, so the extra solve is unconditionally
    # stable at the existing dt (convective CFL >> 1, accuracy-limited
    # like the momentum scheme). Diffusivity is the eddy value
    # NU_EFFECTIVE / PRANDTL_TURB (Reynolds analogy on the constant eddy
    # viscosity). One extra linear solve per step on the P1 scalar space:
    # GMRES + block-Jacobi(ILU) - the advection term makes the operator
    # nonsymmetric (the CG+AMG / CG+Jacobi combos of steps 2/3 need
    # symmetry) and the rho*cp/dt mass shift keeps it well conditioned,
    # exactly the reasoning behind the step-1 solver choice.
    if thermal:
        from dolfinx import mesh as dmesh

        plan = thermal_heat_plan(server_cfg, geo, engine=engine)
        T_n = fem.Function(Q)
        T_n.name = "temperature"
        T_n.x.array[:] = t_inlet
        rcp_c = fem.Constant(msh, default_scalar_type(RHO_AIR * CP_AIR))
        kap_c = fem.Constant(msh, default_scalar_type(
            RHO_AIR * CP_AIR * NU_EFFECTIVE / PRANDTL_TURB))
        one_c = fem.Constant(msh, default_scalar_type(1.0))

        # -- volumetric heat sources [W/m^3] on DG0 (one value per cell).
        # Watts come from thermal_heat_plan; every region's density is
        # normalised by its volume AS MEASURED ON THIS MESH, so the
        # injected power is exact and a region that captured no cells is
        # DETECTED (its share folds into the distributed remainder rather
        # than silently vanishing into a void). The 2-D engine measures
        # areas; x chassis height H converts them to the volumes the
        # W/m^3 densities need (the planar solve is per unit height).
        Q0T = fem.functionspace(msh, ("DG", 0))
        q_src = fem.Function(Q0T)
        q_src.name = "heat_source"
        h_norm = H if two_d else 1.0
        src_note = []

        def region_volume(tag):
            v = fem.assemble_scalar(fem.form(one_c * dx(tag)))
            return comm.allreduce(v, op=MPI.SUM) * h_norm

        def add_region(tag, dens):
            for c in cell_tags.find(tag):
                q_src.x.array[Q0T.dofmap.cell_dofs(c)[0]] += dens

        rest_w = plan["rest_w"]
        for tag, zname, watts in plan["explicit"]:
            vol = region_volume(tag)
            if vol > 1e-12:
                add_region(tag, watts / vol)
                src_note.append(f"{zname} {watts:g}W")
            else:
                rest_w += watts       # no cells at this mesh/slice
        # GPU DECISION - the wizard's GPUs are meshed as SOLID PCIe cards,
        # and solids are CUT OUT of the fluid domain by the CSG step:
        # there are NO CELLS inside a card, so a volumetric source there
        # is impossible. The card's watts are released instead into the
        # 1 cm washing SHELL of fluid cells around it (GPU_SHELL_M - the
        # same shell the GPU airflow telemetry samples): physically that
        # is the air the card sheds its heat to, and at this resolution
        # it is equivalent to a surface heat flux on the card faces while
        # needing no new facet groups on either engine's mesh. The shell
        # volume is measured on the mesh (below), so the injected watts
        # are exact - and a shell that captured no cells folds its share
        # into the distributed remainder instead of reporting success.
        for cname, b, watts in plan["cards"]:
            shell = fem.Function(Q0T)
            g = GPU_SHELL_M
            if two_d:      # planar coords: x[0] = chassis x, x[1] = z
                shell.interpolate(lambda x, b=b: (
                    (x[0] >= b[0] - g) & (x[0] <= b[3] + g)
                    & (x[1] >= b[2] - g) & (x[1] <= b[5] + g))
                    .astype(default_scalar_type))
            else:
                shell.interpolate(lambda x, b=b: (
                    (x[0] >= b[0] - g) & (x[0] <= b[3] + g)
                    & (x[1] >= b[1] - g) & (x[1] <= b[4] + g)
                    & (x[2] >= b[2] - g) & (x[2] <= b[5] + g))
                    .astype(default_scalar_type))
            vol = comm.allreduce(
                fem.assemble_scalar(fem.form(shell * dx)),
                op=MPI.SUM) * h_norm
            if vol > 1e-12:
                q_src.x.array[:] += (watts / vol) * shell.x.array
                src_note.append(f"{geo['labels'].get(cname, cname)} shell "
                                f"{watts:g}W")
            else:
                rest_w += watts
        if rest_w > 0.0:
            union_vol = sum(region_volume(t)
                            for t, _n in plan["implicit_tags"])
            if union_vol > 1e-12:
                for tag, _n in plan["implicit_tags"]:
                    add_region(tag, rest_w / union_vol)
                src_note.append(f"heat regions {rest_w:g}W")
            else:              # no heat-carrying component in the domain:
                vol_all = comm.allreduce(
                    fem.assemble_scalar(fem.form(one_c * dx)),
                    op=MPI.SUM) * h_norm
                q_src.x.array[:] += rest_w / vol_all
                src_note.append(f"whole domain {rest_w:g}W")
        q_src.x.scatter_forward()
        q_injected = comm.allreduce(
            fem.assemble_scalar(fem.form(q_src * dx)),
            op=MPI.SUM) * h_norm

        # -- weak forms: same trial/test family as the pressure space
        Tt, wT = ufl.TrialFunction(Q), ufl.TestFunction(Q)
        aT = ((rcp_c / dt_c) * Tt * wT * dx
              + rcp_c * ufl.dot(u_n, ufl.grad(Tt)) * wT * dx
              + kap_c * ufl.dot(ufl.grad(Tt), ufl.grad(wT)) * dx
              # backflow guard at the outlet, same device as the step-1
              # front-face term: vanishes when the outlet is pure outflow
              - rcp_c * ufl.min_value(ufl.dot(u_n, n_f), 0.0)
              * Tt * wT * ds(SURF_OUTLET))
        LT = ((rcp_c / dt_c) * T_n * wT * dx + q_src * wT * dx)
        aTf, LTf = fem.form(aT), fem.form(LT)

        # -- BCs: inlet air enters at ambient (Dirichlet on the front
        # face); solid walls adiabatic (natural Neumann - 1U/2U sheet
        # metal moves negligible heat next to the advective flux);
        # outlet: natural outflow.
        bcT_in = fem.dirichletbc(
            default_scalar_type(t_inlet),
            fem.locate_dofs_topological(Q, fdim,
                                        facet_tags.find(SURF_FRONT)), Q)
        # Fan-plane MIXING condition: the mesh deliberately SPLITS at the
        # fan wall (two coincident boundary copies), so nothing advects
        # across it by itself. Server fans are strong mixers, so each
        # step the DOWNSTREAM copy is held at the mean temperature of the
        # UPSTREAM copy (uniform fan velocity -> the mass-flow-weighted
        # mean IS the area mean), carrying the bulk enthalpy across the
        # split. Not optional: the switch profiles heat the FRONT
        # component (ASIC before the fans) and would otherwise exhaust at
        # ambient. The copies share the SURF_FAN tag; they are told apart
        # by which side of the fan plane their cell sits on.
        fan_facets = facet_tags.find(SURF_FAN)
        f2c = msh.topology.connectivity(fdim, msh.topology.dim)
        fan_cells = np.array([f2c.links(f)[0] for f in fan_facets],
                             dtype=np.int32)
        if len(fan_cells):
            mids = dmesh.compute_midpoints(msh, msh.topology.dim,
                                           fan_cells)
            fan_side_z = mids[:, 1 if two_d else 2]
        else:
            fan_side_z = np.zeros(0)
        up_facets = np.sort(fan_facets[fan_side_z < geo["fan_z"]])
        down_facets = fan_facets[fan_side_z > geo["fan_z"]]
        T_mix_c = fem.Constant(msh, default_scalar_type(t_inlet))
        bcT_fan = fem.dirichletbc(
            T_mix_c, fem.locate_dofs_topological(Q, fdim, down_facets), Q)
        bcs_T = [bcT_in, bcT_fan]
        ft_up = dmesh.meshtags(msh, fdim, up_facets,
                               np.ones(len(up_facets), dtype=np.int32))
        ds_up = ufl.Measure("ds", domain=msh, subdomain_data=ft_up)
        T_up_form = fem.form(T_n * ds_up(1))
        area_up = comm.allreduce(
            fem.assemble_scalar(fem.form(one_c * ds_up(1))), op=MPI.SUM)

        AT = fp.create_matrix(aTf)
        bT = fp.create_vector(Q)
        sT = PETSc.KSP().create(comm)
        sT.setOperators(AT)
        sT.setType("gmres")
        sT.getPC().setType("bjacobi")
        sT.setTolerances(rtol=KSP_RTOL)

        # mass-flow-weighted exhaust temperature, weighted by the OUTGOING
        # flux only (max(u.n, 0)): the exhaust is the temperature of air
        # actually LEAVING. Weighting by the net flux degenerates when
        # transient backflow nearly cancels the outflow - the 2-D engine's
        # bluff-body shedding does exactly that and produced sub-inlet
        # nonsense means. Numerator and denominator share the same units
        # (2-D line fluxes), so the ratio is engine-agnostic un-H-scaled.
        un_pos = ufl.max_value(ufl.dot(u_n, n_f), 0.0)
        T_flux_form = fem.form(T_n * un_pos * ds(SURF_OUTLET))
        q_pos_form = fem.form(un_pos * ds(SURF_OUTLET))
        t_out_c = t_inlet

        if rank == 0:
            print(f" [worker] thermal sources: {'; '.join(src_note)} "
                  f"(injected {q_injected:.1f} W of "
                  f"{plan['total_w']:g} W config load)")
            intended = (sum(w for _t, _n, w in plan["explicit"])
                        + sum(w for _n, _b, w in plan["cards"])
                        + plan["rest_w"])
            if intended > 0 and abs(q_injected - intended) > 5e-3 * intended:
                print(" [worker] WARNING: injected thermal power deviates "
                      f"from the intended {intended:g} W - a source "
                      "region resolved to (almost) no cells")

    # dashboard sampling grids (identical on every rank; combined on rank 0)
    ncols = int(args.get("cols") or MAIN_COLS_MAX)
    xs = (np.arange(MAIN_ROWS) + 0.5) * W / MAIN_ROWS
    zs = (np.arange(ncols) + 0.5) * L / ncols
    # volumetric stack: the same x-z grid at every VOL_SLICE_FRACS height.
    # ORDER MATTERS: y-outer, then x, then z - the flat eval result then
    # reshapes to (n_slices, MAIN_ROWS, ncols), and the 0.50 slice IS the
    # classic mid-plane the 2-D dashboard, ASCII view and report consume
    # (one eval call feeds both, bit-identical).
    vol_ys = [f * H for f in VOL_SLICE_FRACS]
    mini_rows = int(np.clip(round(H / 0.01), 4, 9))
    xs_m = (np.arange(MINI_COLS) + 0.5) * W / MINI_COLS
    ys_m = (np.arange(mini_rows) + 0.5) * H / mini_rows
    if two_d:
        # the planar mesh IS the mid-height slice: one x-z grid (query
        # points padded to (n, 3) with a zero third column), and the
        # front/rear minis become x-LINES near the two open ends
        pts_vol = np.array([[x, z, 0.0] for x in xs for z in zs])
        pts_front = np.array([[x, 0.012, 0.0] for x in xs_m])
        pts_rear = np.array([[x, L - 0.015, 0.0] for x in xs_m])
    else:
        pts_vol = np.array([[x, y, z] for y in vol_ys for x in xs
                            for z in zs])
        pts_front = np.array([[x, y, 0.012] for y in ys_m for x in xs_m])
        pts_rear = np.array([[x, y, L - 0.015] for y in ys_m for x in xs_m])

    def sample_and_send(step, t_sim, qf, qo, t_out=None):
        # FROZEN WIRE PROTOCOL: both engines emit the SAME header keys and
        # the SAME array names/shapes on the same sampling grid - the
        # launcher dashboard must not need to know which engine ran. The
        # 2-D branch replicates its single plane across the volumetric
        # stack (the mid slice IS the whole 2-D solution) and tiles the
        # front/rear lines to the mini-pane shape; it only ADDS the
        # "engine" header key. The thermal run ADDS "t_out_c" (solved
        # mass-flow-weighted outlet temperature, degC) - never present
        # when the energy equation is off, so the off-path frames stay
        # byte-identical.
        extra = {} if t_out is None else {"t_out_c": float(t_out)}
        if two_d:
            vals = eval_at_points_parallel(msh, u_n, pts_vol, 2)
            pv = eval_at_points_parallel(msh, p_n, pts_vol, 1)
            fr = eval_at_points_parallel(msh, u_n, pts_front, 2)
            re = eval_at_points_parallel(msh, u_n, pts_rear, 2)
            if rank == 0:
                nsl = len(VOL_SLICE_FRACS)
                u_mid = vals.reshape(MAIN_ROWS, ncols, 2)
                sp_mid = np.linalg.norm(vals, axis=1).reshape(MAIN_ROWS,
                                                              ncols)
                p_mid = pv[:, 0].reshape(MAIN_ROWS, ncols)
                sp_vol = np.repeat(sp_mid[None, :, :], nsl, axis=0)
                p_vol = np.repeat(p_mid[None, :, :], nsl, axis=0)
                spf = np.tile(np.linalg.norm(fr, axis=1), (mini_rows, 1))
                spr = np.tile(np.linalg.norm(re, axis=1), (mini_rows, 1))
                send({"type": "frame", "t": t_sim, "step": step,
                      "steps": n_steps, "q_front": qf, "q_out": qo,
                      "fan_vz": fan_vz, "cells": int(n_cells),
                      "vol_y": vol_ys, "engine": "2d", **extra},
                     {"ux": u_mid[:, :, 0], "uz": u_mid[:, :, 1],
                      "speed": sp_mid, "press": p_mid,
                      "vol_speed": sp_vol, "vol_press": p_vol,
                      "front": spf, "rear": spr})
            return
        vals = eval_at_points_parallel(msh, u_n, pts_vol, 3)
        pv = eval_at_points_parallel(msh, p_n, pts_vol, 1)
        fr = eval_at_points_parallel(msh, u_n, pts_front, 3)
        re = eval_at_points_parallel(msh, u_n, pts_rear, 3)
        if rank == 0:
            nsl = len(VOL_SLICE_FRACS)
            u_vol = vals.reshape(nsl, MAIN_ROWS, ncols, 3)
            sp_vol = np.linalg.norm(vals, axis=1).reshape(nsl, MAIN_ROWS,
                                                          ncols)
            p_vol = pv[:, 0].reshape(nsl, MAIN_ROWS, ncols)
            mid = VOL_MID_IDX
            spf = np.linalg.norm(fr, axis=1).reshape(mini_rows, MINI_COLS)
            spr = np.linalg.norm(re, axis=1).reshape(mini_rows, MINI_COLS)
            send({"type": "frame", "t": t_sim, "step": step,
                  "steps": n_steps, "q_front": qf, "q_out": qo,
                  "fan_vz": fan_vz, "cells": int(n_cells),
                  "vol_y": vol_ys, **extra},
                 {"ux": u_vol[mid, :, :, 0], "uz": u_vol[mid, :, :, 2],
                  "speed": sp_vol[mid], "press": p_vol[mid],
                  "vol_speed": sp_vol, "vol_press": p_vol,
                  "front": spf, "rear": spr})

    # ---- periodic mid-run viz export (host-side viewer) ---------------------
    # Atomicity contract: every export lands in its OWN fresh
    # viz_step_NNNNNN/ directory; the manifest is replaced atomically
    # (os.replace of a temp file) ONLY AFTER a barrier confirms every
    # rank's datasets are on disk. A reader that polls OUT_VIZ_MANIFEST
    # therefore never sees a torn file - it learns of a directory only
    # once the directory is complete. The two newest directories are kept
    # (a slow reader may still hold the previous one); older ones go.
    # Like send(): a failed export must never kill the solve.
    viz_state = {}
    viz_dirs = []

    def _write_viz_manifest(man):
        tmp = OUT_VIZ_MANIFEST + ".tmp"
        with open(tmp, "w") as f:
            json.dump(man, f, indent=1)
        os.replace(tmp, OUT_VIZ_MANIFEST)     # atomic on one filesystem

    def _viz_fields(vdir):
        # name the EXACT dataset file carrying each array: dolfinx's
        # '<name>.vtu' is a collection INDEX and, when write_mesh() was
        # used, the first numbered piece set is mesh-only - the reader
        # must not have to guess (see _viz_field_file). "temperature"
        # joins the list ONLY when the energy equation ran: listing it
        # unconditionally would resurrect stale temperature files from an
        # earlier thermal run in the same directory.
        pairs = [("velocity", "velocity"), ("pressure", "pressure"),
                 ("zones", "zone")]
        if thermal:
            pairs.append(("temperature", "temperature"))
        fields = {}
        for base, arr in pairs:
            path = _viz_field_file(vdir, base)
            if path:
                fields[base] = {"file": path, "array": arr}
        return fields

    def viz_export(step, t_sim):
        from dolfinx.io import VTKFile
        if not viz_state:                    # lazy one-time P1/DG0 setup
            Vv = fem.functionspace(msh, ("Lagrange", 1,
                                         (2 if two_d else 3,)))
            fu = fem.Function(Vv); fu.name = "velocity"
            Q0v = fem.functionspace(msh, ("DG", 0))
            zz = fem.Function(Q0v); zz.name = "zone"
            for c, val in zip(cell_tags.indices, cell_tags.values):
                zz.x.array[Q0v.dofmap.cell_dofs(c)[0]] = val
            p_n.name = "pressure"
            viz_state.update(u=fu, zone=zz)
        viz_state["u"].interpolate(u_n)
        vdir = f"{VIZ_DIR_PREFIX}{step:06d}"
        ok = True
        if rank == 0:
            try:
                os.makedirs(vdir, exist_ok=True)
            except OSError:
                ok = False
        if not comm.bcast(ok, root=0):
            return                # cannot export; the solve carries on
        try:
            # collective writes: a rank-LOCAL failure here cannot be
            # handled without risking a collective mismatch, so (like the
            # final export) only uniform failures are survivable - the
            # realistic ones (permissions, missing dir) were caught above
            bases = [("velocity", viz_state["u"]),
                     ("pressure", p_n),
                     ("zones", viz_state["zone"])]
            if thermal:
                bases.append(("temperature", T_n))
            for base, func in bases:
                with VTKFile(comm, os.path.join(vdir, base + ".vtu"),
                             "w") as vtk:
                    vtk.write_function(func)
            comm.Barrier()        # every rank's pieces on disk BEFORE the
        except Exception:         # manifest may name them
            return
        if rank != 0:
            return
        try:
            _write_viz_manifest({
                "type": "viz", "step": step, "steps": n_steps, "t": t_sim,
                "dt": dt, "engine": engine, "cells": int(n_cells),
                "dir": vdir, "fields": _viz_fields(vdir),
                "profile": args.get("profile"),
                "geometry": _viz_geometry(geo), "done": False})
            viz_dirs.append(vdir)
            while len(viz_dirs) > VIZ_KEEP_DIRS:
                shutil.rmtree(viz_dirs.pop(0), ignore_errors=True)
        except OSError:
            pass                  # viewer starves; the solve carries on

    if rank == 0:
        print(f" [worker] mesh ready: {n_cells:,} elements "
              f"(estimate was ~{n_est:,.0f}; {time.time() - t_wall:.0f}s); "
              "time-stepping...")
        if two_d:
            print(f" [worker] engine=2d cell count: {n_cells:,} triangles")

    for step in range(n_steps):
        scale = min(1.0, (step + 1) / RAMP_STEPS)   # fan startup ramp
        u_fan.x.array[:] = fan_base * scale
        u_fan.x.scatter_forward()
        psu_ramp_c.value = scale      # PSU fan sources ramp with the wall

        A1.zeroEntries()
        fp.assemble_matrix(A1, a1f, bcs=bcs_u)
        A1.assemble()
        with b1.localForm() as lf:
            lf.set(0)
        fp.assemble_vector(b1, L1f)
        fp.apply_lifting(b1, [a1f], [bcs_u])
        b1.ghostUpdate(addv=PETSc.InsertMode.ADD_VALUES,
                       mode=PETSc.ScatterMode.REVERSE)
        fp.set_bc(b1, bcs_u)
        s1.solve(b1, u_s.x.petsc_vec)
        u_s.x.scatter_forward()

        with b2.localForm() as lf:
            lf.set(0)
        fp.assemble_vector(b2, L2f)
        fp.apply_lifting(b2, [a2f], [bcs_p])
        b2.ghostUpdate(addv=PETSc.InsertMode.ADD_VALUES,
                       mode=PETSc.ScatterMode.REVERSE)
        fp.set_bc(b2, bcs_p)
        s2.solve(b2, phi.x.petsc_vec)
        phi.x.scatter_forward()

        with b3.localForm() as lf:
            lf.set(0)
        fp.assemble_vector(b3, L3f)
        b3.ghostUpdate(addv=PETSc.InsertMode.ADD_VALUES,
                       mode=PETSc.ScatterMode.REVERSE)
        s3.solve(b3, u_n.x.petsc_vec)
        u_n.x.scatter_forward()

        p_n.x.array[:] = p_n.x.array + phi.x.array
        p_n.x.scatter_forward()

        if thermal:
            # step 4: energy equation. Mixing plane first: the downstream
            # fan copy rides on the upstream mean of the PREVIOUS step's
            # field (a one-dt transport lag through the fan - physical).
            # The matrix is reassembled every step like A1: the advecting
            # u_n changed.
            T_mix_c.value = (comm.allreduce(
                fem.assemble_scalar(T_up_form), op=MPI.SUM) / area_up
                if area_up > 1e-12 else t_inlet)
            AT.zeroEntries()
            fp.assemble_matrix(AT, aTf, bcs=bcs_T)
            AT.assemble()
            with bT.localForm() as lf:
                lf.set(0)
            fp.assemble_vector(bT, LTf)
            fp.apply_lifting(bT, [aTf], [bcs_T])
            bT.ghostUpdate(addv=PETSc.InsertMode.ADD_VALUES,
                           mode=PETSc.ScatterMode.REVERSE)
            fp.set_bc(bT, bcs_T)
            sT.solve(bT, T_n.x.petsc_vec)
            T_n.x.scatter_forward()

        t_sim = (step + 1) * dt
        qf = comm.allreduce(fem.assemble_scalar(flux_front), op=MPI.SUM)
        qo = comm.allreduce(fem.assemble_scalar(flux_out), op=MPI.SUM)
        if thermal:
            # exhaust temperature: outflow-weighted outlet mean (see the
            # form definitions above for why NOT the net flux)
            tq = comm.allreduce(fem.assemble_scalar(T_flux_form),
                                op=MPI.SUM)
            qp = comm.allreduce(fem.assemble_scalar(q_pos_form),
                                op=MPI.SUM)
            t_out_c = tq / qp if qp > 1e-12 else t_inlet
        if two_d:
            # planar line flux [m^2/s] -> volumetric equivalent [m^3/s]
            # (uniform-over-height assumption) so the streamed q_front/
            # q_out keep the 3-D engine's units
            qf *= H
            qo *= H
        if (step + 1) % SEND_EVERY == 0 or step == n_steps - 1:
            sample_and_send(step + 1, t_sim, qf, qo,
                            t_out_c if thermal else None)
        if viz_every and (step + 1) % viz_every == 0:
            viz_export(step + 1, t_sim)
        if rank == 0 and sock is None and (step + 1) % 10 == 0:
            t_note = f" T_out={t_out_c:.1f}C" if thermal else ""
            print(f" [worker] t={t_sim:6.1f}s step {step+1}/{n_steps} "
                  f"q_out={qo:.4f} m^3/s ({qo * M3S_TO_CFM:.1f} CFM)"
                  f"{t_note}")

    # ---- final summary: requirements inputs ---------------------------------
    p_arr = p_n.x.array
    n_own = (p_n.function_space.dofmap.index_map.size_local
             * p_n.function_space.dofmap.index_map_bs)
    coords = p_n.function_space.tabulate_dof_coordinates()
    local_min_i = int(np.argmin(p_arr[:n_own])) if n_own else 0
    local_min = (float(p_arr[local_min_i]) if n_own else np.inf,
                 coords[local_min_i].tolist() if n_own else [0, 0, 0])
    all_mins = comm.gather(local_min, root=0)

    comp_z = ([b[5] for _n, b in geo["solids"]]
              + [b[5] for _n, b, _k, _c in geo["cpus"]]
              + [z["box"][5] for z in geo["extra_porous"]])
    comp_z1 = max(comp_z) if comp_z else geo["fan_z"]
    rz0 = min(comp_z1 + 0.01, L - 0.03)      # clear of block end faces
    dxs = np.linspace(0.02, W - 0.02, 18)
    dys = np.linspace(0.15 * H, 0.85 * H, 6)
    dzs = np.linspace(rz0, L - 0.005, 10)
    if two_d:      # the domain IS y = H/2: the deadzone scan is the x-z grid
        pts_dz = np.array([[x, z, 0.0] for x in dxs for z in dzs])
        dz_vals = eval_at_points_parallel(msh, u_n, pts_dz, 2)
    else:
        pts_dz = np.array([[x, y, z] for x in dxs for y in dys
                           for z in dzs])
        dz_vals = eval_at_points_parallel(msh, u_n, pts_dz, 3)

    # component airflow PROXIES for the IT telemetry checks:
    #   CPU    = mean |u| sampled INSIDE each porous heatsink block
    #   GPU    = mean |u| in a 1 cm shell AROUND each solid PCIe card (the
    #            cards are flow-blockers here; this is the air washing them)
    #   Optics = mean |u| in a DEFINED rear I/O slab [L-0.03, L-0.005] (no
    #            transceiver cage is meshed - the region is the definition)
    # NOTE: every rank must run these loops (collective gathers inside).
    def box_mean_speed(b, inflate=0.0, n=(8, 5, 6)):
        x0 = max(0.005, b[0] - inflate)
        x1 = min(W - 0.005, b[3] + inflate)
        if two_d:
            # project the query box to its x-z rectangle in the plane
            pts = np.array([[x, z, 0.0]
                            for x in np.linspace(x0, x1, n[0])
                            for z in np.linspace(b[2], b[5], n[2])])
            vals = eval_at_points_parallel(msh, u_n, pts, 2)
        else:
            y0 = max(0.003, b[1] - inflate)
            y1 = min(H - 0.003, b[4] + inflate)
            pts = np.array([[x, y, z]
                            for x in np.linspace(x0, x1, n[0])
                            for y in np.linspace(y0, y1, n[1])
                            for z in np.linspace(b[2], b[5], n[2])])
            vals = eval_at_points_parallel(msh, u_n, pts, 3)
        if rank != 0:
            return None
        sp = np.linalg.norm(vals, axis=1)
        sp = sp[~np.isnan(sp)]
        return float(sp.mean()) if sp.size else 0.0

    # cpu/gpu kinds measure INSIDE porous blocks; solid cards (pcie_*, or
    # custom solids with a telemetry kind) measure the 1 cm washing shell
    components = []
    labels = geo["labels"]
    for name, b, _k, _c in geo["cpus"]:
        if two_d and not _midplane_hit(b, H):
            continue              # component absent from the 2-D slice
        components.append(("cpu", labels.get(name) or _block_label(name),
                           box_mean_speed(b)))
    for name, b in geo["solids"]:
        kind = geo["solid_telem"].get(name)
        # cards only ("pcie_card_N"): riser cages are chassis mechanics,
        # not GPUs - they must not join the GPU airflow threshold check
        if kind is None and name.startswith("pcie_card_"):
            kind = "gpu"
        if kind:
            if two_d and not _midplane_hit(b, H):
                continue          # component absent from the 2-D slice
            components.append((kind, labels.get(name)
                               or f"GPU {name.rsplit('_', 1)[-1]}",
                               box_mean_speed(b, inflate=0.01)))
    have_optics = False
    for z in geo["extra_porous"]:
        if z["telemetry"]:
            if two_d and not _midplane_hit(z["box"], H):
                continue          # zone absent from the 2-D slice
            components.append((z["telemetry"],
                               labels.get(z["name"], z["name"]),
                               box_mean_speed(z["box"])))
            have_optics = have_optics or z["telemetry"] == "optics"
    if not have_optics:
        components.append(("optics", "Optics (config zone)"
                           if geo["optics_custom"]
                           else "Optics (rear I/O region)",
                           box_mean_speed(geo["optics_box"])))

    qf = comm.allreduce(fem.assemble_scalar(flux_front), op=MPI.SUM)
    qo = comm.allreduce(fem.assemble_scalar(flux_out), op=MPI.SUM)
    if thermal:
        # final exhaust temperature from the SOLVED field (outflow-
        # weighted outlet mean) + the field maximum; collectives, so
        # every rank participates
        tq_fin = comm.allreduce(fem.assemble_scalar(T_flux_form),
                                op=MPI.SUM)
        qp_fin = comm.allreduce(fem.assemble_scalar(q_pos_form),
                                op=MPI.SUM)
        t_exhaust_c = tq_fin / qp_fin if qp_fin > 1e-12 else t_inlet
        n_own_t = Q.dofmap.index_map.size_local * Q.dofmap.index_map_bs
        t_max_c = comm.allreduce(
            float(T_n.x.array[:n_own_t].max()) if n_own_t else -1e30,
            op=MPI.MAX)
    if two_d:
        qf *= H               # line flux -> volumetric equivalent, as in
        qo *= H               # the time loop (uniform-over-height)

    if rank == 0:
        p_min, p_at = min(all_mins, key=lambda mp: mp[0])
        speeds = np.linalg.norm(dz_vals, axis=1)
        ok = ~np.isnan(speeds)
        j = int(np.argmin(speeds[ok]))
        dz_at = pts_dz[ok][j]
        if two_d:
            # planar coordinates are (x, z, 0); report the PHYSICAL
            # location (x, H/2, z) so the summary semantics match 3-D
            p_at = [p_at[0], 0.5 * H, p_at[1]]
            dz_at = [dz_at[0], 0.5 * H, dz_at[1]]
        summary = {
            "type": "summary",
            "engine": engine,
            "q_out": qo, "q_front": qf, "q_fan": fan_vz * area,
            "fan_vz": fan_vz, "fan_op_cfm": q_op * M3S_TO_CFM,
            # fan-duty / affinity telemetry (all PER-FAN, duty-scaled;
            # None where the fan config has no rating - custom fans):
            # the acoustics/power table combines them with fan_count
            "fan_duty": fan_op["duty"],
            "fan_rpm_rated": fan_op["rpm_rated"],
            "fan_rpm_scaled": fan_op["rpm"],
            "fan_cfm_scaled": fan_op["cfm_max"],
            "fan_mmh2o_scaled": fan_op["mmh2o_max"],
            "fan_watts_scaled": fan_op["watts"],
            "fan_dba_scaled": fan_op["dba"],
            "p_min": p_min, "p_min_at": [round(c, 3) for c in p_at],
            "dz_min": float(speeds[ok][j]),
            "dz_min_at": [round(float(c), 3) for c in dz_at],
            "dz_mean": float(speeds[ok].mean()),
            "components": components,
            "mesh_level": mesh_level,
            "mesh_desc": mesh_desc(mesh_level, lc), "n_cells": int(n_cells),
            "ranks": comm.size,
            "sim_time": sim_time, "dt": dt,
            "wall_time": time.time() - t_wall,
        }
        if psu_fan_srcs:
            # ADDED summary key (frozen wire protocol allows additions):
            # present only when momentum-source zones actually solved, so
            # every unflagged profile's summary stays byte-identical
            summary["psu_fan_sources"] = [
                {"name": z["name"], "rpm": z["fan_rpm"],
                 "size_mm": z["fan_size_mm"],
                 "dp_pa": round(z["fan_dp_pa"], 1)} for z in psu_fan_srcs]
        if thermal:
            # ADDED keys (never present with thermal off, so the frozen
            # off-wire stays byte-identical): the SOLVED exhaust
            # temperature next to the bulk balance estimate the launcher
            # table always showed, so the two can be compared directly.
            summary["thermal"] = True
            summary["t_inlet_c"] = float(t_inlet)
            summary["t_exhaust_c"] = float(t_exhaust_c)
            summary["t_exhaust_bulk_c"] = float(
                t_inlet + plan["total_w"]
                / (RHO_AIR * max(qo, 1e-9) * CP_AIR))
            summary["t_max_c"] = float(t_max_c)
            summary["heat_load_w"] = float(plan["total_w"])
            summary["q_src_w"] = float(q_injected)
        if dba_info is not None:
            # ADDED keys (present only when --dba-target is set, so the
            # frozen default wire stays byte-identical). dba_combined is
            # the SAME free-field estimate the acoustics table prints:
            # per-fan dBA at the EFFECTIVE duty + 10*log10(fan_count).
            combined = combined_noise_dba(fan_op["dba"],
                                          server_cfg["fan_count"])
            summary["dba_target"] = float(dba_target)
            summary["dba_target_duty_cap"] = dba_info["duty_cap"]
            summary["fan_duty_requested"] = float(fan_duty_requested)
            summary["dba_target_binding"] = bool(dba_info["binding"])
            summary["dba_target_achievable"] = bool(
                dba_info["achievable"])
            summary["dba_combined"] = combined
            summary["dba_target_met"] = bool(
                combined is not None
                and combined <= float(dba_target) + 1e-9)
            # noise-limit / thermal interaction: does holding the fans to
            # the acoustic ceiling overheat the exhaust? Judged against
            # requirements.outlet_temp_max_c using the SOLVED outlet mean
            # when the energy equation ran, else the bulk balance
            # estimate over the computed flow (basis says which, so the
            # launcher can phrase the warning honestly).
            t_limit = float((server_cfg.get("requirements") or {})
                            .get("outlet_temp_max_c", 35.0))
            if thermal:
                t_check, basis = float(t_exhaust_c), "solved"
            else:
                t_check = float(
                    t_inlet + float(server_cfg.get("heat_load", 0.0))
                    / (RHO_AIR * max(qo, 1e-9) * CP_AIR))
                basis = "bulk"
            summary["outlet_temp_max_c"] = t_limit
            summary["dba_exhaust_c"] = t_check
            summary["dba_exhaust_basis"] = basis
            summary["dba_overheat"] = bool(t_check > t_limit)
        send(summary)
        print(f" [worker] done: t={sim_time:.1f}s in "
              f"{summary['wall_time']:.0f}s wall, q_out={qo:.4f} m^3/s "
              f"({qo * M3S_TO_CFM:.1f} CFM), p_min={p_min:.0f} Pa")
        if thermal:
            print(f" [worker] thermal: exhaust {t_exhaust_c:.1f} degC "
                  f"solved vs {summary['t_exhaust_bulk_c']:.1f} degC bulk "
                  f"estimate (inlet {t_inlet:g} degC, "
                  f"T_max {t_max_c:.1f} degC)")

    # ---- VTU export (always, even if the UI died) ---------------------------
    from dolfinx.io import VTKFile
    V1 = fem.functionspace(msh, ("Lagrange", 1, (2 if two_d else 3,)))
    u_out = fem.Function(V1); u_out.name = "velocity"; u_out.interpolate(u_n)
    p_n.name = "pressure"
    Q0 = fem.functionspace(msh, ("DG", 0))
    zone = fem.Function(Q0); zone.name = "zone"
    for c, val in zip(cell_tags.indices, cell_tags.values):
        zone.x.array[Q0.dofmap.cell_dofs(c)[0]] = val
    out_fields = [(OUT_VELOCITY, u_out), (OUT_PRESSURE, p_n),
                  (OUT_ZONES, zone)]
    if thermal:
        out_fields.append((OUT_TEMPERATURE, T_n))   # P1 already, like p_n
    for path, func in out_fields:
        with VTKFile(comm, path, "w") as vtk:
            vtk.write_mesh(msh)
            vtk.write_function(func)
    if rank == 0:
        try:
            # final manifest: point the viewer at the FULL final export in
            # the working directory and mark the run done. Same atomic
            # replace; the last viz_step dirs stay for late readers.
            # Written on EVERY run, not just when mid-run export was on:
            # it is how any downstream reader learns which numbered .pvtu
            # actually carries each array (the bare <field>.vtu is a
            # dataless collection index and the first piece set is
            # mesh-only). Gating it on viz_every meant a normal solve with
            # no viewer attached left visualize.py with nothing to read -
            # exactly the case a post-run snapshot tool exists for.
            _write_viz_manifest({
                "type": "viz", "step": n_steps, "steps": n_steps,
                "t": n_steps * dt, "dt": dt, "engine": engine,
                "cells": int(n_cells), "dir": ".",
                "fields": _viz_fields("."),
                "profile": args.get("profile"),
                "geometry": _viz_geometry(geo), "done": True})
        except OSError:
            pass
    if rank == 0:
        print(" [worker] VTU fields written (velocity/pressure/zones"
              + ("/temperature)" if thermal else ")"))
        send({"type": "end"})
        if sock is not None:
            sock.close()


# ==============================================================================
#  LAUNCHER: renderer primitives (no MPI/dolfinx anywhere below this line)
# ==============================================================================

def _status_rgb(t):
    """GREEN = fast/optimal, YELLOW = moderate, RED = stagnant (t=0)."""
    t = float(min(max(t, 0.0), 1.0))
    if t < 0.45:
        f = t / 0.45
        c0, c1 = STATUS_RED, STATUS_YEL
    else:
        f = min((t - 0.45) / 0.45, 1.0)
        c0, c1 = STATUS_YEL, STATUS_GRN
    return tuple(int(round(a + f * (b - a))) for a, b in zip(c0, c1))


def _hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def cfd_colormap(t):
    """24-bit CFD 'velocity heatmap' colour: blue -> cyan -> green -> yellow
    -> red, piecewise-linear between CFD_CMAP_STOPS."""
    t = float(min(max(t, 0.0), 1.0))
    for (t0, c0), (t1, c1) in zip(CFD_CMAP_STOPS, CFD_CMAP_STOPS[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(int(round(a + f * (b - a))) for a, b in zip(c0, c1))
    return CFD_CMAP_STOPS[-1][1]


# 5-row block font covering exactly the letters of the banner word
BANNER_FONT = {
    "A": (" ███ ", "█   █", "█████", "█   █", "█   █"),
    "S": ("█████", "█    ", "█████", "    █", "█████"),
    "C": ("█████", "█    ", "█    ", "█    ", "█████"),
    "I": ("███", " █ ", " █ ", " █ ", "███"),
    "T": ("█████", "  █  ", "  █  ", "  █  ", "  █  "),
    "R": ("████ ", "█   █", "████ ", "█  █ ", "█   █"),
    "E": ("█████", "█    ", "███  ", "█    ", "█████"),
    "M": ("██   ██", "███ ███", "██ █ ██", "██   ██", "██   ██"),
}


def render_banner(console, word="ASCIISTREAM"):
    """Retro boot banner: block letters under a left-to-right 24-bit CFD
    colormap gradient (blue -> cyan -> green -> yellow -> red). Falls back
    to a one-line gradient title on terminals narrower than the art."""
    glyphs = [BANNER_FONT[ch] for ch in word]
    rows = ["  ".join(g[r] for g in glyphs) for r in range(5)]
    width = len(rows[0])
    if console.width < width + 2:
        t = Text()
        for i, ch in enumerate(word):
            t.append(ch, style="bold " + _hex(cfd_colormap(i / (len(word) - 1))))
        console.print(t)
        return
    for row in rows:
        t = Text()
        for c, ch in enumerate(row):
            if ch == " ":
                t.append(" ")
            else:
                t.append(ch, style="bold " + _hex(cfd_colormap(c / (width - 1))))
        console.print(t)


def _flow_char(t):
    for thresh, ch in FLOW_CHARS:
        if t < thresh:
            return ch
    return FLOW_CHARS[-1][1]


def _block_label(name):
    if name.startswith("cpu"):
        return f"CPU {name[3]}"
    if name.startswith("dimm"):
        return "RAM"
    if name.startswith("pcie"):
        return "PCIe"
    return name.upper()


class CharCanvas:
    def __init__(self, nrows, ncols):
        self.nrows, self.ncols = nrows, ncols
        self.ch = [[" "] * ncols for _ in range(nrows)]
        self.st = [[""] * ncols for _ in range(nrows)]
        self.protected = [[False] * ncols for _ in range(nrows)]

    def put(self, r, c, ch, style, protect=False):
        if 0 <= r < self.nrows and 0 <= c < self.ncols:
            self.ch[r][c] = ch
            self.st[r][c] = style
            if protect:
                self.protected[r][c] = True

    def stamp_text(self, r, c0, text, style):
        for k, ch in enumerate(text):
            self.put(r, c0 + k, ch, style, protect=True)

    def render_lines(self):
        lines = []
        for r in range(self.nrows):
            t = Text()
            run_style, run_chars = None, []
            for c in range(self.ncols):
                s = self.st[r][c]
                if s != run_style and run_chars:
                    t.append("".join(run_chars), style=run_style)
                    run_chars = []
                run_style = s
                run_chars.append(self.ch[r][c])
            if run_chars:
                t.append("".join(run_chars), style=run_style)
            lines.append(t)
        return lines


def build_geometry_canvas(geo, nrows, ncols):
    """Static layer: bordered/labelled components, drive cage, fan line."""
    W, H, L = geo["dims"]
    cv = CharCanvas(nrows, ncols)
    border = _hex(COL_BORDER)
    fill_style = _hex(COL_FILL)
    dash = "dim " + _hex(COL_BORDER)

    def zc(z):
        return int(np.clip(round(z / L * (ncols - 1)), 0, ncols - 1))

    def xr(x):
        return int(np.clip(round(x / W * (nrows - 1)), 0, nrows - 1))

    def stamp_box(bounds, name, solid):
        x0, _y0, z0, x1, _y1, z1 = bounds
        r0, r1, c0, c1 = xr(x0), xr(x1), zc(z0), zc(z1)
        for c in range(c0, c1 + 1):
            cv.put(r0, c, "=", border, protect=True)
            cv.put(r1, c, "=", border, protect=True)
        for r in range(r0, r1 + 1):
            cv.put(r, c0, "[", border, protect=True)
            cv.put(r, c1, "]", border, protect=True)
        if solid:
            for r in range(r0 + 1, r1):
                for c in range(c0 + 1, c1):
                    cv.put(r, c, "#", fill_style, protect=True)
        label = geo["labels"].get(name) or _block_label(name)
        rmid = (r0 + r1) // 2
        span = c1 - c0 - 1
        for txt in (f"[ {label} ]", label, label.replace(" ", "")):
            if len(txt) <= span:
                cv.stamp_text(rmid, c0 + 1 + (span - len(txt)) // 2,
                              txt, COL_LABEL)
                return
        txt = label.replace(" ", "")     # tall narrow zones: vertical label
        if r1 - r0 - 1 >= len(txt):
            rt = r0 + 1 + (r1 - r0 - 1 - len(txt)) // 2
            for k, ch in enumerate(txt):
                cv.put(rt + k, (c0 + c1) // 2, ch, COL_LABEL, protect=True)

    if geo["drives"]:
        d = geo["drives"][1]
        r0, r1, c0, c1 = xr(d[0]), xr(d[3]), zc(d[2]), zc(d[5])
        for c in range(c0, c1 + 1):
            cv.put(r0, c, ".", dash, protect=True)
            cv.put(r1, c, ".", dash, protect=True)
        for r in range(r0, r1 + 1):
            cv.put(r, c0, ":", dash, protect=True)
            cv.put(r, c1, ":", dash, protect=True)
        if c1 - c0 > 12:
            cv.stamp_text(r0, c0 + (c1 - c0 - 10) // 2, "[ DRIVES ]",
                          COL_LABEL)

    for name, b, _k, _c in geo["cpus"]:
        stamp_box(b, name, solid=False)
    # impedance zones: bordered, not filled. y-stacked zones project onto
    # the same top-down rectangle - draw the one containing the mid-height
    # slice LAST so its label matches what the particle field shows
    for z in sorted(geo["extra_porous"],
                    key=lambda z: z["box"][1] <= H / 2 <= z["box"][4]):
        stamp_box(z["box"], z["name"], solid=False)
    for name, b in geo["solids"]:
        stamp_box(b, name, solid=True)

    fc = zc(geo["fan_z"])
    for r in range(nrows):
        cv.put(r, fc, "|", "bold " + _hex(COL_FANLN), protect=True)
    fan_lbl = "[ FAN WALL ]"
    cv.stamp_text(1, max(0, min(ncols - len(fan_lbl), fc - len(fan_lbl) // 2)),
                  fan_lbl, "bold " + _hex(COL_FANLN))
    return cv


class ParticleField:
    """Smoke particles advected through the newest streamed field (2-D
    mid-plane streaklines). Fields are hot-swapped as frames arrive."""

    def __init__(self, geo, ncols):
        self.W, self.H, self.L = geo["dims"]
        self.nrows, self.ncols = MAIN_ROWS, ncols
        self.dx = self.W / self.nrows
        self.dz = self.L / self.ncols
        self.ux = np.zeros((self.nrows, ncols))
        self.uz = np.full((self.nrows, ncols), 0.05)
        self.speed = np.full((self.nrows, ncols), 0.05)
        self.vref = 1.0
        self.dt = 0.05
        self.rng = np.random.default_rng(7)
        self.pos = np.zeros((N_PARTICLES, 2))
        self.age = np.zeros(N_PARTICLES, dtype=int)
        for i in range(N_PARTICLES):
            self._respawn(i, warm=True)

    def update_fields(self, ux, uz, speed, vref):
        self.ux, self.uz, self.speed = ux, uz, speed
        self.vref = max(vref, 1e-6)
        v99 = np.nanpercentile(speed, 99)
        self.dt = 0.7 * self.dz / max(v99, 1e-6)

    def _respawn(self, i, warm=False):
        if self.rng.random() < INLET_SPAWN_FRAC:
            x = self.rng.uniform(0.02 * self.W, 0.98 * self.W)
            z = self.rng.uniform(0.002, 0.02 * self.L)
        else:
            x = self.rng.uniform(0.01 * self.W, 0.99 * self.W)
            z = self.rng.uniform(0.01 * self.L, 0.99 * self.L)
        self.pos[i] = (x, z)
        self.age[i] = self.rng.integers(0, PARTICLE_MAX_AGE // 2) if warm else 0

    def cells(self):
        r = np.clip((self.pos[:, 0] / self.dx).astype(int), 0, self.nrows - 1)
        c = np.clip((self.pos[:, 1] / self.dz).astype(int), 0, self.ncols - 1)
        return r, c

    def step(self):
        r, c = self.cells()
        vx = self.ux[r, c]
        vz = self.uz[r, c]
        dead = ~np.isfinite(vx)
        self.pos[:, 0] += np.where(dead, 0.0, vx) * self.dt
        self.pos[:, 1] += np.where(dead, 0.0, vz) * self.dt
        self.age += 1
        out = ((self.pos[:, 0] <= 0.0) | (self.pos[:, 0] >= self.W)
               | (self.pos[:, 1] <= 0.0) | (self.pos[:, 1] >= self.L)
               | dead | (self.age > PARTICLE_MAX_AGE))
        for i in np.flatnonzero(out):
            self._respawn(i)


def build_main_panel(scene):
    cv = scene["canvas"]
    pf = scene["particles"]
    frame = CharCanvas(cv.nrows, cv.ncols)
    frame.ch = [row[:] for row in cv.ch]
    frame.st = [row[:] for row in cv.st]
    r, c = pf.cells()
    spd = pf.speed[r, c]
    for i in range(len(r)):
        ri, ci = int(r[i]), int(c[i])
        if cv.protected[ri][ci] or not np.isfinite(spd[i]):
            continue
        frac = spd[i] / pf.vref
        frame.ch[ri][ci] = _flow_char(frac)
        frame.st[ri][ci] = _hex(_status_rgb(frac))
    st = scene["status"]
    cells = f"  {st['cells'] / 1e3:,.0f}k cells" if st.get("cells") else ""
    # status lives INSIDE the panel: long chassis names make rich crop the
    # panel title, which silently ate the t/step/q_out/cells readout
    status_line = Text(
        f"t={st['t']:.1f}s/{st['t_total']:.0f}s  step {st['step']}"
        f"/{st['steps']}  q_out={st['q_out'] * M3S_TO_CFM:.1f} CFM{cells}",
        style="dim")
    return Panel(Group(status_line, *frame.render_lines()),
                 title=f"TOP-DOWN FLOW - {scene['display_name']}",
                 border_style="cyan", box=box.SQUARE, padding=(0, 1))


def build_mini_panel(speed_xy, vref, title, n_bays=None):
    nrows, ncols = speed_xy.shape
    lines = []
    for r in range(nrows - 1, -1, -1):
        t = Text()
        run_style, run_chars = None, []
        for c in range(ncols):
            v = speed_xy[r, c]
            if np.isfinite(v):
                frac = v / max(vref, 1e-9)
                ch, s = _flow_char(frac), _hex(_status_rgb(frac))
            else:
                ch, s = "#", _hex(COL_FILL)
            if s != run_style and run_chars:
                t.append("".join(run_chars), style=run_style)
                run_chars = []
            run_style = s
            run_chars.append(ch)
        if run_chars:
            t.append("".join(run_chars), style=run_style)
        lines.append(t)
    if n_bays:
        ticks = [" "] * ncols
        for k in range(n_bays + 1):
            ticks[int(round(k * (ncols - 1) / n_bays))] = "|"
        lines.append(Text("".join(ticks), style="dim"))
        lines.append(Text(f"{n_bays} bays", style="dim"))
    return Panel(Group(*lines), title=title, border_style="cyan",
                 box=box.SQUARE, padding=(0, 1))


def build_chassis_iso_panel(speed, geo, vref, max_cols, max_rows,
                            title="3D CHASSIS VIEW (isometric)"):
    """CAD-style isometric projection of the PHYSICAL server chassis.

    Every component box (drive cage, CPU heatsinks, RAM banks, PCIe cards,
    PSUs and the other custom zones) is drawn as an extruded 3-D block with
    the classic ASCII charset ( _  |  \\ staircases), placed by the 2-D
    affine isometric map

        [col]   [2s   0  2s] [x]      z (length) runs along the columns,
        [row] = [ s -hy   0] [y]      x (width) recedes at 45 deg on screen
                             [z]      (+2 cols +1 row - cells are ~2:1 tall,
                                      so slope 1/2 LOOKS like 45 degrees),
                                      y (height) extrudes upward.

    Colour overlays the computed flow: the streamed mid-height |u| field is
    averaged over each block's x-z footprint, normalised by the fan-plane
    velocity and mapped through the CFD colormap (blue = starved -> red =
    full flow); the block's top face is filled with that colour. Solid
    blocks (no interior flow - their footprint samples NaN) use a one-cell
    washing shell instead, matching the telemetry proxy. The fan wall is
    the gold plane; blocks with a config fan_rpm (PSUs) get the gold fan
    marker on their rear face. Painter's algorithm far-x -> near-x, then
    stack bottom -> top, so near/tall blocks overdraw. The height scale is
    clamped to ISO_HGT_MIN..MAX rows so a 6U chassis cannot stretch the
    character grid apart.
    """
    W, H, L = geo["dims"]
    hgt_rows = int(np.clip(round(ISO_HGT_PER_M * H), ISO_HGT_MIN,
                           ISO_HGT_MAX))
    # scale [cells/m] from BOTH budgets: cols span 2s(L+W), rows span
    # hgt_rows + s*W (+ margins); the live pane passes MAIN_ROWS, the
    # post-run/report render can afford more
    s = min((max_cols - 6) / (2.0 * (L + W)),
            (max_rows - hgt_rows - 4) / max(W, 1e-6))
    if s <= 3:
        return Panel(Text("terminal too small for the 3-D chassis view",
                          style="dim"), title=title, border_style="cyan",
                     box=box.SQUARE, padding=(0, 1))
    hy = hgt_rows / max(H, 1e-6)
    r_base = 1 + hgt_rows
    nrows_cv = int(round(r_base + s * W)) + 2
    ncols_cv = int(round(2 * s * (L + W))) + 4
    cv = CharCanvas(nrows_cv, ncols_cv)
    dim = "dim " + _hex(COL_BORDER)
    gold = "bold " + _hex(COL_FANLN)

    def proj(x, y, z):
        """the affine isometric map above (metres -> character cell)"""
        return (int(round(r_base + s * x - hy * y)),
                int(round(1 + 2 * s * z + 2 * s * x)))

    def edge(a, b, style, protect=False):
        """One axis-aligned box edge, rasterized per direction: '_' along
        z, '|' along y, and a doubled '\\' staircase (2 cols per row) for
        the 45-degree x axis - a Bresenham-family stepper specialised to
        the three slopes this projection can produce."""
        (r0, c0), (r1, c1) = a, b
        if r0 == r1:
            for c in range(min(c0, c1), max(c0, c1) + 1):
                cv.put(r0, c, "_", style, protect)
        elif c0 == c1:
            for r in range(min(r0, r1), max(r0, r1) + 1):
                cv.put(r, c0, "|", style, protect)
        else:
            if r1 < r0:
                (r0, c0), (r1, c1) = (r1, c1), (r0, c0)
            ch = "\\" if c1 > c0 else "/"
            stp = 1 if c1 > c0 else -1
            for k in range(r1 - r0 + 1):
                cv.put(r0 + k, c0 + stp * 2 * k, ch, style, protect)
                if k < r1 - r0:
                    cv.put(r0 + k, c0 + stp * (2 * k + 1), ch, style,
                           protect)

    def box_edges(b, style, gold_rear=False):
        """The 9 visible edges of an extruded block for this projection
        (visible faces: top y1, front x1, rear-end z1)."""
        x0, y0, z0, x1, y1, z1 = b
        tA, tB = proj(x0, y1, z0), proj(x0, y1, z1)
        tC, tD = proj(x1, y1, z1), proj(x1, y1, z0)
        bD, bC = proj(x1, y0, z0), proj(x1, y0, z1)
        eB = proj(x0, y0, z1)
        edge(tA, tB, style)                    # top face
        edge(tD, tC, style)
        edge(tA, tD, style)
        rear = gold if gold_rear else style
        edge(tB, tC, rear)
        edge(bD, bC, style)                    # front face (x = x1)
        edge(tD, bD, style)
        edge(tC, bC, rear)
        edge(eB, bC, rear)                     # rear end face (z = z1)
        edge(tB, eB, rear)

    def fill_top(b, colhex):
        """Colour the top-face parallelogram (background paint - the text
        report strips styles and keeps the wireframe)."""
        x0, _y0, z0, x1, y1, z1 = b
        (ra, _ca) = proj(x0, y1, 0)
        (rd, _cd) = proj(x1, y1, 0)
        for r in range(ra + 1, rd):
            f = (r - ra) / max(rd - ra, 1)
            x = x0 + f * (x1 - x0)
            _r0, cl = proj(x, y1, z0)
            _r1, cr = proj(x, y1, z1)
            for c in range(cl + 1, cr):
                cv.put(r, c, " ", "on " + colhex)

    def stamp_label(b, colhex, label):
        """Component name on the top face, AFTER the edges so the strokes
        never mangle it; shrinking fallbacks down to initials+digits (a
        cramped live pane shows 'C1', the wide report render 'CPU 1')."""
        x0, _y0, z0, x1, y1, z1 = b
        (ra, _ca) = proj(x0, y1, 0)
        (rd, _cd) = proj(x1, y1, 0)
        if rd - ra < 2:
            return
        rmid = (ra + rd) // 2
        xm = x0 + (rmid - ra) / (rd - ra) * (x1 - x0)
        _rm, cl = proj(xm, y1, z0)
        _rm2, cr = proj(xm, y1, z1)
        span = cr - cl - 1
        initials = "".join(w[0] for w in label.split() if w)
        for txt in (f" {label} ", label, label.replace(" ", ""), initials):
            if txt and len(txt) <= span:
                cv.stamp_text(rmid, cl + 1 + (span - len(txt)) // 2,
                              txt, "bold bright_white on " + colhex)
                return

    # ---- chassis shell: dim wireframe box (drawn first, blocks overdraw)
    box_edges((0.0, 0.0, 0.0, W, H, L), dim)
    edge(proj(0.0, 0.0, 0.0), proj(0.0, 0.0, L), dim)   # far floor edge
    edge(proj(0.0, H, 0.0), proj(0.0, 0.0, 0.0), dim)   # far-left vertical

    # ---- fan wall: gold plane at z = fan_z
    fz = geo["fan_z"]
    edge(proj(0.0, H, fz), proj(W, H, fz), gold)
    edge(proj(0.0, 0.0, fz), proj(W, 0.0, fz), "dim " + _hex(COL_FANLN))
    edge(proj(0.0, H, fz), proj(0.0, 0.0, fz), gold)
    edge(proj(W, H, fz), proj(W, 0.0, fz), gold)
    fr, fc = proj(0.0, H, fz)
    cv.stamp_text(max(0, fr - 1), max(0, fc - 4), "FAN WALL", gold)

    # ---- component blocks, coloured by the local sampled air speed -------
    n_rf, n_cf = speed.shape

    def block_color(b, solid):
        x0, _y0, z0, x1, _y1, z1 = b
        pad = 1 if solid else 0     # solids sample the washing shell
        r0f = max(0, int(np.floor(x0 / W * n_rf)) - pad)
        r1f = min(n_rf, int(np.ceil(x1 / W * n_rf)) + pad)
        c0f = max(0, int(np.floor(z0 / L * n_cf)) - pad)
        c1f = min(n_cf, int(np.ceil(z1 / L * n_cf)) + pad)
        blk = speed[r0f:max(r1f, r0f + 1), c0f:max(c1f, c0f + 1)]
        if blk.size and np.isfinite(blk).any():
            t = float(np.nanmean(blk)) / max(vref, 1e-9)
            return _hex(cfd_colormap(min(max(t, 0.0), 1.0)))
        return _hex(COL_FILL)

    blocks = []
    if geo["drives"]:
        blocks.append((geo["drives"][1], "drive_array", False))
    for name, b, _k, _c in geo["cpus"]:
        blocks.append((b, name, False))
    for z in geo["extra_porous"]:
        blocks.append((z["box"], z["name"], False))
    for name, b in geo["solids"]:
        blocks.append((b, name, True))
    fan_named = {m["name"] for m in geo["fan_marks"]}
    # painter's order: far x first, then bottom of a y-stack first (note:
    # y-stacked zones share the mid-height footprint, so they share colour)
    blocks.sort(key=lambda t: (t[0][0] + t[0][3], t[0][1]))
    for b, name, solid in blocks:
        colhex = block_color(b, solid)
        fill_top(b, colhex)
        box_edges(b, colhex, gold_rear=(name in fan_named))
        stamp_label(b, colhex, geo["labels"].get(name) or _block_label(name))

    cv.stamp_text(nrows_cv - 1, 1, "FRONT", dim)
    cv.stamp_text(nrows_cv - 1, max(0, ncols_cv - 6), "REAR", dim)

    bar = Text("  |u|  ", style="bold")
    for k in range(24):
        bar.append("█", style=_hex(cfd_colormap(k / 23)))
    bar.append(f"  0 .. {vref:.1f} m/s (block mean)", style="dim")
    if geo["fan_marks"]:
        m = geo["fan_marks"][0]
        bar.append("   gold", style=gold)
        bar.append(f" = fan wall + PSU fan ({m['size_mm']}mm/"
                   f"{m['rpm'] // 1000}k rpm)", style="dim")
    return Panel(Group(*cv.render_lines(), bar), title=title,
                 border_style="cyan", box=box.SQUARE, padding=(0, 1))


def _braille_rows(values, width, rows, vmax):
    """History series -> `rows` Text lines of braille cells, newest sample
    at the right edge: 2 samples per character column, 4 dot-levels per
    character row (so a 2-row graph resolves 8 levels, btop-style). Each
    character is coloured green -> yellow -> red by its own load; empty
    cells keep the blank braille glyph so columns never collapse."""
    n = 2 * width
    vals = [max(0.0, float(v)) for v in list(values)[-n:]]
    vals = [0.0] * (n - len(vals)) + vals
    span = max(float(vmax), 1e-9)
    hmax = 4 * rows
    # ceil: any nonzero sample shows at least one dot
    lv = [min(hmax, int(np.ceil(min(v / span, 1.0) * hmax))) if v > 0 else 0
          for v in vals]
    out = []
    for r in range(rows):                        # top row first
        base = 4 * (rows - 1 - r)
        t = Text()
        for c in range(width):
            l0 = min(max(lv[2 * c] - base, 0), 4)
            l1 = min(max(lv[2 * c + 1] - base, 0), 4)
            ch = chr(0x2800 + BRAILLE_L[l0] + BRAILLE_R[l1])
            if l0 or l1:
                load = min(max(vals[2 * c], vals[2 * c + 1]) / span, 1.0)
                t.append(ch, style=_hex(_status_rgb(1.0 - load)))
            else:
                t.append(ch, style="dim")
        out.append(t)
    return out


def _braille_meter(frac, width, tail=""):
    """btop-style meter at braille half-cell resolution: the filled bar is
    colour-graded along its length (green base -> red tip), the unfilled
    channel is a dark solid so the meter's full extent stays visible."""
    frac = min(max(float(frac), 0.0), 1.0)
    cols = int(round(frac * 2 * width))
    t = Text()
    for c in range(width):
        fill = min(max(cols - 2 * c, 0), 2)
        if fill:
            ch = chr(0x2800 + BRAILLE_L[4] + (BRAILLE_R[4] if fill == 2
                                              else 0))
            t.append(ch, style=_hex(_status_rgb(1.0 - (c + 0.5) / width)))
        else:
            t.append(chr(0x28FF), style=_hex((58, 58, 64)))
    t.append(tail, style="dim")
    return t


def _box_mem_total():
    """MemTotal [bytes] - machine CAPACITY, not usage: the one machine-wide
    fact the telemetry strip is allowed (it only scales the RAM meter).
    /proc/meminfo first; psutil's total field (still capacity) as the
    non-/proc fallback; 0 = unknown, the meter is then dropped."""
    try:
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    if HAVE_PSUTIL:
        try:
            return int(psutil.virtual_memory().total)
        except Exception:
            pass
    return 0


class WorkerTelemetry:
    """Privacy-first telemetry for the CFD WORKERS strip.

    Scope is EXACTLY the spawned solver tree: the mpiexec root plus its
    recursive children (PRRTE/hydra daemons + the python ranks). Nothing
    global is read - no system-wide psutil.cpu_percent (/proc/stat), no
    virtual_memory() usage numbers; other tenants of the box are invisible
    to this widget by construction.

      RAM  = sum of per-PID USS (unique set size: pages shared with nobody
             else, so MPI shared-memory segments and the N copies of the
             interpreter image are not double-counted). Reads
             /proc/<pid>/smaps_rollup via memory_full_info; falls back to
             RSS where USS is unsupported. Refreshed every TELEM_USS_SEC
             (slower than CPU on purpose - ~40 ranks of smaps reads must
             never stall the render loop).
      CPU  = sum of per-PID busy %, normalised by the AFFINITY POOL: the
             union of hardware threads the tree is allowed to run on
             (taskset/cpuset aware via Process.cpu_affinity), so 36
             saturated ranks pinned to 36 of 72 threads read ~100 %, not
             ~50 %. The summed raw % is reported alongside.

    Process handles are cached per PID and only NEW pids get the priming
    cpu_percent(None) call - psutil's busy-time delta lives on the handle,
    so recreating handles every walk would zero every other sample and
    sawtooth the graphs."""

    def __init__(self, root_pid):
        self._root = root_pid
        self._procs = {}                     # pid -> cached psutil.Process
        self._walked = -1e9
        self._uss_at = -1e9
        self._uss = 0
        self._ever = False                   # tree observed alive once

    def _walk(self, now):
        if now - self._walked < TELEM_TREE_SEC:
            return
        self._walked = now
        found = {}
        try:
            root = psutil.Process(self._root)
            for p in [root] + root.children(recursive=True):
                found[p.pid] = self._procs.get(p.pid, p)
        except psutil.Error:
            found = {}
        for pid, p in found.items():
            if pid not in self._procs:
                try:
                    p.cpu_percent(None)      # prime the new arrival
                except psutil.Error:
                    pass
        self._procs = found

    def sample(self):
        """One strip sample: dict(alive, ever, n_procs, cpu_raw [summed %],
        pool [thread count], cpu_pool [0..1 of the pool], uss [bytes])."""
        now = time.monotonic()
        self._walk(now)
        cpu_raw, pool, alive = 0.0, set(), 0
        want_uss = now - self._uss_at >= TELEM_USS_SEC
        uss = 0
        for p in list(self._procs.values()):
            try:
                with p.oneshot():
                    cpu_raw += p.cpu_percent(None)
                    try:
                        pool.update(p.cpu_affinity())
                    except (psutil.Error, AttributeError, OSError):
                        pass
                    if want_uss:
                        try:
                            uss += p.memory_full_info().uss
                        except (psutil.Error, AttributeError):
                            uss += p.memory_info().rss
                alive += 1
            except psutil.Error:
                self._procs.pop(p.pid, None)
        if want_uss and alive:
            self._uss_at, self._uss = now, uss
        n_pool = len(pool) or (os.cpu_count() or 1)
        self._ever = self._ever or alive > 0
        return {"alive": alive > 0, "ever": self._ever, "n_procs": alive,
                "cpu_raw": cpu_raw, "pool": n_pool,
                "cpu_pool": cpu_raw / (100.0 * n_pool), "uss": self._uss}


def build_sys_panel(sample, cpu_hist, ram_hist, mem_total, width):
    """CFD WORKERS strip: full-width btop-style braille graphs + meters for
    the solver tree only (WorkerTelemetry). CPU graphs on a fixed 0..100 %
    -of-pool scale; the RAM graph autoscales to its own peak while the RAM
    meter shows the fraction of the box's MemTotal (capacity constant)."""
    if not HAVE_PSUTIL:
        return Panel(Text("psutil not available (pip install psutil) - "
                          "worker telemetry disabled", style="dim"),
                     title="CFD WORKERS", border_style="cyan",
                     box=box.SQUARE, padding=(0, 1))
    if sample is None or not sample["ever"]:
        return Panel(Text("waiting for the worker pool...", style="dim"),
                     title="CFD WORKERS", border_style="cyan",
                     box=box.SQUARE, padding=(0, 1))

    half = max(24, (width - 10) // 2)
    gw = max(10, half - 2)                   # graph width [chars]
    mw = max(10, half - 26)                  # meter width [chars]

    cpu_t = min(max(sample["cpu_pool"], 0.0), 1.0)
    head_c = Text("CPU ", style="bold")
    head_c.append(f"{100.0 * sample['cpu_pool']:5.1f}%",
                  style="bold " + _hex(_status_rgb(1.0 - cpu_t)))
    head_c.append(f" of {sample['pool']}-thread affinity pool", style="dim")
    if not sample["alive"]:
        head_c.append("  [pool exited]", style="yellow")
    meter_c = _braille_meter(cpu_t, mw,
                             f" {sample['cpu_raw']:6.0f}% raw over "
                             f"{sample['n_procs']} procs")

    ram_frac = sample["uss"] / mem_total if mem_total else 0.0
    head_r = Text("RAM ", style="bold")
    head_r.append(f"{sample['uss'] / 2**30:6.2f} GiB",
                  style="bold " + _hex(_status_rgb(1.0 - ram_frac)))
    head_r.append(" USS, worker tree only", style="dim")
    peak = max([v for v in ram_hist] or [0.0])
    meter_r = _braille_meter(ram_frac, mw,
                             f" {100.0 * ram_frac:5.1f}% of "
                             f"{mem_total / 2**30:.0f} GiB box"
                             if mem_total else " box MemTotal unknown")

    grid = Table.grid(expand=True, padding=(0, 2))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(head_c, head_r)
    gc = _braille_rows(cpu_hist, gw, SYS_GRAPH_ROWS, 1.0)
    gr = _braille_rows(ram_hist, gw, SYS_GRAPH_ROWS, max(peak * 1.15, 1e-9))
    for i in range(SYS_GRAPH_ROWS):
        grid.add_row(gc[i], gr[i])
    grid.add_row(meter_c, meter_r)
    return Panel(grid, title="CFD WORKERS (process-scoped telemetry)",
                 border_style="cyan", box=box.SQUARE, padding=(0, 1))


# ==============================================================================
#  HOST VIEWER BRIDGE (launcher side - see viewer_sidecar.py)
#  The interactive PyVista/Qt 3-D window is a separate HOST process: run.sh
#  starts viewer_sidecar.py next to the container (a GUI cannot cross the
#  container boundary), and the two sides talk ONLY through files in the
#  bind-mounted work directory. The sidecar drops VIEWER_READY_FILE (defined
#  with the viz-export constants above) while it is alive - that is what
#  switches the solver's periodic mid-run export on. The [p] hotkey writes
#  VIEWER_TRIGGER_FILE; the sidecar consumes it and opens its window.
#  viewer_sidecar.py imports both names from here - single source.
# ==============================================================================

VIEWER_TRIGGER_FILE = ".asciistream_viewer_open"


def request_host_viewer():
    """[p] hotkey: ask the host-side PyVista sidecar to open its window.
    Non-blocking by construction - one tiny write + atomic rename, called
    from the key-watcher thread. Returns a one-line rich-markup message for
    the dashboard loop to print (mirrors how the old 3-D fallback explained
    itself when unavailable)."""
    if not os.path.exists(VIEWER_READY_FILE):
        return (" [yellow]3-D pop-out off - no host viewer sidecar is "
                "attached (run ./setup_host_viewer.sh once on the host, "
                "then restart ./run.sh)[/]")
    try:
        tmp = VIEWER_TRIGGER_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{time.time():.3f}\n")
        os.replace(tmp, VIEWER_TRIGGER_FILE)   # atomic on one filesystem
    except OSError as exc:
        return f" [yellow]could not signal the host viewer: {exc}[/]"
    return (" [dim]host viewer signalled - the PyVista window opens on the "
            "host desktop (log: $TMPDIR/asciistream-viewer.log)[/]")



def _decode_keys(buf):
    """Raw cbreak byte chunk -> logical keys: single characters come back
    lowercased ('v', 'w', ...), CSI arrows as 'up'/'down'/'left'/'right',
    coalesced auto-repeat chunks yield every key in order. MUST be fed
    raw os.read bytes: a buffered TextIO read(1) slurps the tail of an
    escape sequence into userspace where select cannot see it, turning
    arrows into junk keystrokes."""
    keys, i = [], 0
    arrows = {0x41: "up", 0x42: "down", 0x43: "right", 0x44: "left"}
    while i < len(buf):
        b = buf[i]
        if (b == 0x1B and i + 2 < len(buf) and buf[i + 1:i + 2] == b"["
                and buf[i + 2] in arrows):
            keys.append(arrows[buf[i + 2]])
            i += 3
        elif b == 0x1B:
            i += 1              # bare ESC / foreign CSI: drop the byte
        else:
            keys.append(chr(b).lower())
            i += 1
    return keys



def build_legend():
    t = Text()
    t.append("flow: ")
    for frac, label in ((0.05, ". stagnant"), (0.3, "~ slow"),
                        (0.6, "- moderate"), (1.0, "* fast")):
        t.append(label.split()[0] + " ", style=_hex(_status_rgb(frac)))
        t.append(label.split()[1] + "  ", style="dim")
    t.append("| ", style="dim")
    t.append("green", style=_hex(STATUS_GRN))
    t.append("=fast ", style="dim")
    t.append("yellow", style=_hex(STATUS_YEL))
    t.append("=moderate ", style="dim")
    t.append("red", style=_hex(STATUS_RED))
    t.append("=stagnant ", style="dim")
    t.append("| 2-D mid-plane streaklines | live transient field | ",
             style="dim")
    t.append("[v]", style="bold")
    t.append(" 3-D iso view ", style="dim")
    t.append("[p]", style="bold")
    t.append(" pop-out 3-D (host viewer) | Ctrl+C stops", style="dim")
    return t


def requirements_table(reqs, summary, heat_load):
    tbl = Table(title="Requirements vs computed fields (final state)",
                box=box.SIMPLE_HEAVY, title_style="bold")
    tbl.add_column("Quantity")
    tbl.add_column("Requested", justify="right")
    tbl.add_column("Computed", justify="right")
    tbl.add_column("Status", justify="center")

    def status(ok):
        return "[bold green]PASS[/]" if ok else "[bold red]FAIL[/]"

    q_out = summary["q_out"]
    t_out = reqs["inlet_temp_c"] + heat_load / (RHO_AIR * max(q_out, 1e-9)
                                                * CP_AIR)
    tbl.add_row("Airflow through chassis", "-",
                f"{q_out * M3S_TO_CFM:.1f} CFM ({q_out:.4f} m^3/s)",
                "[dim]info[/]")
    tbl.add_row("Fan operating estimate", "-",
                f"{summary['fan_op_cfm']:.1f} CFM -> "
                f"{summary['fan_vz']:.2f} m/s plane", "[dim]info[/]")
    tbl.add_row("Mass balance (front vs outlet)", "-",
                f"{100.0 * abs(summary['q_out'] + summary['q_front']) / max(summary['q_fan'], 1e-9):.2f} %",
                "[dim]info[/]")
    # With the energy equation on, the SOLVED mass-flow-weighted outlet
    # temperature supersedes the bulk balance above: the bulk figure
    # assumes every watt reaches the outlet air perfectly mixed, so it
    # cannot show a hot spot or short-circuited flow. Both are printed so
    # they can be compared - agreement is the conservation check, and a
    # gap is the interesting result. Without thermal, unchanged.
    if summary.get("thermal"):
        t_solved = float(summary["t_exhaust_c"])
        t_bulk = float(summary.get("t_exhaust_bulk_c", t_out))
        tbl.add_row(f"Outlet air temperature ({heat_load:.0f} W load)",
                    f"<= {reqs['outlet_temp_max_c']:.1f} degC",
                    f"{t_solved:.1f} degC solved "
                    f"({t_bulk:.1f} bulk est.)",
                    status(t_solved <= reqs["outlet_temp_max_c"]))
        t_max = summary.get("t_max_c")
        if t_max is not None:
            tbl.add_row("Peak air temperature (hot spot)", "-",
                        f"{float(t_max):.1f} degC", "[dim]info[/]")
        q_src = summary.get("q_src_w")
        load_w = summary.get("heat_load_w")
        if q_src is not None and load_w:
            # energy audit: watts actually injected vs watts configured
            err = 100.0 * abs(float(q_src) - float(load_w)) / float(load_w)
            tbl.add_row("Heat injected vs configured", "-",
                        f"{float(q_src):.0f} W of {float(load_w):.0f} W "
                        f"({err:.1f} % off)",
                        status(err <= 1.0))
    else:
        tbl.add_row(f"Outlet air temperature ({heat_load:.0f} W load)",
                    f"<= {reqs['outlet_temp_max_c']:.1f} degC",
                    f"{t_out:.1f} degC (bulk estimate)",
                    status(t_out <= reqs["outlet_temp_max_c"]))
    tbl.add_row("Minimum static pressure (field)",
                f">= {reqs['pressure_min_pa']:.0f} Pa",
                f"{summary['p_min']:.0f} Pa @ {tuple(summary['p_min_at'])}",
                status(summary["p_min"] >= reqs["pressure_min_pa"]))
    tbl.add_row("Min air speed, rear dead zone",
                f">= {reqs['deadzone_speed_min_ms']:.2f} m/s",
                f"{summary['dz_min']:.3f} m/s @ {tuple(summary['dz_min_at'])}",
                status(summary["dz_min"] >= reqs["deadzone_speed_min_ms"]))
    tbl.add_row("Dead-zone mean air speed", "-",
                f"{summary['dz_mean']:.3f} m/s", "[dim]info[/]")
    return tbl


# ==============================================================================
#  IT TELEMETRY (Stage 2): thermal thresholds, acoustics/power, run report
# ==============================================================================

def thermal_check(reqs, summary):
    """Component airflow vs config thresholds. Returns (rows, failed_labels).
    Rows: (label, threshold, computed_mean, ok)."""
    thr = {"cpu": float(reqs.get("cpu_min_airflow_ms", 0.5)),
           "gpu": float(reqs.get("gpu_min_airflow_ms", 0.3)),
           "optics": float(reqs.get("optics_min_airflow_ms", 0.2))}
    rows, fails = [], []
    for kind, label, val in summary.get("components", []):
        t = thr.get(kind, 0.0)
        ok = val is not None and val >= t
        rows.append((label, t, val or 0.0, ok))
        if not ok:
            fails.append(label)
    return rows, fails


def component_table(rows):
    tbl = Table(title="Component airflow (IT telemetry)",
                box=box.SIMPLE_HEAVY, title_style="bold")
    tbl.add_column("Component")
    tbl.add_column("Min required", justify="right")
    tbl.add_column("Computed mean", justify="right")
    tbl.add_column("Status", justify="center")
    for label, thr, val, ok in rows:
        tbl.add_row(label, f">= {thr:.2f} m/s", f"{val:.3f} m/s",
                    "[bold green]PASS[/]" if ok else "[bold red]FAIL[/]")
    return tbl


def thermal_banners(fails):
    """The spec-literal flashing warnings (blink support varies by terminal;
    bold red renders everywhere). Built as Text so the brackets never touch
    the markup parser."""
    return [Text(f"[THERMAL WARNING: {label} Airflow Critical]",
                 style="bold red blink") for label in fails]


def fan_telemetry_table(fan_cfg, n_fans, summary=None):
    """Acoustic + power estimate at the operating RPM. N equal sources
    combine as +10*log10(N) - a free-field engineering estimate.

    When the solver reports duty-scaled values (`summary` carries the
    fan_* keys written by fan_operating_point), the table shows the
    ACTUAL operating point via the Fan Affinity Laws - flow ~ N,
    pressure ~ N^2, shaft power ~ N^3, and dBA ~ +50*log10(N2/N1).
    Without them it falls back to the rated 100 %-duty figures, so a
    headless/legacy run renders exactly as before."""
    duty = (summary or {}).get("fan_duty")
    scaled = duty is not None and abs(float(duty) - 1.0) > 1e-9
    cap = ("fans modeled at rated RPM (100% duty; no thermal PWM); "
           "dBA/W are config estimates")
    if scaled:
        cap = (f"fan affinity laws at {float(duty) * 100:.0f} % of rated RPM "
               "(Q~N, dP~N^2, W~N^3); dBA/W are config estimates")
    tbl = Table(title="Fan acoustics & power (est.)", box=box.SIMPLE_HEAVY,
                title_style="bold", caption=cap)
    tbl.add_column("Quantity")
    tbl.add_column("Value", justify="right")

    def pick(key, fallback):
        """Duty-scaled value when the solver supplied one, else rated."""
        v = (summary or {}).get(key)
        return fallback if v is None else v

    rpm = pick("fan_rpm_scaled", fan_cfg.get("rpm", "-"))
    dba = pick("fan_dba_scaled", fan_cfg.get("max_dBA"))
    watts = pick("fan_watts_scaled", fan_cfg.get("max_wattage"))
    tbl.add_row("Fan model", fan_cfg["display"])
    tbl.add_row("Fan count", str(n_fans))
    if scaled:
        rated = (summary or {}).get("fan_rpm_rated")
        rpm_txt = (f"{rpm:,.0f}" if isinstance(rpm, (int, float))
                   else str(rpm))
        if isinstance(rated, (int, float)):
            rpm_txt += f"  (rated {rated:,.0f})"
        tbl.add_row("Fan duty", f"{float(duty) * 100:.0f} % of rated")
        tbl.add_row("Operating RPM (duty-scaled)", rpm_txt)
        cfm = (summary or {}).get("fan_cfm_scaled")
        mmh = (summary or {}).get("fan_mmh2o_scaled")
        if cfm is not None:
            tbl.add_row("Per-fan max flow (scaled)", f"{cfm:.1f} CFM")
        if mmh is not None:
            tbl.add_row("Per-fan max static (scaled)", f"{mmh:.1f} mmH2O")
    else:
        tbl.add_row("Operating RPM (rated, 100% duty)",
                    f"{rpm:,.0f}" if isinstance(rpm, (int, float))
                    else str(rpm))
    tbl.add_row("Per-fan noise", f"{dba:.1f} dBA" if dba else "n/a")
    if dba:
        tbl.add_row("Combined noise (free-field estimate)",
                    f"{dba + 10.0 * np.log10(max(n_fans, 1)):.1f} dBA")
    tbl.add_row("Total fan power draw",
                f"{watts * n_fans:.1f} W ({watts:.1f} W x {n_fans})"
                if watts else "n/a")

    # --- acoustic ceiling (present only when a target was set) ---------
    s = summary or {}
    if s.get("dba_target") is not None:
        tgt = float(s["dba_target"])
        tbl.add_row("Noise ceiling requested", f"{tgt:g} dBA")
        cap_duty = s.get("dba_target_duty_cap")
        if cap_duty is not None:
            tbl.add_row("Max duty permitted by the ceiling",
                        f"{float(cap_duty) * 100:.0f} % of rated RPM")
        combined = s.get("dba_combined")
        if combined is not None:
            met = s.get("dba_target_met")
            tbl.add_row("Combined noise at the solved duty",
                        f"{float(combined):.1f} dBA "
                        + ("[green]within target[/]" if met
                           else "[bold red]OVER target[/]"))
        if s.get("dba_target_binding"):
            req = s.get("fan_duty_requested")
            tbl.add_row("Ceiling binding?",
                        "[yellow]yes - capped from "
                        f"{float(req) * 100:.0f} %[/]" if req is not None
                        else "[yellow]yes[/]")
        elif s.get("dba_target_achievable") is False:
            tbl.add_row("Ceiling binding?",
                        "[bold red]unreachable even at the duty floor[/]")
        else:
            tbl.add_row("Ceiling binding?", "no - fans stayed below it")
    return tbl


def dba_thermal_banner(summary):
    """The homelab verdict: did holding the noise limit cook the box?

    Returns a rich renderable, or None when no acoustic target was set.
    `dba_exhaust_basis` records whether the exhaust figure came from the
    solved energy equation ("solved") or the bulk balance ("bulk") - the
    warning says which, because a bulk estimate cannot see a hot spot."""
    s = summary or {}
    if s.get("dba_target") is None:
        return None
    t_out = s.get("dba_exhaust_c")
    t_max = s.get("outlet_temp_max_c")
    if t_out is None or t_max is None:
        return None
    basis = s.get("dba_exhaust_basis", "bulk")
    basis_txt = ("solved energy equation" if basis == "solved"
                 else "bulk balance - run with the energy equation for a "
                      "hot-spot-aware answer")
    if s.get("dba_overheat"):
        return Text(
            f"[THERMAL WARNING: {float(s['dba_target']):g} dBA noise limit "
            f"starves the chassis] exhaust {float(t_out):.1f} degC exceeds "
            f"the {float(t_max):.1f} degC ceiling ({basis_txt}). Raise the "
            "dBA limit, cut the heat load, or accept throttling.",
            style="bold red")
    return Text(
        f"noise limit {float(s['dba_target']):g} dBA holds without "
        f"overheating: exhaust {float(t_out):.1f} degC vs the "
        f"{float(t_max):.1f} degC ceiling ({basis_txt})",
        style="green")


def write_report(server_cfg, params, fan_cfg, summary, comp_rows, fails,
                 scene):
    """Timestamped plain-text run report: profile, fan, telemetry, warnings
    and a clean non-ANSI version of the dashboard cross-section."""
    rc = Console(record=True, width=118, file=io.StringIO(),
                 force_terminal=False)
    rc.print("ASCIISTREAM - SERVER CHASSIS CFD RUN REPORT")
    rc.print(time.strftime("generated: %Y-%m-%d %H:%M:%S"))
    rc.print(f"server   : {server_cfg['display_name']} "
             f"[{params['profile']}, {server_cfg['form_factor']}]")
    rc.print(f"fan      : {fan_cfg['display']} x {server_cfg['fan_count']}")
    rc.print(f"mesh     : "
             f"{summary.get('mesh_desc', summary.get('mesh_level', '?'))}, "
             f"{summary.get('n_cells', 0):,} elements, "
             f"{summary.get('ranks', '?')} MPI ranks")
    rc.print(f"simulated: {summary['sim_time']:.1f} s @ "
             f"dt={summary.get('dt', SIM_DT):g} s "
             f"(wall {summary['wall_time']:.0f} s)")
    # Which engine produced these numbers is load-bearing context: the same
    # profile reports very different through-flow on the 2-D slice (no
    # floor/ceiling friction) than in 3-D, so a report that omitted it
    # would make two incomparable runs look interchangeable.
    eng = str(summary.get("engine", "3d")).lower()
    duty = summary.get("fan_duty")
    line = ("2-D planar (mid-height slice)" if eng == "2d"
            else "3-D volumetric")
    if duty is not None:
        line += f"  |  fan duty {float(duty) * 100:.0f} % of rated RPM"
    rc.print(f"engine   : {line}")
    if eng == "2d":
        rc.print("           NOTE: the planar engine models no floor/"
                 "ceiling friction, so it over-predicts")
        rc.print("           through-flow - measured at +5 % vs 3-D near "
                 "steady state on 6029U/coarse,")
        rc.print("           and by more during the early transient. "
                 "Confirm in 3-D before relying on it.")
    hw = params.get("hw") or {}
    bits = []
    if hw.get("drive_type"):
        bits.append(f"drives {hw['drive_type']}")
    if hw.get("gpu_count"):
        bits.append(f"{hw['gpu_count']}x GPU @ "
                    f"{hw.get('gpu_watts', 0.0):.0f} W")
    if hw.get("nic"):
        bits.append("NIC populated")
    if bits:
        rc.print(f"hardware : {', '.join(bits)}")
    rc.print()
    rc.print(requirements_table(server_cfg["requirements"], summary,
                                server_cfg["heat_load"]))
    rc.print(component_table(comp_rows))
    if fails:
        for label in fails:
            rc.print(f"[THERMAL WARNING: {label} Airflow Critical]",
                     markup=False)
    else:
        rc.print("no thermal warnings - all component airflow thresholds met")
    rc.print(fan_telemetry_table(fan_cfg, server_cfg["fan_count"], summary))
    _dba_banner = dba_thermal_banner(summary)
    if _dba_banner is not None:
        rc.print(_dba_banner)
    rc.print()
    rc.print(build_main_panel(scene))
    rc.print(build_legend())
    if scene.get("iso_panel"):
        rc.print(scene["iso_panel"])
    if scene.get("front_panel"):
        rc.print(scene["front_panel"])
        rc.print(scene["rear_panel"])

    fname = (f"cfd_report_{params['profile']}_"
             f"{time.strftime('%Y%m%d-%H%M%S')}.txt")
    with open(fname, "w") as f:
        f.write(rc.export_text(styles=False))
    return os.path.abspath(fname)


# ==============================================================================
#  LAUNCHER: socket server, worker process management, live dashboard
# ==============================================================================

def recv_exact(f, n):
    data = b""
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def reader_thread(conn, state):
    """Receive length-prefixed (json header, npz payload) messages."""
    f = conn.makefile("rb")
    try:
        while True:
            hlen = struct.unpack(">I", recv_exact(f, 4))[0]
            header = json.loads(recv_exact(f, hlen).decode())
            plen = struct.unpack(">I", recv_exact(f, 4))[0]
            payload = recv_exact(f, plen) if plen else b""
            if header["type"] == "frame":
                arrays = dict(np.load(io.BytesIO(payload)))
                with state["lock"]:
                    state["frame"] = (header, arrays)
                    state["n_frames"] += 1
            elif header["type"] == "summary":
                state["summary"] = header
            elif header["type"] == "end":
                break
    except (EOFError, OSError):
        pass
    finally:
        state["done"].set()


def launcher_wizard(console, cfg, config_path):
    console.clear()
    render_banner(console)
    console.print(Panel.fit(
        "[bold cyan]ASCIISTREAM[/]  [dim]v0.8 - terminal CFD for server "
        "chassis[/]\n"
        "[white]Transient Navier-Stokes (incremental pressure-correction) on "
        "MPI workers[/]\n"
        "[dim]parametric gmsh meshing from server_configs.json | live ASCII "
        "flow dashboard | isometric 3-D chassis view[/]",
        border_style="cyan", box=box.DOUBLE))

    def bays_cell(s):
        front = int(s.get("drive_bays_front", s.get("drive_bay_count", 0)))
        rear = int(s.get("drive_bays_rear", 0))
        if front == 0 and rear == 0:
            return "-"
        txt = f"{front}F" + (f"+{rear}R" if rear else "")
        return f"{txt} {s['drive_bay_type']}" if s.get("drive_bay_type") \
            else txt

    servers = list(cfg["servers"].keys())
    menu = Table(box=box.SIMPLE, title="Server profiles (server_configs.json)",
                 title_style="bold")
    for col in ("#", "Key", "Chassis", "Form", "Bays", "PCIe", "DIMMs"):
        menu.add_column(col)
    for i, key in enumerate(servers, 1):
        s = cfg["servers"][key]
        menu.add_row(str(i), key, s["display_name"], s["form_factor"],
                     bays_cell(s),
                     str(s.get("populated_pcie_slots", 0)),
                     str(s.get("total_dimm_slots", 0)))
    custom_row = len(servers) + 1
    menu.add_row(str(custom_row), "[bold]custom[/]",
                 "[bold]Custom Server Configuration[/] (type a name; new "
                 "names are appended to the JSON)", "-", "-", "-", "-")
    console.print(menu)
    sel = Prompt.ask("  Select target server profile",
                     choices=[str(i) for i in range(1, custom_row + 1)],
                     default="1", console=console)
    if int(sel) == custom_row:
        while True:
            name = Prompt.ask("    Custom server name",
                              console=console).strip()
            if name:
                break
            console.print("    [red]a name is required[/]")
        if ensure_custom_server(cfg, name, config_path):
            console.print(f"    [green]->[/] new profile '{name}' appended "
                          f"to {config_path} (generic 2U template - edit "
                          "the JSON to shape its geometry)")
        else:
            console.print(f"    [green]->[/] existing profile '{name}' "
                          "loaded")
        profile = name
    else:
        profile = servers[int(sel) - 1]

    # --- hardware configuration prompts (RUNTIME overrides - the profile
    # --- JSON stays untouched; a temp overlay config carries them to the
    # --- workers)
    s = json.loads(json.dumps(cfg["servers"][profile]))    # deep copy
    reqs0 = s.get("requirements", {})
    console.print("\n  [bold]Hardware configuration[/] [dim](Enter keeps "
                  "the profile default)[/]")
    hw = {}
    if int(s.get("drive_bay_count", 0)) > 0 and s.get("drive_zone_z"):
        # [0] removes the cage entirely: diskless compute nodes are real
        # (the C4130 is usually ordered that way), and the geometry layer
        # has always supported drive_bay_count = 0 - only this prompt
        # blocked it.
        dsel = Prompt.ask("  Drive type  [0] No drives  "
                          "[1] 2.5in NVMe/SAS  [2] 3.5in HDD",
                          choices=["0", "1", "2"],
                          default="2" if "3.5" in str(s.get("drive_bay_type")
                                                      or "") else "1",
                          console=console)
        if dsel == "0":
            hw["drive_bay_count"] = 0
            console.print("    [dim]drive cage removed - the front bay "
                          "becomes open intake air[/]")
        else:
            hw["drive_type"] = ("2.5in NVMe/SAS", "3.5in HDD")[int(dsel) - 1]
    hw["heat_load_w"] = FloatPrompt.ask(
        "  Total system wattage [W of heat load]",
        default=float(s["heat_load"]), console=console)
    hw["inlet_temp_c"] = FloatPrompt.ask(
        "  Ambient intake air temperature [degC]",
        default=float(reqs0.get("inlet_temp_c", 22.0)), console=console)
    hw["exhaust_temp_c"] = FloatPrompt.ask(
        "  Desired exhaust temperature ceiling [degC]",
        default=float(reqs0.get("outlet_temp_max_c", 35.0)), console=console)
    if hw["exhaust_temp_c"] <= hw["inlet_temp_c"]:
        console.print("    [yellow]note:[/] exhaust ceiling <= intake - the "
                      "outlet temperature check can only FAIL.")
    if s.get("pcie_zone_z"):
        # Explicit card count rather than a binary GPU/NIC toggle: a slot
        # is either occupied or not, and the profile's pcie_max_slots caps
        # it so cards only spawn into risers that physically exist.
        max_slots = int(s.get("pcie_max_slots", PCIE_MAX_SLOTS_DEFAULT))
        n_cards = IntPrompt.ask(
            f"  How many PCIe cards installed? [0 to {max_slots}]",
            default=0, console=console)
        n_cards = max(0, min(int(n_cards), max_slots))
        hw["pcie_card_count"] = n_cards
        if n_cards:
            n_gpu = IntPrompt.ask(
                f"    How many of those {n_cards} are GPUs?",
                default=0, console=console)
            n_gpu = max(0, min(int(n_gpu), n_cards))
            if n_gpu:
                hw["gpu_count"] = n_gpu
                hw["gpu_watts"] = max(0.0, FloatPrompt.ask(
                    "    Wattage per GPU [W]", default=250.0,
                    console=console))
            # any remaining card is treated as a NIC-class card, which is
            # what earns the [ NIC ] label on the last slot
            hw["nic"] = n_gpu < n_cards
            console.print(
                f"    [dim]{n_cards} card(s) meshed in the PCIe zone"
                + (f"; {n_gpu} GPU(s), wattage joins the heat load"
                   if n_gpu else "") + "[/]")
        else:
            hw["nic"] = False
            console.print("    [dim]no cards fitted - the slots stay open "
                          "air; only the static risers remain[/]")
    else:
        console.print("    [dim]profile has no PCIe riser - GPU/NIC prompts "
                      "skipped[/]")
    s = apply_hw_overrides(s, hw)

    fans = list(cfg["fans"].keys())
    fmenu = Table(box=box.SIMPLE, title="Fans", title_style="bold")
    for col in ("#", "Fan", "Free flow", "Shut-off", "RPM"):
        fmenu.add_column(col)
    for i, key in enumerate(fans, 1):
        fan = cfg["fans"][key]
        fmenu.add_row(str(i), fan["display"], f"{fan['max_cfm']:.1f} CFM",
                      f"{fan['max_mmh2o']:.1f} mmH2O", str(fan.get("rpm", "-")))
    fmenu.add_row(str(len(fans) + 1), "[bold]Custom fan[/] (enter your own "
                  "specs)", "your CFM", "your mmH2O", "-")
    console.print(fmenu)
    fsel = Prompt.ask("  Select fan",
                      choices=[str(i) for i in range(1, len(fans) + 2)],
                      default="1", console=console)
    fan_custom = None
    if int(fsel) == len(fans) + 1:
        fan = "custom"
        while True:
            cfm = FloatPrompt.ask("    Custom fan max airflow [CFM]",
                                  default=100.0, console=console)
            mmh2o = FloatPrompt.ask("    Custom fan max static pressure "
                                    "[mmH2O]", default=40.0, console=console)
            if cfm > 0 and mmh2o > 0:
                break
            console.print("    [red]both values must be > 0[/]")
        fan_custom = custom_fan_cfg(cfm, mmh2o)
        console.print(f"    [green]->[/] {fan_custom['display']} "
                      "[dim](acoustics/power telemetry shows n/a - no "
                      "rpm/dBA/wattage data)[/]")
    else:
        fan = fans[int(fsel) - 1]

    n_host = os.cpu_count() or 4
    # no ceiling: any integer goes straight to `mpiexec -n` (36-core boxes
    # should not be argued with); floor of 1 is the only clamp
    cores = IntPrompt.ask("  MPI ranks / CPU threads to allocate "
                          f"(no cap - this machine reports {n_host})",
                          default=n_host, console=console)
    cores = max(1, int(cores))
    if cores > n_host:
        console.print(f"    [yellow]note:[/] oversubscribing - this machine "
                      f"reports {n_host} hardware threads; Open MPI's "
                      "--oversubscribe/--use-hwthread-cpus flags cover this.")
    if cores > 8:
        console.print("    [yellow]note:[/] iterative solves on this mesh "
                      "size stop scaling around ~8 ranks.")
    sim_time = FloatPrompt.ask("  Simulation time passage [s of transient "
                               "fluid dynamics]", default=30.0,
                               console=console)
    dt = FloatPrompt.ask(f"  Time step dt [s] (default {SIM_DT:g}; smaller = "
                         "finer transients, more steps)", default=SIM_DT,
                         console=console)
    if not (SIM_DT_MIN <= dt <= SIM_DT_MAX):
        dt = min(max(dt, SIM_DT_MIN), SIM_DT_MAX)
        console.print(f"    [yellow]note:[/] dt clamped to {dt:g} s "
                      f"(allowed {SIM_DT_MIN:g}..{SIM_DT_MAX:g}).")
    if sim_time / dt > 20000:
        console.print(f"    [yellow]note:[/] {sim_time / dt:,.0f} steps at "
                      "this dt - expect a long solve.")

    # --- mesh resolution preset + RAM safeguard ------------------------------
    ms = s.get("mesh_settings") or DEFAULT_MESH_SETTINGS
    if "mesh_settings" not in s:
        console.print("    [yellow]note:[/] config predates mesh presets - "
                      "run --write-config to refresh server_configs.json")
    levels = [lv for lv in MESH_LEVEL_ORDER if lv in ms]
    console.print("\n  Mesh Resolution Level:")
    for i, lv in enumerate(levels, 1):
        mm = float(ms[lv]["element_size_mm"])
        console.print(f"[{i}] {MESH_LEVEL_LABEL.get(lv, lv.title())} "
                      f"({mm:g}mm) - Est. RAM: "
                      f"{MESH_RAM_NOTES.get(lv, 'n/a')}",
                      markup=False, highlight=False)
    custom_i = len(levels) + 1
    console.print(f"[{custom_i}] Custom element size - any value down to "
                  f"{MESH_MM_FLOOR:g}mm (sub-millimetre needs a massive "
                  "RAM pool)", markup=False, highlight=False)
    console.print("  [dim]note: heatsinks stay homogenized porous blocks at "
                  "every preset - finer meshes sharpen jets and wakes, they "
                  "do not add fin geometry.[/]")
    msel = Prompt.ask("  Select mesh resolution",
                      choices=[str(i) for i in range(1, custom_i + 1)],
                      default="1", console=console)
    if int(msel) == custom_i:
        mesh_mm = FloatPrompt.ask(
            f"    Element size [mm] ({MESH_MM_FLOOR:g}-35)", default=1.0,
            console=console)
        if not (MESH_MM_FLOOR <= mesh_mm <= 35.0):
            mesh_mm = min(max(mesh_mm, MESH_MM_FLOOR), 35.0)
            console.print(f"    [yellow]note:[/] element size clamped to "
                          f"{mesh_mm:g} mm.")
        # transported to the workers as the literal value ("0.8") - the
        # numeric branch of mesh_level_lc picks it up on their side
        mesh_level, mesh_arg = "custom", f"{mesh_mm:g}"
    else:
        mesh_level = levels[int(msel) - 1]
        mesh_arg = mesh_level
        # Stage 3 STRICT gate: ultra is offered to everyone, but selecting
        # it on a machine under 32 GB raises MemoryError and the script
        # dies with the traceback - deliberately unhandled, per spec
        enforce_ultra_ram(mesh_level)
        mesh_mm = float(ms[mesh_level]["element_size_mm"])
    # Deliberately the 3-D estimate: the engine is chosen after the mesh
    # prompt, so this stays a conservative upper bound. A 2-D run needs
    # roughly an order of magnitude less, so the RAM guidance here only
    # ever over-warns - it never lets an oversized choice through.
    n_est = est_cells(build_geometry(s), mesh_mm / 1000.0)
    if mesh_level != "ultra":               # soft confirm for the rest
        try:                                # container-visible MemTotal check
            with open("/proc/meminfo") as f:
                total_gb = int(f.readline().split()[1]) / 1024**2
            need = (MESH_RAM_HIGH_GB.get(mesh_level)
                    or n_est * MESH_EST_KB_CELL / 2**20)
            if need > 0.8 * total_gb:
                console.print(f"    [bold red]RAM warning:[/] this mesh "
                              f"may need ~{need:,.0f} GB; this machine "
                              f"reports {total_gb:.0f} GB total.")
                if not Confirm.ask("    Continue anyway?", default=False,
                                   console=console):
                    console.print("  [yellow]Aborted.[/]")
                    return None
        except (OSError, ValueError, IndexError):
            pass                            # no /proc: skip, never block

    summary = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    summary.add_column(style="dim")
    summary.add_column(justify="right")
    summary.add_row("Server", s["display_name"])
    fan_disp = fan_custom["display"] if fan_custom else cfg["fans"][fan]["display"]
    summary.add_row("Fan", f"{fan_disp} x {s['fan_count']} (fan wall)")
    summary.add_row("MPI ranks", str(cores))
    summary.add_row("Simulated time", f"{sim_time:.1f} s @ dt={dt:g}s "
                    f"({int(round(sim_time / dt))} steps)")
    summary.add_row("Mesh resolution",
                    f"{MESH_LEVEL_LABEL.get(mesh_level, mesh_level.title())} "
                    f"({mesh_mm:g} mm, ~{n_est:,.0f} elements est.)")
    summary.add_row("Heat load", f"{s['heat_load']:.0f} W")
    if hw.get("drive_type"):
        summary.add_row("Drive type", hw["drive_type"])
    if hw.get("gpu_count"):
        summary.add_row("GPUs", f"{hw['gpu_count']} x "
                        f"{hw.get('gpu_watts', 0.0):.0f} W (meshed as PCIe "
                        "cards)")
    if hw.get("nic"):
        summary.add_row("NIC", "1 slot (Mellanox/Intel class)")
    summary.add_row("Intake -> exhaust ceiling",
                    f"{s['requirements']['inlet_temp_c']:.1f} -> "
                    f"{s['requirements']['outlet_temp_max_c']:.1f} degC")

    # --- dual engine + fan duty ----------------------------------------
    # Asked last so the existing prompt order (and the README's numbered
    # walkthrough of it) stays intact.
    console.print("\n [bold]Solver engine[/] - [cyan]3d[/] volumetric is the "
                  "full-fidelity solve; [cyan]2d[/] planar solves only the\n"
                  " mid-height slice: ~13x fewer cells, ~26x quicker. It "
                  "models no floor/ceiling friction,\n so it over-predicts "
                  "through-flow - measured at +5% near steady state on "
                  "6029U/coarse,\n but more during the early transient. "
                  "Explore in 2d, confirm in 3d.")
    engine = Prompt.ask("  Engine", choices=["3d", "2d"], default="3d",
                        console=console)
    fan_duty = FloatPrompt.ask(
        "  Fan duty as a fraction of rated RPM (affinity laws: flow ~ N, "
        "pressure ~ N^2, power ~ N^3)", default=1.0, console=console)
    if not (FAN_DUTY_MIN <= fan_duty <= FAN_DUTY_MAX):
        clamped = min(max(fan_duty, FAN_DUTY_MIN), FAN_DUTY_MAX)
        console.print(f"  [yellow]duty {fan_duty:g} outside "
                      f"[{FAN_DUTY_MIN:g}, {FAN_DUTY_MAX:g}] - "
                      f"clamped to {clamped:g}[/]")
        fan_duty = clamped
    thermal = Confirm.ask(
        "  Solve the energy equation (real air temperature rise)?",
        default=False, console=console)

    # --- acoustic ceiling (the homelab question) -----------------------
    # Enter = unconstrained. A ceiling caps the fan duty via the same
    # affinity/noise laws the telemetry table uses, so the answer to "will
    # it cook itself at 45 dBA?" comes out of the same physics.
    dba_target = None
    fan_dba = (cfg["fans"].get(fan) or {}).get("max_dBA") if not fan_custom \
        else None
    if fan_dba:
        quietest = combined_noise_dba(
            fan_dba + 50.0 * np.log10(FAN_DUTY_MIN), s["fan_count"])
        loudest = combined_noise_dba(fan_dba, s["fan_count"])
        console.print(f"\n  [bold]Noise ceiling[/] - this fan wall runs at "
                      f"[cyan]{loudest:.0f} dBA[/] at full duty "
                      f"(~{quietest:.0f} dBA at the {FAN_DUTY_MIN:g} duty "
                      "floor).\n  Typical limits: 45 dBA living room, "
                      "55 dBA office, 65 dBA rack room.")
        raw = Prompt.ask("  Set target noise limit in dBA (Enter = max "
                         "performance)", default="", console=console)
        raw = str(raw).strip()
        if raw:
            try:
                dba_target = float(raw)
            except ValueError:
                console.print("    [yellow]not a number - ignoring the "
                              "noise limit[/]")
                dba_target = None
    elif not fan_custom:
        console.print("\n  [dim]this fan has no rated dBA - noise-target "
                      "mode unavailable[/]")
    else:
        console.print("\n  [dim]custom fan has no rated dBA - noise-target "
                      "mode unavailable[/]")

    if dba_target is not None:
        sol = solve_duty_for_dba(dba_target, fan_dba, s["fan_count"])
        if not sol["achievable"]:
            console.print(f"    [yellow]{dba_target:g} dBA is below what "
                          f"these fans can reach even at the "
                          f"{FAN_DUTY_MIN:g} duty floor - the solve will "
                          "run at that floor and report the shortfall[/]")
        elif sol["constrained"]:
            console.print(f"    [dim]caps fan duty at "
                          f"{sol['duty_cap'] * 100:.0f} % of rated RPM[/]")
        else:
            console.print("    [dim]above this fan wall's full-duty output "
                          "- not a binding constraint[/]")

    summary.add_row("Solver engine",
                    "3-D volumetric" if engine == "3d" else "2-D planar")
    summary.add_row("Fan duty", f"{fan_duty * 100:.0f} % of rated RPM")
    summary.add_row("Noise ceiling",
                    f"{dba_target:g} dBA target" if dba_target is not None
                    else "none (max performance)")
    summary.add_row("Energy equation",
                    f"on - {s['heat_load']:.0f} W over the heat sources, "
                    f"intake {s['requirements']['inlet_temp_c']:.0f} degC"
                    if thermal else "off (bulk outlet estimate only)")

    console.print(Panel(summary, title="Run parameters", border_style="cyan"))
    if not Confirm.ask("  Launch the MPI solver?", default=True,
                       console=console):
        console.print("  [yellow]Aborted.[/]")
        return None
    return {"profile": profile, "profile_runtime": s, "hw": hw,
            "fan": fan, "fan_custom": fan_custom,
            "cores": cores, "sim_time": sim_time, "dt": dt,
            "mesh": mesh_arg, "engine": engine, "fan_duty": fan_duty,
            "thermal": thermal, "dba_target": dba_target}


def detect_mpi_flags():
    """Launch flags for the worker pool, keyed off `mpiexec --version`.

    Open MPI refuses to start more ranks than the slots it detects, and on
    Intel hybrid P/E-core CPUs it undercounts usable slots - so runs above
    8 ranks (or above the detected count) died at launch. --oversubscribe
    lifts the slot limit, --use-hwthread-cpus counts hardware threads as
    slots, and root (the usual uid inside the dolfinx container) also needs
    --allow-run-as-root. MPICH has neither the problem nor the flags (it
    errors on them), and an unidentified launcher gets none.
    """
    try:
        r = subprocess.run(["mpiexec", "--version"], capture_output=True,
                           text=True, timeout=10)
        banner = ((r.stdout or "") + (r.stderr or "")).lower()
    except (OSError, subprocess.SubprocessError):
        return []
    if not any(k in banner for k in ("open mpi", "open-mpi", "openrte",
                                     "open rte", "prte", "prterun")):
        return []
    flags = ["--oversubscribe", "--use-hwthread-cpus"]
    if getattr(os, "geteuid", lambda: -1)() == 0:
        flags.append("--allow-run-as-root")
    return flags


def launcher_main(config_path):
    console = Console()
    cfg = load_config(config_path)
    params = launcher_wizard(console, cfg, config_path)
    if params is None:
        return

    # RUNTIME profile (config + hardware prompt overrides). The workers
    # rebuild geometry from JSON, so the overrides ride to them in a temp
    # overlay config - the user's server_configs.json is never touched.
    server_cfg = params["profile_runtime"]
    run_cfg = json.loads(json.dumps(cfg))
    run_cfg["servers"][params["profile"]] = server_cfg
    fd, run_cfg_path = tempfile.mkstemp(prefix="asciistream_run_",
                                        suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(run_cfg, f)
    geo = build_geometry(server_cfg)
    # fill the terminal: the main pane takes every column the fixed-width
    # side stack leaves behind - no artificial cap, 16:9 monitors get the
    # whole width (the worker samples its field grid at exactly this size)
    ncols = max(40, console.width - MINI_COLS - 12)

    # socket server first, then spawn the MPI worker pool pointing back at it
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    cmd = ["mpiexec", *detect_mpi_flags(), "-n", str(params["cores"]),
           sys.executable, os.path.abspath(__file__), "--worker",
           "--profile", params["profile"], "--fan", params["fan"],
           "--sim-time", str(params["sim_time"]), "--dt", str(params["dt"]),
           "--mesh", params["mesh"],
           "--engine", params.get("engine", "3d"),
           "--fan-duty", str(params.get("fan_duty", 1.0)),
           "--thermal", "on" if params.get("thermal") else "off",
           *(["--dba-target", str(params["dba_target"])]
             if params.get("dba_target") is not None else []),
           "--callback-port", str(port), "--cols", str(ncols),
           "--config", run_cfg_path]
    # Mid-run field export costs real I/O, so only enable it when the host
    # viewer sidecar has announced itself (see VIEWER_READY_FILE).
    if os.path.exists(VIEWER_READY_FILE):
        cmd += ["--viz-every", str(VIZ_EVERY_DEFAULT)]
    if params["fan_custom"]:
        cmd += ["--fan-cfm", str(params["fan_custom"]["max_cfm"]),
                "--fan-mmh2o", str(params["fan_custom"]["max_mmh2o"])]
    # stdin=DEVNULL is load-bearing two ways: PRRTE's stdin forwarder
    # otherwise reads the launcher's pty and (1) races the [v]/[p] key
    # watcher for keystrokes, (2) can trip tty job-control stops under
    # synthetic ptys. The workers never read stdin - argv + callback
    # socket only.
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    err_tail = deque(maxlen=30)
    threading.Thread(target=lambda: [err_tail.append(l) for l in proc.stderr],
                     daemon=True).start()

    # privacy-scoped telemetry: the widget watches THIS process tree only
    telem = WorkerTelemetry(proc.pid) if HAVE_PSUTIL else None
    mem_total = _box_mem_total() if HAVE_PSUTIL else 0
    cpu_hist = deque(maxlen=1024)     # pool-fraction samples, 0.5 s apart
    ram_hist = deque(maxlen=1024)     # summed-USS samples [bytes]
    out_tail = deque(maxlen=30)
    threading.Thread(target=lambda: [out_tail.append(l) for l in proc.stdout],
                     daemon=True).start()

    n_est = est_cells(geo, mesh_level_lc(server_cfg, params["mesh"]),
                      engine=params.get("engine", "3d"))
    console.print(f"\n [dim]worker pool: "
                  f"{' '.join(cmd[:cmd.index(sys.executable)])} python3 "
                  f"chassis_cfd.py --worker ... (callback port {port})[/]")
    console.print(f" waiting for the solver: building a ~{n_est:,.0f}-element "
                  f"'{params['mesh']}' mesh + JIT-compile "
                  "(first run can take a minute)...")
    srv.settimeout(FIRST_FRAME_TIMEOUT)
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        proc.terminate()
        console.print("[red]worker never connected; last output:[/]")
        console.print("".join(list(out_tail)[-10:] + list(err_tail)[-10:]))
        return
    finally:
        srv.close()
        try:            # workers read the overlay before connecting back,
            os.unlink(run_cfg_path)     # so it is disposable either way
        except OSError:
            pass

    state = {"lock": threading.Lock(), "frame": None, "summary": None,
             "n_frames": 0, "done": threading.Event()}
    threading.Thread(target=reader_thread, args=(conn, state),
                     daemon=True).start()

    scene = {
        "canvas": build_geometry_canvas(geo, MAIN_ROWS, ncols),
        "particles": ParticleField(geo, ncols),
        "display_name": server_cfg["display_name"],
        "status": {"t": 0.0, "t_total": params["sim_time"], "step": 0,
                   "steps": 1, "q_out": 0.0},
        "front_panel": None, "rear_panel": None,
        "iso_panel": None, "view": "top",
    }

    def ingest(header, arrays):
        vref = max(header["fan_vz"], 1e-6)
        scene["particles"].update_fields(arrays["ux"], arrays["uz"],
                                         arrays["speed"], vref)
        scene["status"] = {"t": header["t"], "t_total": params["sim_time"],
                           "step": header["step"], "steps": header["steps"],
                           "q_out": header["q_out"],
                           "cells": header.get("cells", 0)}
        scene["front_panel"] = build_mini_panel(
            arrays["front"], vref, "FRONT INLET", n_bays=geo["n_bays"])
        scene["rear_panel"] = build_mini_panel(arrays["rear"], vref,
                                               "REAR EXHAUST")
        if "speed" in arrays:
            scene["iso_panel"] = build_chassis_iso_panel(
                arrays["speed"], geo, vref, ncols, MAIN_ROWS)

    seen = 0

    def poll_frame():
        nonlocal seen
        with state["lock"]:
            if state["n_frames"] > seen and state["frame"]:
                seen = state["n_frames"]
                ingest(*state["frame"])
                return True
        return False

    # [v] toggles the main pane between top-down and the ASCII iso view,
    # [p] signals the host viewer sidecar: single-char reads off a
    # cbreak'd stdin in a daemon thread. The original termios state is
    # restored (and the thread joined) before any post-run prompt.
    keys_stop = threading.Event()
    key_thr = None

    def key_watcher():
        try:
            import select
            import termios
            import tty
        except ImportError:
            return
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
        except (termios.error, OSError):
            return
        try:
            tty.setcbreak(fd)
            while not keys_stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.15)
                if not ready:
                    continue
                # RAW reads only: buffered TextIO read(1) slurps escape-
                # sequence tails into userspace where select is blind
                chunk = os.read(fd, 64)
                if chunk.endswith(b"\x1b") or chunk.endswith(b"\x1b["):
                    # an arrow split across relay buffers (podman pty):
                    # give the tail one short chance to arrive, or an
                    # up-arrow would decode as a stray 'a' = left-rotate
                    r2, _, _ = select.select([fd], [], [], 0.03)
                    if r2:
                        chunk += os.read(fd, 8)
                for k in _decode_keys(chunk):
                    if k in ("v", "2", "3"):
                        scene["view"] = ("iso" if scene["view"] == "top"
                                         else "top")
                    elif k == "p":
                        # non-blocking: one tiny file write; the message
                        # is printed by the main loop so rich Console
                        # calls stay off this thread
                        scene["viewer_note"] = request_host_viewer()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    try:
        if not console.is_terminal:
            # non-interactive (piped/CI): consume frames, print final board
            while not state["done"].wait(timeout=0.5):
                poll_frame()
            poll_frame()
            for _ in range(60):
                scene["particles"].step()
            console.print(build_main_panel(scene))
            console.print(build_legend())
            if scene["iso_panel"]:
                console.print(scene["iso_panel"])
            if scene["front_panel"]:
                console.print(scene["front_panel"])
                console.print(scene["rear_panel"])
        else:
            if sys.stdin.isatty():
                key_thr = threading.Thread(target=key_watcher, daemon=True)
                key_thr.start()
            if telem:
                telem.sample()      # prime the per-process delta counters
            # btop-style frame: flow panes on top, a full-width telemetry
            # strip along the bottom; the side stack is FIXED width so the
            # main pane absorbs all remaining columns (no dead space)
            layout = Layout()
            layout.split_column(Layout(name="body", ratio=1),
                                Layout(name="system", size=SYS_STRIP_ROWS))
            layout["body"].split_row(Layout(name="main", ratio=1),
                                     Layout(name="side",
                                            size=MINI_COLS + 4))
            layout["side"].split_column(Layout(name="front"),
                                        Layout(name="rear"))
            wait = Panel("waiting for first field frame...",
                         border_style="yellow")
            layout["main"].update(wait)
            layout["front"].update(wait)
            layout["rear"].update(wait)
            layout["system"].update(build_sys_panel(
                None, cpu_hist, ram_hist, mem_total, console.width))
            sys_at = 0.0
            with Live(layout, console=console, refresh_per_second=ANIM_FPS,
                      screen=False):
                while not state["done"].is_set() or poll_frame():
                    got = poll_frame()
                    note = scene.pop("viewer_note", None)
                    if note:            # [p] feedback (set by key_watcher;
                        console.print(note)   # Live lifts prints above it)
                    if scene["front_panel"] is not None:
                        scene["particles"].step()
                        # [v]/2/3 toggle: the main pane is either the 2-D
                        # top-down particle field or the ASCII isometric
                        # chassis view (always available - it is the same
                        # renderer the report embeds; the raster-grade 3-D
                        # window lives in the host viewer sidecar, [p])
                        main_view = (scene["iso_panel"]
                                     if scene["view"] == "iso"
                                     and scene["iso_panel"] is not None
                                     else build_main_panel(scene))
                        layout["main"].update(
                            Group(main_view, build_legend()))
                        if got:
                            layout["front"].update(scene["front_panel"])
                            layout["rear"].update(scene["rear_panel"])
                    now = time.monotonic()
                    if telem and now - sys_at >= 0.5:   # telemetry strip
                        sys_at = now
                        smp = telem.sample()
                        cpu_hist.append(smp["cpu_pool"])
                        ram_hist.append(smp["uss"])
                        layout["system"].update(build_sys_panel(
                            smp, cpu_hist, ram_hist, mem_total,
                            console.width))
                    time.sleep(1.0 / ANIM_FPS)
    except KeyboardInterrupt:
        console.print("\n [yellow]dashboard stopped - terminating worker[/]")
        proc.terminate()
    finally:
        keys_stop.set()
        if key_thr is not None:
            key_thr.join(timeout=1.0)

    proc.wait(timeout=60)
    if state["summary"]:
        summary = state["summary"]
        fan_cfg = params["fan_custom"] or cfg["fans"][params["fan"]]
        console.print()
        if scene["iso_panel"]:      # final 3-D chassis view (ASCII)
            console.print(scene["iso_panel"])
        console.print(requirements_table(server_cfg["requirements"], summary,
                                         server_cfg["heat_load"]))
        comp_rows, fails = thermal_check(server_cfg["requirements"], summary)
        console.print(component_table(comp_rows))
        if fails:
            for banner in thermal_banners(fails):
                console.print(banner)
        else:
            console.print(Text("all component airflow thresholds met",
                               style="green"))
        console.print(fan_telemetry_table(fan_cfg, server_cfg["fan_count"], summary))
        dba_banner = dba_thermal_banner(summary)
        if dba_banner is not None:
            console.print(dba_banner)
        console.print(f" [dim]wall time {summary['wall_time']:.0f}s "
                      f"for {summary['sim_time']:.0f}s simulated | "
                      "VTU files: velocity.vtu / pressure.vtu / zones.vtu[/]")
        if Confirm.ask("Export run summary to text report?", default=True,
                       console=console):
            path = write_report(server_cfg, params, fan_cfg, summary,
                                comp_rows, fails, scene)
            console.print(f" report written -> {path}")
    elif proc.returncode not in (0, None):
        console.print("[red]worker failed; last output:[/]")
        console.print("".join(list(out_tail)[-10:] + list(err_tail)[-10:]))


# ==============================================================================
#  CLI
# ==============================================================================

def _arg(argv, name, default=None):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def main():
    argv = sys.argv[1:]
    config_path = _arg(argv, "--config", CONFIG_FILE_DEFAULT)

    if "--write-config" in argv:
        with open(config_path, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"example config written -> {config_path}")
        return

    if "--worker" in argv:
        args = {
            "config": config_path,
            "profile": _arg(argv, "--profile"),
            "fan": _arg(argv, "--fan") or next(iter(DEFAULT_CONFIG["fans"])),
            "fan_cfm": _arg(argv, "--fan-cfm"),
            "fan_mmh2o": _arg(argv, "--fan-mmh2o"),
            "sim_time": float(_arg(argv, "--sim-time", "30")),
            "dt": _arg(argv, "--dt"),
            "mesh": _arg(argv, "--mesh", "coarse"),
            "callback_port": _arg(argv, "--callback-port"),
            "cols": _arg(argv, "--cols"),
            # dual-engine + fan duty + mid-run viz export. worker_main
            # coerces each of these itself, so raw strings/None are fine.
            "engine": _arg(argv, "--engine", "3d"),
            "fan_duty": _arg(argv, "--fan-duty"),
            "viz_every": _arg(argv, "--viz-every"),
            # energy equation gate; thermal_enabled() validates the value
            # and defaults to off, so a plain run stays pre-thermal.
            "thermal": _arg(argv, "--thermal"),
            # acoustic ceiling: combined free-field dBA for the whole fan
            # wall. worker_main validates and clamps the duty to it.
            "dba_target": _arg(argv, "--dba-target"),
        }
        worker_main(args)     # config validation happens inside (rank 0)
        return

    # self-bootstrap the UI dependencies so the run command needs no shell
    # wrapper (nested `bash -lc "..."` quoting broke for some frontends,
    # dropping users into a bare python REPL)
    if ((not HAVE_RICH or not HAVE_PSUTIL)
            and os.environ.get("CHASSIS_CFD_BOOTSTRAP") != "1"):
        missing = [m for m, have in (("rich", HAVE_RICH),
                                     ("psutil", HAVE_PSUTIL)) if not have]
        print(f" [setup] installing launcher UI dependencies "
              f"({', '.join(missing)})...", flush=True)   # flush: execv next
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q"]
                           + missing)
        if r.returncode == 0:
            os.environ["CHASSIS_CFD_BOOTSTRAP"] = "1"
            os.execv(sys.executable, [sys.executable] + sys.argv)
    if not HAVE_RICH:
        print("could not install 'rich' (pip install rich); or run the "
              "solver directly, e.g.\n"
              "  mpiexec -n 4 python3 chassis_cfd.py --worker "
              "--profile 6029U --sim-time 10")
        return
    # psutil is optional: without it the dashboard just drops the SYSTEM
    # widget, so a failed install is not fatal
    launcher_main(config_path)


if __name__ == "__main__":
    main()
