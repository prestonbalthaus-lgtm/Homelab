# ASCIISTREAM v0.9.1

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

---

## What's new in this update

A large round: three geometry/solver bug fixes plus four new capabilities.
Everything below is additive — existing runs behave exactly as before
unless you opt into the new modes.

**Bug fixes**

- **Dell C4130 could not be modelled with any PCIe card fitted.** Adding a
  card raised `ValueError: riser 'riser_left' overlaps 'pcie_card_1'`, and
  behind it a second `porous zone 'gpu_1' overlaps solid 'pcie_card_1'`.
  Root cause: card x-placement was hardcoded to span the full chassis
  width, so it collided with anything else in the rear. Cards now honour an
  optional per-profile `pcie_x_band`, C4130's risers moved to the chassis
  sides, and its four GPUs moved out of the rear PCIe slots into the
  **front compute bay** (z = 0.105–0.215 m) where they belong.
- **A diskless server could not be selected.** The geometry layer has
  always supported `drive_bay_count: 0`; only the wizard's drive prompt
  blocked it (`choices=["1","2"]`). It now offers **[0] No drives**.
- **Dell R640 PSUs were 74 mm stubs.** They are now full-depth 0.25 m
  blocks extending forward from the rear plane, narrowed to 62 mm so they
  flank the riser cage the way a real R640 does — and each one now drives a
  **solved 40 mm internal fan momentum source** (≈118 Pa over 0.25 m)
  rather than being a drawn annotation. Opt-in per zone via
  `fan_momentum: true`, so every other profile is untouched.

**New**

- **Acoustic / dBA target mode** — see below. The homelab question:
  *will this box cook itself if I hold it to 45 dBA?*
