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
                                         [--streamtubes | --no-streamtubes]

For the interactive, rotatable window press [p] in the live dashboard
instead (viewer_sidecar.py); this script is the scriptable variant.

The streamtube layer (velocity streamlines swept into chassis-scaled
tubes, coloured by |u| on the flow layer's clim) comes from
flow_streamtubes.streamtube_layers - the SAME entry point the live
pop-out uses, so the two renders cannot drift apart. Default ON for 3-D
exports; --no-streamtubes or ASCIISTREAM_STREAMTUBES=0 turns it off (the
flag wins over the environment). 2-D planar exports never get tubes -
the zero-thickness slab keeps its colour map + arrows, behind one log
line. See flow_streamtubes.py for the seeding, integration-scale,
clipping and failure policy.
"""
import argparse
import logging
import sys

import numpy as np
import pyvista as pv

from chassis_cfd import VOL_OPEN
from flow_streamtubes import streamtube_layers, streamtubes_enabled
from hardware_assets import hardware_boundary_layers
from viewer_sidecar import (load_field_mesh, read_manifest, spatial_labels,
                            velocity_magnitude)

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
    ap.add_argument("--streamtubes", action=argparse.BooleanOptionalAction,
                    default=None,
                    help="draw the velocity streamtube layer (default: on "
                         "for 3-D exports; ASCIISTREAM_STREAMTUBES=0 also "
                         "disables it - this flag wins over the "
                         "environment)")
    args = ap.parse_args(argv)
    tubes_on = (args.streamtubes if args.streamtubes is not None
                else streamtubes_enabled())

    # surface hardware_assets' asset-choice / scale-fit / failure lines in
    # this script's console output (the sidecar has its own log config)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(message)s")

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
    mag = velocity_magnitude(flow, vec)
    if mag is None:
        print(f"velocity dataset carries no '{vec}' array", file=sys.stderr)
        return 1
    vmax = float(np.nanmax(mag)) if mag.size else 0.0
    if not np.isfinite(vmax) or vmax <= 0.0:
        vmax = 1.0
    clim = [0.0, vmax]
    diag = flow.length or 1.0          # bounding-box diagonal [m]
    flat = (flow.bounds[5] - flow.bounds[4]) < 1e-9   # 2d planar export

    plotter = pv.Plotter(off_screen=True, window_size=[1920, 1080])
    plotter.set_background("#101014")

    # dense field: speed lives in COLOUR, the convention the pop-out
    # viewer and the TUI colormap already use (a 3-D volume is drawn
    # translucent so the hardware inside stays visible)
    print(f"flow field coloured by |u| (0..{vmax:.2f} m/s)...")
    plotter.add_mesh(flow, scalars="|u|", cmap="jet", clim=clim,
                     opacity=1.0 if flat else 0.35,
                     scalar_bar_args={"title": "|u| [m/s]", "color": "white"})
    plotter.add_mesh(flow.outline(), color="#c8c8d0")

    # direction arrows, sized from data + geometry: scale by |u| with
    # factor GLYPH_FRAC*diag/vmax, so the longest arrow is always
    # GLYPH_FRAC of the chassis diagonal - legible at any |u| range by
    # construction. tolerance thins the seed points on fine meshes.
    try:
        seeds = flow.threshold(GLYPH_MIN_FRAC * vmax, scalars="|u|")
        glyphs = seeds.glyph(orient=vec, scale="|u|",
                             factor=GLYPH_FRAC * diag / vmax,
                             tolerance=0.02)
        if flat:                       # lift arrows off the coplanar
            glyphs = glyphs.translate((0.0, 0.0, 1e-3 * diag))   # surface
        scal = "|u|" if "|u|" in glyphs.point_data else None
        plotter.add_mesh(glyphs, scalars=scal, cmap="jet", clim=clim,
                         show_scalar_bar=False)
        print(f"direction arrows: longest = {GLYPH_FRAC * diag:.3f} m "
              f"({GLYPH_FRAC:.0%} of the {diag:.2f} m chassis diagonal)")
    except Exception as exc:           # colour-only render still stands
        print(f"direction arrows skipped ({exc})", file=sys.stderr)

    # Streamtube layer through the SAME entry point as the live pop-out
    # (flow_streamtubes): |u|-seeded streamlines swept into chassis-
    # scaled tubes, clipped to the fluid domain, coloured on this
    # render's clim. Returns [] behind one log line for 2-D slabs and
    # every failure mode - the colour+arrow render above still stands.
    if tubes_on:
        for tube_mesh, tube_kwargs in streamtube_layers(man, flow, vec,
                                                        clim):
            plotter.add_mesh(tube_mesh, **tube_kwargs)
    else:
        print("streamtube layer disabled (--no-streamtubes / "
              "ASCIISTREAM_STREAMTUBES)")

    zones = load_field_mesh(args.dir, fields, "zones")
    if zones is not None:
        zarr = (fields.get("zones") or {}).get("array", "zone")
        if zarr in zones.cell_data:
            hardware = zones.threshold(VOL_OPEN + 0.5, scalars=zarr)
            if hardware.n_cells:
                print("chassis hardware from the zone tags "
                      f"({hardware.n_cells} cells)...")
                if flat:               # same lift: the 2d zones patches
                    hardware = hardware.translate(   # are coplanar with
                        (0.0, 0.0, 2e-3 * diag))     # the flow surface
                plotter.add_mesh(hardware, color="#9a9aa4", opacity=0.9)

    # Hardware-boundary layer, through the SAME entry point as the live
    # pop-out window (hardware_assets): per-profile CAD asset when one is
    # installed under assets/, else procedural component boxes + chassis
    # shell from the manifest geometry block. [] for 2d exports.
    hw_layers = hardware_boundary_layers(args.dir, man)
    if hw_layers:
        print(f"hardware boundary layer: {len(hw_layers)} mesh(es) "
              "(see hardware_assets.py for the asset search order)")
    for hw_mesh, hw_kwargs in hw_layers:
        plotter.add_mesh(hw_mesh, **hw_kwargs)

    # Same annotations, same source, as the live pop-out window.
    lpts, ltxt = spatial_labels(man.get("geometry"))
    if lpts:
        print(f"spatial labels: {', '.join(ltxt)}")
        plotter.add_point_labels(
            lpts, ltxt, font_size=12, text_color="white",
            shape_color="#101014", shape_opacity=0.55,
            point_color="#ffd479", point_size=6, always_visible=True,
            render_points_as_spheres=True)

    plotter.add_text(
        f"{'final' if man.get('done') else 'mid-run'} export  "
        f"t={man.get('t', 0.0):.2f}s  step {man.get('step', '?')}"
        f"/{man.get('steps', '?')}  engine={man.get('engine', '?')}",
        position="upper_left", font_size=10, color="white")
    if flat:
        plotter.view_xy()              # planar slice: face-on, no skew
    else:
        plotter.view_isometric()
    print(f"rendering {args.out} ...")
    plotter.screenshot(args.out)
    print(f"done - open {args.out} to view the snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
