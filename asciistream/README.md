# ASCIISTREAM v0.5

**Terminal CFD for server chassis.** Watch the airflow through a Supermicro
2U, a Dell GPU node or an Arista switch as a live ASCII particle field —
rendered by `rich` straight in your terminal, fed by a real transient
Navier–Stokes solve (FEniCSx/dolfinx) running in parallel on your own cores.

One Python file. The entire numerical stack — gmsh parametric meshing,
dolfinx finite elements, Open MPI — lives inside the official
`dolfinx/dolfinx:stable` container, so the host needs nothing but Podman or
Docker.

```
./run.sh
```

## Features

- **Live ASCII dashboard** — 2-D mid-plane streaklines (`.` stagnant → `~`
  slow → `-` moderate → `*` fast, green = healthy flow, red = dead zone),
  labelled geometry (`[ CPU 1 ]`, `[ RAM ]`, `[ PCIe ]`, `[ FAN WALL ]`),
  front/rear cross-section panes and a live `psutil` CPU/RAM widget.
- **3-D isometric chassis view** — press `v` (or `2`/`3`) to flip the main
  pane to a CAD-style isometric projection of the physical chassis: every
  component box extruded in ASCII, top faces colour-filled by the local
  block-averaged air speed (blue = starved → red = full flow), the fan wall
  and the PSU fans picked out in gold; the final view is reprinted after
  the run and embedded in the text report.
- **10 hardware profiles + custom** — rack servers, a 4×GPU node, two
  32×QSFP switches, a 6RU aggregation router and an ATX mid-tower, all
  generated parametrically from `server_configs.json` (no hardcoded
  geometry), the rack chassis complete with rear PSU banks carrying their
  own 40 mm fans; *Custom Server Configuration* in the wizard appends a
  generic-2U template under any new name for you to shape.
- **Hardware prompts** — every run asks drive type (2.5″ NVMe/SAS vs 3.5″
  HDD → drive-cage impedance), total system wattage, ambient intake and
  desired exhaust temperature, GPU count + wattage (meshed as PCIe cards,
  watts joining the heat load) and a NIC slot. All runtime overrides —
  `server_configs.json` on disk is never edited by them.
- **Fan library + custom fan creator** — real 80 mm server fans and generic
  40/60/120 mm classes, or enter any max-CFM / max-mmH₂O pair in the wizard.
- **Mesh presets with RAM guidance** — coarse → ultra, per-profile element
  sizes, an estimated cell count and a MemTotal safeguard that asks before
  letting you exceed the machine — except `ultra`, which under 32 GB of
  RAM deliberately halts with a `MemoryError` (see the table note).
- **IT telemetry** — post-run airflow checks per component (CPU / GPU /
  optics) with `[THERMAL WARNING]` banners, a fan acoustics/power table, and
  an exportable plain-text run report.
- **Real output files** — `velocity` / `pressure` / `zones` VTU series you
  can open in ParaView.

## Requirements

| What | Detail |
|---|---|
| OS | Linux or macOS with **Podman** (preferred) or **Docker** |
| CPU | x86-64 **and** ARM64 (Apple M-series, Graviton) — the image publishes both `linux/amd64` and `linux/arm64`, so pulls run natively on either |
| Terminal | 100+ columns recommended; 24-bit colour (any modern emulator) |
| Disk | ~2 GB for the container image, a few MB per run for VTU output |
| RAM | depends on mesh preset — see table below |

Everything else ships in the container: the code is written and tested
against **dolfinx 0.11.0** (the `dolfinx/dolfinx:stable` tag as of
Aug 2026 — note `:stable` is a moving tag), which also provides gmsh,
Open MPI, PETSc and numpy. The launcher pip-installs `rich` and `psutil`
into the container on first start; `psutil` is optional (without it the
dashboard just drops the SYSTEM widget).

Mesh presets (RAM guidance as shown in the wizard):

| Preset | Element size* | RAM |
|---|---|---|
| Coarse | 15 mm | ~1–2 GB (Ultra-fast) |
| Medium | 8 mm | ~4–6 GB (Balanced) |
| Fine | 4 mm | ~10–14 GB (High-Detail, resolves heatsink fins) |
| Ultra | 2.5 mm | ~18–24 GB (Extreme fidelity, requires 32GB system) |

\* default; each profile tunes its own sizes in `mesh_settings` (e.g. the
6RU Cisco meshes coarse at 21 mm, the 1U Arista at 10 mm). **Coarse and
medium are the validated presets** — fine/ultra mesh correctly but have not
been solved end-to-end here. **`ultra` is hard-gated:** selecting it on a
machine with less than 32 GB of physical RAM raises an unhandled
`MemoryError` ("Insufficient RAM for Ultra mesh") — by design, no confirm
prompt, the launcher just exits with the traceback. The other presets get
the soft RAM warning + confirmation instead.

## Quick start

```bash
git clone https://github.com/prestonbalthaus-lgtm/Homelab.git
cd Homelab/asciistream
./run.sh
```