- **Variable PCIe card count** — the wizard asks how many cards are
  installed (capped by the profile's `pcie_max_slots`) instead of a binary
  GPU/NIC toggle, then how many of those are GPUs for the heat load.
- **Seven enterprise fan profiles** (Dell PowerEdge 1U/2U, HPE ProLiant
  DL360/DL380 Gen10) — **class-representative estimates, not vendor data**;
  see the note under *Fans*.
- **Fluid streamtubes** in the 3-D viewers — smooth, seeded by local air
  speed, clipped strictly to the fluid domain.

## Acoustic / dBA target mode

Homelab servers usually live somewhere you can hear them. Give the wizard a
noise ceiling — or pass `--dba-target 45` — and the solver inverts the same
acoustic law the telemetry table already uses
(`dBA = dBA_rated + 50·log₁₀(duty)`, combined across N fans as
`+10·log₁₀(N)`) to find the highest fan duty that stays under your limit,
clamps the fan curve to it, and then tells you whether the airflow you have
left is still enough.

Measured on a Supermicro 6029U with 4× FAN-0118L4 (60.5 dBA rated, so
66.5 dBA at full tilt):

| Ceiling | Permitted duty | Fan operating estimate | Solved q_out |
|---|---|---|---|
| none | 100 % | 129.0 CFM | 74.5 CFM |
| 65 dBA (rack room) | 93.2 % | 120.3 CFM | 69.4 CFM |
| 45 dBA (living room) | **37.1 %** | 47.9 CFM | **27.6 CFM** |

At 45 dBA that chassis no longer holds its exhaust ceiling, and the run
report says so outright:

```
[THERMAL WARNING: 45 dBA noise limit starves the chassis] exhaust 36.9 degC
exceeds the 35.0 degC ceiling (solved energy equation). Raise the dBA limit,
cut the heat load, or accept throttling.
```

Combine it with `--thermal on` for a hot-spot-aware answer; without the
energy equation the verdict falls back to the bulk balance and says so.

**Two honest limits.** The 50·log₁₀ law has **no noise floor**, so the model
will happily "meet" a 30 dBA target at a duty a real fan cannot reach —
treat very low ceilings as optimistic. And fans with no rated dBA
(including custom fans) cannot be solved for at all; the mode is skipped
with a reason rather than guessing.

## Features

- **Live ASCII dashboard** — 2-D mid-plane streaklines (`.` stagnant → `~`
  slow → `-` moderate → `*` fast, green = healthy flow, red = dead zone),
  labelled geometry (`[ CPU 1 ]`, `[ RAM ]`, `[ PCIe ]`, `[ RISER ]`,
  `[ DRIVES ]`, `[ FAN WALL ]` — `[ PCIe ]` only when cards are fitted),
  front/rear cross-section panes and a full-width btop-style **CFD
  WORKERS** strip — braille history graphs and meters (psutil) scoped to
  the solver's own processes only: summed USS memory and CPU normalised
  to their affinity pool, never global system telemetry.
- **Pop-out 3-D viewer (PyVista/Qt)** — press `p` in the dashboard and a
  native, mouse-rotatable PyVista window opens **on the host**, next to
  the container: the chassis hardware drawn from the solver's per-cell
  zone tags, the flow coloured by air speed on the classic CFD rainbow,
  live-refreshed as the solve streams and switched to the final full
  export when the run completes. Provisioned once with
  `./setup_host_viewer.sh`; entirely optional — without it `p` prints a
  one-line reason and everything else works as before.
- **Dual engine — fast 2-D planar / heavy 3-D volumetric** — chosen in the
  wizard or with `--engine`. The 2-D engine is a genuine planar
  formulation on the mid-height slice (~13× fewer cells, ~26× quicker),
  not a cheaper render of the 3-D solve; the engine used is recorded in
  every run report along with its over-prediction caveat.
- **Energy equation (ΔT)** — `--thermal on` (or the wizard prompt) solves
  real temperature transport instead of a bulk balance: the system wattage
  becomes volumetric heat sources on the CPU/GPU/optics regions, and the
  run reports the solved exhaust temperature, the peak hot spot, and an
  energy audit confirming the injected watts match the configured load.
  A `temperature` field joins the VTU and viewer exports.
- **Fan Affinity Laws** — `--fan-duty` scales the quadratic fan curve by
  RPM fraction (flow ∝ N, pressure ∝ N², power ∝ N³, dBA ≈ +50·log₁₀ N)
  before the operating point, replacing the old fixed 100 %-duty
  assumption; the acoustics/power table follows the chosen duty.
- **3-D spatial labels** — the pop-out scene is annotated in place:
  `FRONT (Intake / Drives)`, `BACK (Exhaust / PSUs)`, `Fan Wall`,
  `CPU 1`/`CPU 2`, `RAM`, and the rest of the hardware, using the same
  labels the ASCII renderer stamps so the two views cannot disagree.
- **Optional CAD chassis assets** — drop a `.glb`/`.gltf` (Draco
  compression supported) at `assets/<profile>.glb` and the viewer uses it
  as the chassis boundary; with no asset — the normal case, none ship —
  it builds the chassis procedurally from the solver's own geometry. See
  `hardware_assets.py` for the search order and the scale/fit rule.
- **Cluster tooling (`hpc/`)** — an Apptainer recipe and a Slurm `.sbatch`
  generator for multi-node runs over InfiniBand. **Untested against real
  HPC hardware** — see `hpc/README.md` for the itemised assumptions.
- **ASCII 3-D chassis view** — the CAD-style isometric projection of the
  physical chassis (component boxes extruded in ASCII, top faces coloured
  by local air speed, the fan wall and PSU fans picked out in gold):
  press `v` (or `2`/`3`) to swap it into the main pane during the run; it
  is printed after every run and embedded in the text report — raster
  images cannot live in a `.txt` file — and it is the 3-D view for
  remote/SSH-only sessions, where no host window can open.
- **10 hardware profiles + custom** — rack servers, a 4×GPU node, two
  32×QSFP switches, a 6RU aggregation router and an ATX mid-tower, all
  generated parametrically from `server_configs.json` (no hardcoded
  geometry), the rack chassis complete with rear PSU banks carrying their
  own 40 mm fans; *Custom Server Configuration* in the wizard appends a
  generic-2U template under any new name for you to shape.
- **Hardware prompts** — every run asks drive type (2.5″ NVMe/SAS vs 3.5″
  HDD → drive-cage impedance), total system wattage, ambient intake and
  desired exhaust temperature, GPU count + wattage (meshed as PCIe cards,
  watts joining the heat load) and a NIC slot. **The answers own the PCIe
  population:** the card count is exactly GPUs + NIC, so declining both
  leaves the slots empty — open air, with only the static riser cages
  standing in the flow — rather than falling back to the profile's
  default card count. All runtime overrides —
  `server_configs.json` on disk is never edited by them.
- **Fan library + custom fan creator** — real 80 mm server fans and generic
  40/60/120 mm classes, or enter any max-CFM / max-mmH₂O pair in the wizard.
- **Mesh presets with RAM guidance** — coarse → ultra, per-profile element
  sizes, an estimated cell count and a MemTotal safeguard that asks before
  letting you exceed the machine — except `ultra`, which under 32 GB of
  RAM deliberately halts with a `MemoryError` (see the table note). A
  fifth **custom** option takes any element size down to the 0.5 mm floor
  for machines with the RAM to match.
- **IT telemetry** — post-run airflow checks per component (CPU / GPU /
  optics) with `[THERMAL WARNING]` banners, a fan acoustics/power table, and
  an exportable plain-text run report.
- **Real output files** — a final `velocity` / `pressure` / `zones` VTU
  snapshot you can open in ParaView (plus periodic `viz_step_*` exports
  while the host viewer is attached — see Outputs).

## Requirements

| What | Detail |
|---|---|
| OS | Linux or macOS with **Podman** (preferred) or **Docker** |
| CPU | x86-64 **and** ARM64 (Apple M-series, Graviton) — the image publishes both `linux/amd64` and `linux/arm64`, so pulls run natively on either |
| Terminal | 100+ columns recommended; 24-bit colour (any modern emulator) |
| 3-D viewer (optional) | a desktop session on the machine running `./run.sh` plus Homebrew CPython 3.12 for `./setup_host_viewer.sh` — remote/SSH-only runs skip it and keep the ASCII isometric view |
| Disk | ~2 GB for the container image, a few MB per run for VTU output |
| RAM | depends on mesh preset — see table below |

Everything else ships in the container: the code is written and tested
against **dolfinx 0.11.0** (the `dolfinx/dolfinx:stable` tag as of
Aug 2026 — note `:stable` is a moving tag), which also provides gmsh,
Open MPI, PETSc and numpy. The launcher pip-installs `rich` and `psutil`
into the container on first start; `psutil` is optional (without it the
dashboard just drops the CFD WORKERS telemetry strip). The pop-out 3-D
viewer is the one component that lives on the **host** instead — a GUI
cannot cross the container boundary — in its own `.venv-viewer/`
(`./setup_host_viewer.sh`, once).

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
the soft RAM warning + confirmation instead. The **Custom element size**
wizard option (or a numeric `--mesh`, e.g. `--mesh 0.8`) accepts anything
down to **0.5 mm**; RAM need is estimated at ~5 KB/cell before you commit.

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
4. **MPI ranks** — any integer, no cap (default = this machine's hardware
   threads); oversubscription supported
5. **Simulated time span** and **time step dt** (0.001–2.0 s, default 0.1)
6. **Mesh resolution** — the four presets with the RAM guidance above, or
   **Custom element size** down to 0.5 mm; a MemTotal check asks to
   confirm oversized choices, while the `ultra` preset on a sub-32 GB
   machine halts hard (table note above)

Then the launcher spawns the MPI worker pool and the live dashboard runs
until the simulated time is reached (`Ctrl+C` stops both). During the run
`v` (or `2`/`3`) toggles the main pane to the ASCII isometric chassis
view and `p` pops out the interactive PyVista window (host viewer
provisioned). After the run: telemetry tables, thermal warnings, the
final ASCII 3-D chassis view, and an optional timestamped report
(`cfd_report_<profile>_<timestamp>.txt`).

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

## The pop-out 3-D viewer (PyVista)

Everything ASCIISTREAM computes runs inside the dolfinx container — but a
GUI cannot cross the container boundary, so the interactive 3-D window is
a **host-side sidecar** (`viewer_sidecar.py`) that talks to the
containerized TUI through files in the shared work directory:

1. provision the host venv once: `./setup_host_viewer.sh` (Homebrew
   CPython 3.12; installs vtk/pyvista/pyvistaqt/PyQt5 into
   `.venv-viewer/`)
2. `./run.sh` then starts the sidecar automatically next to the
   container (log: `${TMPDIR:-/tmp}/asciistream-viewer.log`). It stays
   **dormant** — no window, near-zero cost — and drops a
   `.asciistream_viewer_ready` marker in the work dir, which tells the
   launcher to enable the solver's periodic mid-run field export
3. press **`p`** in the live dashboard: the TUI writes a trigger file,
   the sidecar answers by opening a native PyVista/Qt window — chassis
   hardware built from the solver's per-cell `zone` tags, flow coloured
   by `|u|` on the classic blue→cyan→green→yellow→red CFD palette —
   rotatable/zoomable with the usual PyVista mouse controls
4. while the solve runs the window refreshes itself from
   `viz_manifest.json`, the atomically-replaced manifest naming each
   export's datasets (`viz_step_NNNNNN/` directories; only the two
   newest are kept); when the run completes it switches to the final
   full-resolution export
