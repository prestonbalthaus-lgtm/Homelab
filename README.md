# 🖥️ Retro NOC Dashboard (AS402846)

A 1990s Motif-styled Network Operations Center (NOC) dashboard built for the Screwhead Networks CAN. This project utilizes a Java 8 Applet compiled to WebAssembly (via CheerpJ) to render a dynamic, hardware-accelerated BGP topology map at a strict 17 FPS.

[![Java 8](https://img.shields.io/badge/Java-8-orange.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)]()
[![CheerpJ](https://img.shields.io/badge/Wasm-CheerpJ_3.0-brightgreen.svg)]()
[![Security](https://img.shields.io/badge/Security-mTLS_Enforced-red.svg)]()

![NOC Dashboard Screenshot](./docs/screenshot.png) <!-- Replace with an actual screenshot -->

## ⚡ Quick Start

Bring up the entire local simulation environment, including the Python middleware, LibreNMS mock, and synthetic NetFlow generation.

```bash
# 1. Clone the repository
git clone [https://github.com/yourusername/homelab-web-applet.git](https://github.com/yourusername/homelab-web-applet.git)
cd homelab-web-applet

# 2. Boot the testing suite and bridge APIs
./run.sh up

# 3. Access the dashboard
# Navigate to https://localhost (Requires valid client certificate)
