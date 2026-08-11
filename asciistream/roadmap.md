# 🗺️ ASCIISTREAM Roadmap

This document outlines the planned development phases, upcoming features, and architectural leaps for the ASCIISTREAM project. The goal is to evolve the engine from a local terminal utility into a fully distributed, bare-metal high-performance computing simulation tool.

---

## 🟢 Phase 1: Local TUI & Rendering Overhaul (In Progress)
**Objective:** Perfect the single-node, terminal-native experience with high-fidelity graphics and privacy-scoped telemetry.

*   [x] **CFD Core:** Implement FEniCS/dolfinx Navier-Stokes solvers for chassis airflow.
*   [x] **Hardware Profiles:** JSON-driven server configurations (1U, 2U, PSU blockers, fan curves).
*   [ ] **Privacy-First Telemetry:** Isolate `psutil` scraping to only monitor the specific PIDs, USS RAM footprint, and core affinities of the active OpenMPI workers (no global system scraping).
*   [ ] **btop-Style Dashboard:** Overhaul the `rich` UI with horizontal scaling and Unicode Braille (`⣿`, `⣾`) sparklines for localized core/RAM usage.
*   [~] **Sixel 3D Graphics:** ~~Transition from ASCII 3D rendering to hardware-accelerated Sixel graphics using `gnuplot` subprocesses.~~ **Shipped in v0.8, then retired** — superseded by the PyVista pop-out below. Sixel required a capable emulator, an in-container `gnuplot-nox` install and a subprocess per rotation; a real VTK window is interactive natively.
*   [x] **PyVista/Qt pop-out viewer:** `[p]` in the dashboard opens a true interactive 3-D window. Because the whole stack (TUI included) runs inside the dolfinx container, a GUI cannot cross that boundary — so `run.sh` launches `viewer_sidecar.py` on the *host* and the two sides talk through the shared bind mount: the sidecar drops a marker that switches the solver's atomic mid-run export on, `[p]` writes a trigger, and the window live-refreshes from `viz_manifest.json`. Structurally cannot block the OpenMPI solve. The ASCII isometric view remains the renderer for SSH-only sessions and the text report.

---

## 🔵 Phase 1b: Dual-Engine Architecture (Done)
**Objective:** Let a user explore quickly, then confirm at full fidelity.

*   [x] **Fast 2-D planar engine:** a genuine 2-D formulation (`gmsh generate(2)`, P2²/P1, 2-D Darcy–Forchheimer, fan plane as a line) on the mid-height slice, selectable in the wizard beside the 3-D volumetric solve. Measured on 6029U/coarse: 2,158 triangles vs 27,952 tets, ~1 s vs ~5 s. **Caveat, measured:** the slice models no floor/ceiling friction and over-predicts through-flow (127.3 CFM vs 74.5 CFM) — an exploration tool, not a quantitative substitute. Recorded in every run report.
*   [x] **CSG hardware toggles + static risers:** PCIe cards already appeared/vanished with `populated_pcie_slots`; risers are new and *persist* when the cards are gone, so an empty slot still carries its cage impedance.
*   [x] **Fan Affinity Laws:** the quadratic curve is now scaled by a user-set duty (fraction of rated RPM) before the operating-point intersection — flow ~ N, pressure ~ N², shaft power ~ N³, dBA ~ +50·log₁₀(N/N_rated) — replacing the old fixed 100 %-duty assumption. Feeds the acoustics/power table.
*   [x] **First regression suite:** 286 host-side tests (geometry, validation, config, fan laws, TUI smoke, import hygiene), runnable with no container thanks to the lazy heavy imports.

---

## 🟡 Phase 2: True Distributed HPC Integration
**Objective:** Transition the FEniCS/dolfinx engine from a local multi-threaded process to a true distributed-memory parallelized workload, scaling across the 15-node R640 compute cluster.

*   [ ] **The Transport Layer (InfiniBand & RDMA):** 
    *   Configure OpenMPI to utilize the UCX (Unified Communication X) framework. 
    *   Enable automatic detection of MCX-456A NICs to establish direct RDMA connections over the InfiniBand backbone, ensuring zero-copy CPU bypass and microsecond latency between nodes.
*   [ ] **The Execution Environment (Slurm & OpenHPC):** 
    *   Decouple the simulation from local `subprocess.run(["mpiexec"])` calls. 
    *   Implement `.sbatch` generation to submit workloads directly to the cluster queue. 
    *   Leverage PMIx for native Slurm integration, automatically allocating MPI ranks across the dual Platinum 8160s and pinning processes to the correct NUMA domains.
*   [ ] **Containerization Shift (Apptainer):** 
    *   Migrate the runtime from Docker/Podman to Apptainer for cluster workloads. 
    *   Write definition files that natively map the Rocky Linux host's Mellanox OFED drivers, InfiniBand devices, and OpenMPI libraries directly into the container, achieving bare-metal execution speeds.

---

## ⚪ Phase 3: Advanced Physical Modeling (Planned)
**Objective:** Increase the fidelity of the thermodynamic and fluid simulations.

*   [ ] **Thermal Heat Transfer:** Introduce energy equations to model air temperature deltas across CPU and GPU blocks based on user-defined system wattage.
*   [~] **Acoustic Estimation:** dB now scales with the *chosen* fan duty via the affinity law (dBA ~ dBA_rated + 50·log₁₀(N/N_rated)), combined across fans as +10·log₁₀(N_fans), and is shown in the acoustics/power table. Still open: solving for the RPM actually *required* to meet a thermal target, rather than the user picking a duty.
*   [ ] **Automated Chassis Scanning:** Allow users to define a custom chassis name and append a blank template to `server_configs.json` dynamically from the TUI.

---

## 📋 The Backlog / Brain dump
*Use this section to drop unorganized ideas, UI tweaks, or future hardware support notes.*

*   *(Obsolete)* ~~Add an explicit `--fallback` flag for terminal emulators that reject Sixel sequences.~~ — no sixel pipeline to fall back from any more.
*   *(Idea)* Auto-calibrate the 2-D planar engine against a 3-D reference run per profile, so the fast engine can report a corrected through-flow instead of a known over-prediction.
*   *(Idea)* `.glb`/Draco hardware-boundary overlay is implemented in the viewer but ships **unexercised** — no asset exists in the repo to load. Needs a real exported chassis mesh to validate against.
*   *(Idea)* Extract the 2-D/3-D sampling grid contract into a documented schema; the launcher wire protocol is currently frozen by convention rather than by a test.