5. closing the window returns the sidecar to dormant — `p` re-opens it;
   the sidecar exits with `run.sh` (including `Ctrl+C`)
6. optionally, drop a `.glb`/`.gltf` hardware-boundary model (Draco
   compression supported) into the work directory and the viewer
   overlays it on the chassis

The viewer never runs inside the container and structurally cannot slow
the MPI solve — separate process, separate interpreter, separate OS
namespace; the solver's only extra cost is the periodic export, which is
enabled solely while a sidecar is attached. `ASCIISTREAM_VIEWER=0
./run.sh` opts out entirely. Without the sidecar (no venv, opt-out, or a
remote/SSH-only session with no desktop) `p` prints a one-line reason
and `v` still gives the ASCII isometric 3-D view, which also prints
post-run and embeds in the text report.

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
`--dt DT` (default 0.1), `--mesh coarse|medium|fine|ultra|<mm>` (default
coarse; a number is a literal element size in millimetres, e.g.
`--mesh 0.8`), `--config PATH`. Custom fan: `--fan custom --fan-cfm 95
--fan-mmh2o 38`.

| Flag | Default | What it does |
|---|---|---|
| `--engine 2d\|3d` | `3d` | 2-D solves only the mid-height slice: ~13× fewer cells and ~26× quicker, but it models no floor/ceiling friction so it over-predicts through-flow (+5 % near steady state on 6029U/coarse, more during the early transient). Explore in 2d, confirm in 3d. |
| `--fan-duty F` | `1.0` | Fraction of rated RPM. Fan Affinity Laws applied before the operating point: flow ∝ N, pressure ∝ N², shaft power ∝ N³, dBA ≈ +50·log₁₀(N/N_rated). |
| `--thermal on\|off` | `off` | Solve the energy equation (see **Physics**). Off is byte-identical to the pre-thermal solver. |
| `--viz-every N` | `0` (off) | Export a field snapshot every N steps for the host viewer. The launcher sets this automatically when the viewer sidecar is attached. |
| `--dba-target D` | none | Acoustic ceiling: combined free-field dBA for the whole fan wall. Caps fan duty to the loudest setting that stays under it (the quieter of this and `--fan-duty` wins). Unavailable for fans with no rated dBA. |

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
| `dell-1u-std-40mm` | Dell PowerEdge 1U standard 40 mm *(est.)* | 19 CFM | 65.0 mmH₂O |
| `dell-1u-hp-40mm` | Dell PowerEdge 1U high-perf 40 mm dual-rotor *(est.)* | 26 CFM | 120.0 mmH₂O |
| `dell-2u-std-60mm` | Dell PowerEdge 2U standard 60 mm *(est.)* | 40 CFM | 28.0 mmH₂O |
| `dell-2u-hp-60mm` | Dell PowerEdge 2U high-perf 60 mm *(est.)* | 55 CFM | 45.0 mmH₂O |
| `hpe-dl360g10-hp-40mm` | HPE DL360 Gen10 high-perf (875284-001 class) *(est.)* | 24 CFM | 110.0 mmH₂O |
| `hpe-dl380g10-std-60mm` | HPE DL380 Gen10 standard module *(est.)* | 42 CFM | 30.0 mmH₂O |
| `hpe-dl380g10-hp-60mm` | HPE DL380 Gen10 high-perf module *(est.)* | 58 CFM | 48.0 mmH₂O |

