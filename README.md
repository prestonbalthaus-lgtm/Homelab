# 🖥️ The Infrastructure & Homelab Repository

Welcome to my central infrastructure repository. This monorepo houses the configuration, code, and documentation for my enterprise-grade homelab, network operations, and high-performance computing clusters. 

The goal of this infrastructure isn't just to tinker—it's built to replicate a modern data center environment. It serves as the physical and logical backbone for heavy research, enterprise routing, and the operational foundation for non-profit supercomputing access.

---

## 🚀 Featured Project: ASCIISTREAM (Terminal CFD) v0.9.2

![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white) ![CFD FEniCS dolfinx](https://img.shields.io/badge/CFD-FEniCS%20dolfinx-red?style=flat-square) ![MPI Multi-Core](https://img.shields.io/badge/MPI-Multi--Core-green?style=flat-square) ![TUI Rich](https://img.shields.io/badge/TUI-Rich-purple?style=flat-square) ![3D PyVista + Qt](https://img.shields.io/badge/3D-PyVista%20%2B%20Qt-orange?style=flat-square) ![Thermal Energy Equation](https://img.shields.io/badge/Thermal-Energy%20Equation-critical?style=flat-square) ![Arch x86_64 | ARM64](https://img.shields.io/badge/Arch-x86__64%20%7C%20ARM64-blue?style=flat-square)

**ASCIISTREAM** is a custom-built, terminal-native Computational Fluid Dynamics (CFD) engine. It models server chassis airflow, solves the momentum, continuity and (optionally) energy equations, and renders 24-bit ANSI colour fluid dynamics — live particle streaklines, btop-style solver telemetry, and a CAD-style ASCII isometric chassis view — directly in the command line using MPI multi-processing. Press `p` and a **native PyVista/Qt window** pops out on the host with the real 3-D field: mouse-rotatable, streamtubes threading the hardware, live-refreshed as the solve streams.

<!-- HTML video tag natively supports WebM in GitHub Markdown -->
<img width="800" height="461" alt="ezgif com-video-to-gif-converter" src="https://github.com/user-attachments/assets/1bb0bd0b-647d-4f59-86ab-2b2131e345fe" />

> *Watch ASCIISTREAM dynamically calculate and render airflow through a 2U server chassis in real-time.*

**Key Features:**
*   **DOLFINx/FEniCS Engine:** Solves Navier-Stokes momentum and continuity equations, plus an optional **energy equation** that maps your wattage onto the CPU/GPU blocks as volumetric heat sources and reports the real solved exhaust temperature and hot spot.
*   **Dual Engine — fast 2-D / heavy 3-D:** A genuine planar solve on the mid-height slice (~13× fewer cells, ~26× quicker) for exploring, and the full 3-D volumetric solve for concluding. The engine used is recorded in every run report.
*   **Acoustic dBA Target Mode:** Tell it your noise limit — 45 dBA for a living room, 55 for an office — and it inverts the fan affinity/noise laws to find the loudest duty that stays under it, then tells you whether the airflow you have left is still enough or whether you have just built a space heater.
*   **MPI Multi-Threading:** Distributes the math across hardware cores for rapid processing — rank count uncapped, sized by you.
*   **Native PyVista/Qt 3-D Pop-Out:** `p` opens a real mouse-rotatable VTK window on the host — chassis hardware from the solver's own cell tags, smooth streamtubes clipped strictly to the fluid domain, spatial labels (`FRONT (Intake / Drives)`, `CPU 1`, `Fan Wall`), live-refreshed as the solve streams. The ASCII isometric view remains the renderer for SSH-only sessions and the text report.
*   **Privacy-Scoped Telemetry:** btop-style Braille dashboards track ONLY the solver's own processes — USS memory and affinity-pool CPU — never global system stats.
*   **Dynamic Configurations:** Swap out server geometries (1U, 2U, specific drive bay layouts) and fan curves on the fly via JSON — or answer the wizard's hardware prompts (drive type or no drives at all, wattage, PCIe card count, GPU wattage, fan duty, noise ceiling, target temps) for per-run what-ifs, and mint entirely new chassis profiles without leaving the terminal.
*   **Hardware Telemetry:** Built-in alerts for acoustic noise, power draw, and thermal choking.
*   **Cluster Tooling:** An Apptainer recipe and Slurm `.sbatch` generator for multi-node InfiniBand runs — *written but not yet validated on real HPC hardware; see `asciistream/hpc/README.md` for the itemised assumptions.*

[View the full ASCIISTREAM source code and documentation here.](./asciistream)

---

## 🌐 The NOC (Network Operations Center)

A 1990s Motif-styled Network Operations Center (NOC) dashboard built for the Screwhead Networks CAN. This project utilizes a Java 8 Applet compiled to WebAssembly (via CheerpJ) to render a dynamic, hardware-accelerated BGP topology map at a strict 17 FPS.

The network core is architected as a highly resilient, physical fiber ring topology with centralized Layer 3 routing. Rather than daisy-chaining outbuildings, everything routes back to the main datacenter hub. 

*   **The Core:** Cisco ASR 1006-X chassis (serving as the primary BNG and DIA terminator).
*   **Routing Protocol:** eBGP with Private ASNs creating a routing circle between independent peers.
*   **External Identity:** Officially routing under **AS402846** with an ARIN-allocated IPv6 block.
*   **Access Layer:** GPON architecture handled by dual redundant FS.com OLT3000-1G units.

---

## 💻 Compute & Storage (The Cluster)

The compute side of the house is strictly dedicated to scientific and mathematical research simulations, leveraging enterprise hardware and Infiniband networking. 

*   **Compute Nodes:** 15x Dell PowerEdge R640s (Dual Platinum 8160s, 192GB RAM, local BOSS-S1 boot).
*   **Storage Nodes:** 4x Dell PowerEdge R740xd chassis packed with PCIe Gen 3 NVMe arrays.
*   **Software Stack:** Rocky Linux, OpenHPC, Slurm, and BeeGFS for parallel storage operations.

---

## 🛠️ Tech Stack & Preferences
*   **Hardware:** Heavy preference for Intel compute architectures and enterprise-grade Dell/Supermicro chassis.
*   **OS/Environment:** Fedora Linux (Workstation) / Rocky Linux (Servers).
*   **Networking:** BGP, OSPF, GPON, Mellanox ConnectX, and raw single-mode fiber.
