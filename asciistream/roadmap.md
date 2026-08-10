# 🗺️ ASCIISTREAM Roadmap

This document outlines the planned development phases, upcoming features, and architectural leaps for the ASCIISTREAM project. The goal is to evolve the engine from a local terminal utility into a fully distributed, bare-metal high-performance computing simulation tool.

---

## 🟢 Phase 1: Local TUI & Rendering Overhaul (In Progress)
**Objective:** Perfect the single-node, terminal-native experience with high-fidelity graphics and privacy-scoped telemetry.

*   [x] **CFD Core:** Implement FEniCS/dolfinx Navier-Stokes solvers for chassis airflow.
*   [x] **Hardware Profiles:** JSON-driven server configurations (1U, 2U, PSU blockers, fan curves).
*   [ ] **Privacy-First Telemetry:** Isolate `psutil` scraping to only monitor the specific PIDs, USS RAM footprint, and core affinities of the active OpenMPI workers (no global system scraping).
*   [ ] **btop-Style Dashboard:** Overhaul the `rich` UI with horizontal scaling and Unicode Braille (`⣿`, `⣾`) sparklines for localized core/RAM usage.
*   [ ] **Sixel 3D Graphics:** Transition from ASCII 3D rendering to hardware-accelerated Sixel graphics using `gnuplot` subprocesses for true physical 3D chassis visualization.

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
*   [ ] **Acoustic Estimation:** Calculate and display estimated dB levels based on required fan RPM to push air through the calculated impedance zones.
*   [ ] **Automated Chassis Scanning:** Allow users to define a custom chassis name and append a blank template to `server_configs.json` dynamically from the TUI.

---

## 📋 The Backlog / Brain dump
*Use this section to drop unorganized ideas, UI tweaks, or future hardware support notes.*

*   *(Idea)* Add an explicit `--fallback` flag for terminal emulators that reject Sixel sequences.