`run.sh` picks Podman or Docker (override with `ENGINE=docker ./run.sh`),
pulls `docker.io/dolfinx/dolfinx:stable` on first use, mounts the project
directory at `/work` and starts the wizard, which asks for:

1. **Server profile** (table below; Supermicro 6029U is the default) — or
   *Custom Server Configuration*: type a name, and unknown names are
   appended to the JSON as a generic-2U template, then modelled
2. **Hardware configuration** — drive type (2.5″ NVMe/SAS vs 3.5″ HDD),
   total system wattage, ambient intake / desired exhaust °C, GPUs
   (count + watts each) and a NIC slot; Enter keeps each profile default.
   Runtime overrides only — the JSON on disk stays untouched
3. **Fan model** — a library fan or *Custom fan* (enter max CFM + max mmH₂O)
4. **MPI ranks** — up to `max(16, cpu_count)`; oversubscription supported
5. **Simulated time span** and **time step dt** (0.001–2.0 s, default 0.1)
6. **Mesh resolution preset** — with the RAM guidance above; a MemTotal
   check asks to confirm oversized choices, while `ultra` on a sub-32 GB
   machine halts hard (table note above)

Then the launcher spawns the MPI worker pool and the live dashboard runs
until the simulated time is reached (`Ctrl+C` stops both). After the run:
telemetry tables, thermal warnings, the final 3-D chassis view, and an
optional timestamped report (`cfd_report_<profile>_<timestamp>.txt`).

## MPI notes

- **Host needs no MPI.** The launcher and its `mpiexec` worker pool run in
  one container and talk over an in-container localhost socket — that is
  also why the whole thing must stay in a single container.
- **Open MPI quirks are handled automatically.** The launcher runs
  `mpiexec --version`; when it identifies Open MPI (incl. OpenRTE/PRRTE) it
  adds `--oversubscribe` and `--use-hwthread-cpus` (Open MPI undercounts
  usable slots on hybrid P/E-core CPUs), plus `--allow-run-as-root` inside
  root containers. **MPICH** needs none of these and gets none.
- Rank 0 builds the gmsh mesh, the mesh is distributed, all ranks solve; if
  the dashboard socket dies the solve continues and still writes the VTUs.

## Scripted / headless runs

The solver core is runnable without the TUI. Single rank through the
wrapper (any extra `run.sh` arguments are passed to `chassis_cfd.py`):

```bash
./run.sh --worker --profile 6029U --fan arctic-s8038-10k --sim-time 10
```

Multi-rank scripted runs call `mpiexec` inside the container yourself:

```bash
podman run -it --rm -v "$PWD":/work:z -w /work docker.io/dolfinx/dolfinx:stable \
  mpiexec --oversubscribe --use-hwthread-cpus -n 12 \
  python3 chassis_cfd.py --worker --profile 6029U \
    --fan supermicro-fan-0118l4 --sim-time 10 --mesh medium --dt 0.05
```

(The two `--oversubscribe --use-hwthread-cpus` flags are optional and
Open MPI-only — MPICH rejects them. The launcher adds them for you in
interactive mode.)

Worker flags: `--profile K`, `--fan K`, `--sim-time T` (default 30),
`--dt DT` (default 0.1), `--mesh coarse|medium|fine|ultra` (default coarse),
`--config PATH`. Custom fan: `--fan custom --fan-cfm 95 --fan-mmh2o 38`.

## Configuration — `server_configs.json`

Written automatically with the built-in example on first run
(`./run.sh --write-config` regenerates it; `--config PATH` points
elsewhere). The wizard writes to it in exactly one case: *Custom Server
Configuration* appends new profiles; the hardware prompts never touch it.

### Profiles

| Key | Model | Form | Fans | Heat load |
|---|---|---|---|---|
| `6029U` | Supermicro SuperServer 6029U-E1CR4T *(default)* | 2U | 4 | 350 W |
| `R640` | Dell PowerEdge R640 (10-bay) | 1U | 8 | 300 W |
| `R740xd` | Dell PowerEdge R740xd (24-bay) | 2U | 6 | 400 W |
| `DL360` | HPE ProLiant DL360 Gen10 | 1U | 7 | 290 W |
| `DL380` | HPE ProLiant DL380 Gen10 | 2U | 6 | 380 W |
| `C4130` | Dell PowerEdge C4130 (4× passive GPU) | 1U | 8 | 1500 W |
| `A7050X3` | Arista 7050CX3-32S (32× QSFP100) | 1U | 4 | 350 W |
| `SN2700` | Mellanox SN2700 (32× QSFP28) | 1U | 4 | 400 W |
| `ASR1006X` | Cisco ASR 1006-X (6RU aggregation router) | 6U | 6 | 1800 W |
| `ATX-MID` | Generic ATX Mid-Tower (side view)† | MT | 3 | 450 W |

† modelled side-on: `chassis_width` holds the tower *height* — deliberate.

### Fans

