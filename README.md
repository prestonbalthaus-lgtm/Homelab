# 🖥️ The Infrastructure & Homelab Repository

Welcome to my central infrastructure repository. This monorepo houses the configuration, code, and documentation for my enterprise-grade homelab, network operations, and high-performance computing clusters. 

The goal of this infrastructure isn't just to tinker—it's built to replicate a modern data center environment. It serves as the physical and logical backbone for heavy research, enterprise routing, and the operational foundation for non-profit supercomputing access.

---

## 🚀 Featured Project: ASCIISTREAM (Terminal CFD)

![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white) ![CFD FEniCS dolfinx](https://img.shields.io/badge/CFD-FEniCS%20dolfinx-red?style=flat-square) ![MPI Multi-Core](https://img.shields.io/badge/MPI-Multi--Core-green?style=flat-square) ![TUI Rich](https://img.shields.io/badge/TUI-Rich-purple?style=flat-square) ![Arch x86_64 | ARM64](https://img.shields.io/badge/Arch-x86__64%20%7C%20ARM64-blue?style=flat-square)

**ASCIISTREAM** is a custom-built, terminal-native Computational Fluid Dynamics (CFD) engine. It is designed to model server chassis airflow, calculate pressure gradients, and render 24-bit ANSI color and 3D wireframe fluid dynamics directly in the command line using MPI multi-processing.

<!-- HTML video tag natively supports WebM in GitHub Markdown -->
[Screencast_20260809_230151.webm](https://github.com/user-attachments/assets/56ef9a9c-fcfe-46d0-9120-84a0a479dad5)
> *Watch ASCIISTREAM dynamically calculate and render airflow through a 2U server chassis in real-time.*

**Key Features:**
*   **DOLFINx/FEniCS Engine:** Solves Navier-Stokes momentum and continuity equations.
*   **MPI Multi-Threading:** Distributes the math across hardware cores for rapid processing.
*   **Dynamic Configurations:** Swap out server geometries (1U, 2U, specific drive bay layouts) and fan curves on the fly via JSON.
*   **Hardware Telemetry:** Built-in alerts for acoustic noise, power draw, and thermal choking.

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
