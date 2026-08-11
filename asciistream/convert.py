#!/usr/bin/env python3
"""Flatten the newest ASCIISTREAM export into single clean .vtu files.

The solver writes distributed .pvtu piece sets (one piece per MPI rank).
ParaView opens those directly, but tools that want ONE self-contained file
per field can use this: it reads each dataset named by viz_manifest.json's
fields[...]["file"] entries - never the bare velocity.vtu / pressure.vtu /
zones.vtu, which are dataless PVD-style collection indexes - merges the
rank pieces and writes velocity_clean.vtu / pressure_clean.vtu /
zones_clean.vtu. (The previous version of this script read a
velocity.xdmf; the solver has no XDMF output, so it never worked. pyvista
replaces meshio here because meshio does not read .pvtu piece sets.)

    .venv-viewer/bin/python convert.py [--dir DIR]
"""
import argparse
import os
import sys

from viewer_sidecar import load_field_mesh, read_manifest


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="merge the newest ASCIISTREAM export into single .vtu "
                    "files per field")
    ap.add_argument("--dir", default=".",
                    help="work directory holding viz_manifest.json "
                         "(default: current directory)")
    args = ap.parse_args(argv)

    man = read_manifest(args.dir)
    if man is None:
        print("no readable viz_manifest.json in", args.dir,
              "- run the solver first (./run.sh)", file=sys.stderr)
        return 1
    fields = man.get("fields") or {}

    wrote = 0
    for name in ("velocity", "pressure", "zones"):
        mesh = load_field_mesh(args.dir, fields, name)
        if mesh is None:
            print(f"  {name}: not in the manifest or not readable - "
                  "skipped", file=sys.stderr)
            continue
        out = os.path.join(args.dir, f"{name}_clean.vtu")
        mesh.save(out)
        print(f"  {name}: {mesh.n_cells:,} cells -> {out}")
        wrote += 1
    if not wrote:
        print("nothing converted", file=sys.stderr)
        return 1
    print("done - single-file VTUs ready for ParaView or any VTK tool")
    return 0


if __name__ == "__main__":
    sys.exit(main())