**The seven entries marked *(est.)* are class-representative engineering
estimates, NOT vendor data.** Dell and HPE do not publish curves for these
OEM fans, and none of these numbers were sourced from a datasheet — the
part reference identifies which *class* the entry models, not a verified
spec. They are internally consistent with each other and with the physics
(40 mm dual-rotor: high static pressure, low flow, high RPM; 60 mm: more
flow, less static), and are fine for comparing configurations — but do not
size a real machine from them. The same caveat has always applied to the
`generic-*` entries.

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

- One **final snapshot** of `velocity` / `pressure` / `zones` (not a time
  series): numbered `.pvtu` piece sets from the MPI ranks plus
  rank-piece `.vtu` files. Note the bare `velocity.vtu` /
  `pressure.vtu` / `zones.vtu` are PVD-style collection *indexes* — they
  open in ParaView but carry no data themselves; load the `.pvtu` (or
  follow `viz_manifest.json`, which names the exact data-carrying file
  per field).
- While the host viewer sidecar is attached: periodic mid-run
  `viz_step_NNNNNN/` exports plus the atomically-updated
  `viz_manifest.json` (only the two newest step directories are kept;
  `"done": true` marks the final export). These feed the pop-out viewer
  and the two helper scripts below.
- `visualize.py` — headless PNG snapshot of the newest export
  (`.venv-viewer/bin/python visualize.py`); `convert.py` — merges the
  rank pieces into single `*_clean.vtu` files, one per field. Both are
  manifest-driven and run under the host viewer venv.
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
eddy viscosity (block-level electronics-cooling practice). The fan plane is
imposed on two coincident boundary copies of a deliberately split mesh — an
interior Dirichlet plane on continuous elements leaks flux.

