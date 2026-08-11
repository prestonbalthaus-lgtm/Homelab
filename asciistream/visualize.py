#!/usr/bin/env python3
"""Headless snapshot of the newest ASCIISTREAM export -> server_airflow.png.

Manifest-driven: reads viz_manifest.json (the solver replaces it
atomically; "done": true marks the final full export) and takes every
dataset path from fields[...]["file"]. The bare velocity.vtu /
pressure.vtu / zones.vtu files at an export root are PVD-style collection
INDEXES - they parse without error but carry no data, so they are never
read here. The solver has no XDMF output; the previous version of this
script read a velocity.xdmf that nothing ever wrote.

Run from the repo/work directory with the host viewer venv
(./setup_host_viewer.sh provisions it):

    .venv-viewer/bin/python visualize.py [--dir DIR] [--out PNG]

For the interactive, rotatable window press [p] in the live dashboard
instead (viewer_sidecar.py); this script is the scriptable variant.
"""
import argparse
import sys

import numpy as np
import pyvista as pv

from chassis_cfd import VOL_OPEN
from viewer_sidecar import load_field_mesh, read_manifest, velocity_magnitude

# Direction-arrow sizing: the LONGEST glyph spans this fraction of the
# chassis bounding-box diagonal, whatever the velocity range. |u| is an
# unbounded physical quantity (a 6029U coarse solve peaks at ~26 m/s on a
# 0.82 m diagonal), so a fixed absolute glyph factor cannot work - the
# scale must come from the data + geometry.
GLYPH_FRAC = 0.05
# Seeds below this fraction of the peak speed get no arrow: at ~0 m/s an
# arrow's orientation is numerical noise and only clutters dead zones.
GLYPH_MIN_FRAC = 0.02


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="render the newest ASCIISTREAM viz export to a PNG")
    ap.add_argument("--dir", default=".",
                    help="work directory holding viz_manifest.json "
                         "(default: current directory)")
    ap.add_argument("--out", default="server_airflow.png",
                    help="output image path (default: server_airflow.png)")
    args = ap.parse_args(argv)

    man = read_manifest(args.dir)
    if man is None:
        print("no readable viz_manifest.json in", args.dir,
              "- run the solver first (./run.sh); the manifest is written "
              "at the end of every run, and mid-run when the host viewer "
              "sidecar is attached", file=sys.stderr)
        return 1
    fields = man.get("fields") or {}

    flow = load_field_mesh(args.dir, fields, "velocity")
    if flow is None:
        print("velocity dataset listed in the manifest is not readable "
              "(mid-run exports rotate - re-run when the solve settles)",
              file=sys.stderr)
        return 1
    vec = (fields.get("velocity") or {}).get("array", "velocity")
    if velocity_magnitude(flow, vec) is None:
        print(f"velocity dataset carries no '{vec}' array", file=sys.stderr)
        return 1

    plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])
    plotter.set_background("#101014")

    print("velocity glyphs over the flow domain...")
    # tolerance thins the glyph seed points so fine meshes stay tractable
    glyphs = flow.glyph(orient=vec, scale=vec, factor=0.02, tolerance=0.01)
    plotter.add_mesh(glyphs, scalars="GlyphScale", cmap="jet",
                     scalar_bar_args={"title": "|u| [m/s]", "color": "white"})
    plotter.add_mesh(flow.outline(), color="#c8c8d0")

    zones = load_field_mesh(args.dir, fields, "zones")
    if zones is not None:
        zarr = (fields.get("zones") or {}).get("array", "zone")
        if zarr in zones.cell_data:
            hardware = zones.threshold(VOL_OPEN + 0.5, scalars=zarr)
            if hardware.n_cells:
                print("chassis hardware from the zone tags "
                      f"({hardware.n_cells} cells)...")
                plotter.add_mesh(hardware, color="#9a9aa4", opacity=0.9)

    plotter.add_text(
        f"{'final' if man.get('done') else 'mid-run'} export  "
        f"t={man.get('t', 0.0):.2f}s  step {man.get('step', '?')}"
        f"/{man.get('steps', '?')}  engine={man.get('engine', '?')}",
        position="upper_left", font_size=10, color="white")
    plotter.view_isometric()
    print(f"rendering {args.out} ...")
    plotter.screenshot(args.out)
    print(f"done - open {args.out} to view the snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