| Key | Fan | Max flow | Max static |
|---|---|---|---|
| `supermicro-fan-0118l4` | Supermicro FAN-0118L4 (9.5k rpm) | 100 CFM | 45.0 mmH₂O |
| `arctic-s8038-10k` | ARCTIC S8038-10K | 102 CFM | 51.0 mmH₂O |
| `silverstone-fhs80x` | SilverStone FHS80X (12V) | 83.7 CFM | 50.8 mmH₂O |
| `generic-40mm-dual` | 40×56 mm dual-rotor (1U/switch class) | 22 CFM | 90.0 mmH₂O |
| `generic-60mm` | 60×38 mm high-static (router class) | 38 CFM | 30.0 mmH₂O |
| `generic-120mm` | 120×25 mm case fan (ATX class) | 72 CFM | 3.0 mmH₂O |

The fan curve (quadratic, `P = Pmax·(1−(Q/Qmax)²)`, `fan_count` in parallel)
is intersected with a ζ-based impedance *estimate* only to set the inlet
velocity; the meshed CFD impedance is what actually shapes the flow.

### Geometry schema (per profile)

Standard zones are generated from plain numbers: drive cage (optional —
`drive_bay_count: 0` skips it), `cpu_sockets` porous heatsinks (0 allowed;
`cpu_label` renames them, e.g. `"ASIC"`), DIMM banks sized from
`total_dimm_slots`, `populated_pcie_slots` solid cards, and the fan wall at
`fan_wall_z`. Anything else is a **custom zone**:

```jsonc
"custom_zones": [
  { "name": "optics_cage",             // unique id within the profile
    "box": [x0, y0, z0, x1, y1, z1],   // metres
    "type": "porous",                  // or "solid" (full blockage)
    "zeta": 55.0,                      // porous: loss coeff. over the zone z-length
    "permeability": 1e-7,              // porous: Darcy K [m^2]
    "label": "QSFP CAGE",              // optional, drawn on the dashboard
    "telemetry": "optics",             // optional: cpu | gpu | optics checks
    "fan_rpm": 15000,                  // optional pair: zone carries its own fan
    "fan_size_mm": 40 }                //   (PSUs) — drawn in gold, not solved
]
```

Zones may sit on either side of the fan wall but must not straddle it, and
porous zones must not overlap another zone (touching faces are fine) — the
config is validated at startup. A zone with `fan_rpm`/`fan_size_mm` (the
PSUs) gets the gold fan marker on its rear face in the 3-D view — a drawn
annotation, not a momentum source; the PSU brick itself is the impedance.
Per-profile `requirements`
(`cpu/gpu/optics_min_airflow_ms`) drive the post-run thermal checks, and
`mesh_settings` holds the per-preset element sizes.

## Outputs

- `velocity.vtu` / `pressure.vtu` / `zones.vtu` time series (plus
  `*_pN_*.vtu` / `.pvtu` piece files from the MPI ranks) — open the `.pvtu`
  or `.vtu` in ParaView.
- `cfd_report_<profile>_<timestamp>.txt` — profile, fan, the hardware
  answers used for the run, telemetry, warnings, and ANSI-free copies of
  the dashboard cross-section and the 3-D chassis view.

## Physics, briefly

Transient incompressible Navier–Stokes, incremental pressure-correction
(Chorin/IPCS family): backward-Euler with **semi-implicit convection**
(linearised about uₙ) so dt = 0.1 s stays stable at convective CFL ≫ 1, an
**implicit Darcy–Forchheimer sink** (its ~3 ms time scale would blow up any
explicit treatment) for drive cages, heatsinks and porous custom zones, and
three linear solves per step (GMRES+ILU tentative velocity, CG+BoomerAMG
pressure Poisson, CG+Jacobi correction). Turbulence is a constant effective
eddy viscosity (block-level electronics-cooling practice); the outlet
temperature is the bulk balance T_in + P/(ρ·Q·c_p). The fan plane is imposed
on two coincident boundary copies of a deliberately split mesh — an interior
Dirichlet plane on continuous elements leaks flux.

**Accuracy disclaimer:** chassis outer dimensions follow vendor specs, but
internal layouts, ζ values and heat loads are documented engineering
estimates, and the generic fan curves are class-representative rather than
real part numbers. ASCIISTREAM is a design-exploration and visualisation
tool at engineering accuracy — not a validated thermal-certification tool.

## Troubleshooting

- **`docker: permission denied` on the socket** — use rootless Podman
  (`sudo dnf install podman`, then `./run.sh` — it prefers Podman
  automatically).
- **Wrong/8-bit colours** — `run.sh` sets `COLORTERM=truecolor`; use a
  24-bit-capable terminal for the CFD colormap.
- **Garbled layout** — widen the terminal; 100+ columns recommended.
- **First start is slow** — one-off `pip install rich psutil` inside the
  container, then mesh + JIT compilation before the first frame (the
  launcher waits up to 7 minutes before giving up).
- **`fine`/`ultra` presets** — mind the RAM table; the MemTotal guard asks
  before letting `fine` overcommit, while `ultra` under 32 GB halts with
  `MemoryError` by design.
- **SELinux (Fedora/RHEL)** — the mount uses `:z` relabelling; if you run
  the container manually, keep that flag.
- **macOS: "VM is not running"** — start the engine's VM first:
  `podman machine start` (or open Docker Desktop), then `./run.sh` again.