**Outlet temperature** is the bulk balance T_in + P/(ρ·Q·c_p) unless
`--thermal on` is given, which adds a real **energy equation**: advection–
diffusion of temperature coupled to the solved velocity, backward-Euler with
the velocity lagged to uₙ, P1 elements, eddy diffusivity ν_eff/Pr_t
(Pr_t = 0.9), GMRES + block-Jacobi/ILU (advection makes the operator
nonsymmetric, so the CG+AMG used for the pressure Poisson does not apply).
The profile's wattage becomes volumetric sources on the meshed heat
regions — porous CPU heatsinks and any zone carrying `heat_w` — normalised
by mesh-measured volumes so the injected power is exact. Wizard-fitted GPUs
are meshed as **solid** PCIe cards and therefore have no interior cells, so
their watts go into the 1 cm shell of fluid washing each card; if that shell
is empty the share is folded back into the distributed remainder with a
warning rather than silently vanishing. The run then reports the **solved**
mass-flow-weighted exhaust temperature beside the bulk estimate, a hot-spot
peak, and an energy audit (watts injected vs watts configured).

The two exhaust figures are weighted differently on purpose: the bulk
balance divides by the *net* outlet flux, while the solved mean is weighted
by the *outgoing* flux only (max(u·n, 0)) — the temperature of air actually
leaving. They agree once the flow settles and there is no outlet backflow;
they diverge while q_out is still converging, or when recirculation makes
the outgoing flux exceed the net. Net-flux weighting was tried first and
abandoned: transient shedding in the 2-D wake can nearly cancel the outflow
and produce a sub-inlet, unphysical mean.

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
- **`p` says the host viewer is not attached** — run
  `./setup_host_viewer.sh` once on the host, then restart `./run.sh`
  (the sidecar starts with it). On remote/SSH-only sessions there is no
  desktop for the window — use `v` for the ASCII isometric 3-D view.
  The sidecar's log is at `${TMPDIR:-/tmp}/asciistream-viewer.log`;
  `ASCIISTREAM_VIEWER=0 ./run.sh` disables the sidecar entirely.
- **SELinux (Fedora/RHEL)** — the mount uses `:z` relabelling; if you run
  the container manually, keep that flag.
- **macOS: "VM is not running"** — start the engine's VM first:
  `podman machine start` (or open Docker Desktop), then `./run.sh` again.
