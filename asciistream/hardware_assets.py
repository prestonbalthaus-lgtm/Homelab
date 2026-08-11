"""Per-profile 3-D hardware geometry for the ASCIISTREAM host viewers.

Both renderers - the live pop-out (viewer_sidecar.py) and the headless
snapshot (visualize.py) - draw their hardware-boundary layer through ONE
entry point here, `hardware_boundary_layers(watch_dir, manifest)`, so the
two views cannot drift apart. The layer comes from the first source that
resolves:

  1. A user-supplied CAD asset, looked up per profile (see ASSET SEARCH
     ORDER below). glTF binary/.gltf, including Draco-compressed
     primitives via DracoPy.
  2. A procedural chassis built from the manifest's `geometry` block:
     one box per component plus the chassis shell outline. This is the
     normal case - the repo ships no CAD assets.

The procedural boxes COMPLEMENT the zone-tag hardware layer (the grey
cells thresholded out of zones*.pvtu), they do not replace it:

  * "solid" components (RAM banks, PCIe cards, risers, PSU bodies) are
    VOIDS in the solver mesh - the zone layer cannot show them at all.
    They are drawn as filled translucent boxes, filling exactly the
    holes the mesh cut out.
  * "porous" components (drive cage, CPU sinks) DO exist as tagged
    cells, already painted grey by the zone layer. They are drawn as
    wireframe outlines only, framing the jagged tetrahedral threshold
    surface with the crisp true box instead of double-painting it.

ASSET SEARCH ORDER (first hit wins; only the assets/ subdirectory of the
watch dir is consulted - a loose *.glb at the watch-dir root is
deliberately IGNORED so a stray download can never silently become the
chassis model):

  1. assets/<profile>.glb, then assets/<profile>.gltf - <profile> is the
     server profile key (e.g. "6029U"), sanitised to [A-Za-z0-9._-]
     (other characters -> "_"). The profile is taken from
     manifest["profile"] when present; today's manifests do not carry it,
     so it is DERIVED by matching manifest["geometry"]["dims"] against
     the chassis dims of every profile in server_configs.json next to
     the manifest - all shipped profiles have distinct dims, and an
     ambiguous or failed match simply resolves no profile.
  2. assets/default.glb, then assets/default.gltf - profile-agnostic.
  3. Nothing found -> procedural fallback (above).

UNITS AND PLACEMENT for imported assets: the chassis lives in
[0,W]x[0,H]x[0,L] metres (z front->back, y height). An asset whose
bounding box already sits inside that box (within 5 % of the chassis
diagonal) is treated as authored in chassis metres and left untouched.
Anything else is fitted: uniformly scaled (never anisotropically - that
would distort the model) so its bounding box just fits inside the
chassis, then translated so the two box centres coincide, and the
applied transform is logged. A manifest without usable dims gives no
scale reference; the asset is then drawn as-is behind ONE warning line.
A silently misplaced mesh is worse than no mesh.

FAILURE POLICY: nothing here ever raises into a render loop.
`hardware_boundary_layers` returns [] or falls back to the procedural
layer; a broken asset costs one clear log line (never a traceback).

This module imports only the stdlib at module level - numpy, pyvista,
trimesh and DracoPy are lazy - so the dormant sidecar can import it for
free.
"""

import json
import logging
import os
import re
import struct

ASSET_DIR = "assets"
ASSET_EXTS = (".glb", ".gltf")
CONFIG_NAME = "server_configs.json"
DIMS_TOL = 1e-6           # [m] per-axis tolerance for the dims->profile match
FIT_SLACK_FRAC = 0.05     # "already in chassis metres" slack, x chassis diag
DRACO_EXT = "KHR_draco_mesh_compression"

log = logging.getLogger("hardware_assets")


# ------------------------------------------------------------------------------
#  Profile resolution + asset lookup
# ------------------------------------------------------------------------------

def _asset_stem(profile):
    """Filesystem-safe file stem for a profile name; None if nothing
    safe remains. Guards the lookup against path separators and dot
    names - a profile string can come from a hand-edited manifest."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(profile)).lstrip(".")
    return stem or None


def _geometry_dims(geom):
    """(W, H, L) from a manifest geometry block, or None when absent,
    malformed or non-positive."""
    if not isinstance(geom, dict):
        return None
    try:
        W, H, L = (float(v) for v in geom["dims"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (W > 0 and H > 0 and L > 0):
        return None
    return (W, H, L)


def derive_profile(watch_dir, man):
    """The server-profile key to look assets up under, or None.

    manifest["profile"] wins when a future solver publishes it. Today's
    manifests carry only the geometry block, so the profile is derived
    from it: manifest["geometry"]["dims"] is (chassis_width,
    chassis_height, chassis_length) verbatim, and every profile in
    server_configs.json has distinct dims - a UNIQUE match (within
    DIMS_TOL per axis) names the profile. Zero or several matches, a
    missing/unreadable config, or no dims resolve to None (asset lookup
    then falls through to assets/default.*). Never raises."""
    if not isinstance(man, dict):
        return None
    profile = man.get("profile")
    if isinstance(profile, str) and profile.strip():
        return profile
    dims = _geometry_dims(man.get("geometry"))
    if dims is None:
        return None
    try:
        with open(os.path.join(watch_dir, CONFIG_NAME)) as f:
            servers = json.load(f).get("servers") or {}
    except (OSError, ValueError):
        return None
    hits = []
    for name, s in servers.items():
        try:
            cand = (float(s["chassis_width"]), float(s["chassis_height"]),
                    float(s["chassis_length"]))
        except (KeyError, TypeError, ValueError):
            continue
        if all(abs(a - b) <= DIMS_TOL for a, b in zip(dims, cand)):
            hits.append(name)
    return hits[0] if len(hits) == 1 else None


def find_hardware_asset(watch_dir, profile):
    """Path of the CAD asset to draw, or None. Search order is the
    module docstring's: assets/<profile>.{glb,gltf}, then
    assets/default.{glb,gltf}. Only assets/ is consulted."""
    stems = []
    if profile:
        stem = _asset_stem(profile)
        if stem:
            stems.append(stem)
    stems.append("default")
    for stem in stems:
        for ext in ASSET_EXTS:
            path = os.path.join(watch_dir, ASSET_DIR, stem + ext)
            if os.path.isfile(path):
                return path
    return None


# ------------------------------------------------------------------------------
#  glTF / Draco decoding
# ------------------------------------------------------------------------------

def _glb_chunks(path):
    """(gltf_json_dict, bin_bytes_or_None) from a .glb container.
    Raises ValueError on anything that is not a well-formed GLB."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("not a GLB container")
    _ver, length = struct.unpack_from("<II", data, 4)
    length = min(length, len(data))
    off, gltf, blob = 12, None, None
    while off + 8 <= length:
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        chunk = data[off:off + clen]
        if len(chunk) < clen:
            raise ValueError("truncated GLB chunk")
        if ctype == 0x4E4F534A and gltf is None:          # 'JSON'
            gltf = json.loads(chunk)
        elif ctype == 0x004E4942 and blob is None:        # 'BIN\0'
            blob = chunk
        off += clen + (-clen % 4)      # tolerate unpadded writers
    if not isinstance(gltf, dict):
        raise ValueError("GLB has no JSON chunk")
    return gltf, blob


def _declares_draco(path):
    """True when the glTF/GLB file lists the Draco extension. Cheap
    (JSON only, no geometry decode); False on any parse problem."""
    try:
        if path.lower().endswith(".glb"):
            gltf, _ = _glb_chunks(path)
        else:
            with open(path) as f:
                gltf = json.load(f)
        used = list(gltf.get("extensionsUsed") or []) \
            + list(gltf.get("extensionsRequired") or [])
        return DRACO_EXT in used
    except Exception:
        return False


def _quat_matrix(q):
    """3x3 rotation from a glTF [x, y, z, w] unit quaternion."""
    import numpy as np
    x, y, z, w = (float(v) for v in q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _node_local(nd):
    """4x4 local transform of a glTF node (matrix, or T*R*S)."""
    import numpy as np
    if "matrix" in nd:                     # column-major in the file
        return np.asarray(nd["matrix"], dtype=float).reshape(4, 4, order="F")
    M = np.eye(4)
    if "scale" in nd:
        s = nd["scale"]
        M[:3, :3] = np.diag([float(s[0]), float(s[1]), float(s[2])])
    if "rotation" in nd:
        M[:3, :] = _quat_matrix(nd["rotation"]) @ M[:3, :]
    if "translation" in nd:
        M[:3, 3] += np.asarray(nd["translation"], dtype=float)
    return M


def _mesh_instances(gltf):
    """[(mesh_index, world_4x4)] for every mesh referenced by the active
    scene graph; every mesh once at identity when there is no scene."""
    import numpy as np
    nodes = gltf.get("nodes") or []
    out = []

    def walk(idx, parent, depth):
        if depth > 256 or not (0 <= idx < len(nodes)):
            return                          # cyclic / out-of-range: skip
        nd = nodes[idx]
        world = parent @ _node_local(nd)
        if isinstance(nd.get("mesh"), int):
            out.append((nd["mesh"], world))
        for child in nd.get("children") or []:
            walk(child, world, depth + 1)

    scenes = gltf.get("scenes") or []
    if scenes:
        sc = gltf.get("scene", 0)
        sc = sc if isinstance(sc, int) and 0 <= sc < len(scenes) else 0
        for root in scenes[sc].get("nodes") or []:
            walk(root, np.eye(4), 0)
    if not out:
        out = [(i, np.eye(4)) for i in range(len(gltf.get("meshes") or []))]
    return out


def _decode_draco_glb(path):
    """(verts, faces) of every Draco-compressed primitive in a .glb,
    node transforms applied, concatenated. trimesh has no Draco backend
    (it returns PLACEHOLDER ZERO vertices for such files - verified
    against trimesh 5.0), so the primitives are decoded here with
    DracoPy straight from the bufferViews the extension names.
    Raises on anything undecodable; the caller turns that into one log
    line."""
    import numpy as np
    import DracoPy
    gltf, blob = _glb_chunks(path)
    if blob is None:
        raise ValueError("Draco GLB has no BIN chunk")
    meshes = gltf.get("meshes") or []
    views = gltf.get("bufferViews") or []
    verts_all, faces_all, base = [], [], 0
    for mesh_idx, world in _mesh_instances(gltf):
        if not (0 <= mesh_idx < len(meshes)):
            continue
        for prim in meshes[mesh_idx].get("primitives") or []:
            dr = (prim.get("extensions") or {}).get(DRACO_EXT)
            if not isinstance(dr, dict):
                continue
            bv = views[dr["bufferView"]]
            start = int(bv.get("byteOffset", 0))
            enc = blob[start:start + int(bv["byteLength"])]
            dec = DracoPy.decode(enc)
            pts = np.asarray(dec.points, dtype=float)
            fcs = np.asarray(dec.faces, dtype=np.int64)
            if pts.size == 0 or fcs.size == 0:
                continue
            pts = pts @ world[:3, :3].T + world[:3, 3]
            verts_all.append(pts)
            faces_all.append(fcs + base)
            base += len(pts)
    if not verts_all:
        raise ValueError("no decodable Draco primitives")
    return np.vstack(verts_all), np.vstack(faces_all)


def load_asset_geometry(path):
    """(verts, faces) numpy arrays for one asset file. Draco-declaring
    files go straight to the DracoPy path - trimesh must never be
    trusted for them (placeholder-zero vertices, see above); everything
    else loads through trimesh. Raises on failure."""
    import numpy as np
    if _declares_draco(path):
        if not path.lower().endswith(".glb"):
            raise ValueError(f"Draco-compressed .gltf with external "
                             f"buffers is not supported - re-export "
                             f"{os.path.basename(path)} as .glb")
        return _decode_draco_glb(path)
    import trimesh
    tm = trimesh.load(path, force="mesh")
    verts = np.asarray(getattr(tm, "vertices", ()), dtype=float)
    faces = np.asarray(getattr(tm, "faces", ()), dtype=np.int64)
    if verts.size == 0 or faces.size == 0:
        raise ValueError("asset has no triangles")
    return verts, faces


# ------------------------------------------------------------------------------
#  Units / placement
# ------------------------------------------------------------------------------

def fit_transform(lo, hi, dims):
    """(scale, offset) placing a mesh with bounding box [lo, hi] into the
    chassis box [0,W]x[0,H]x[0,L]: p' = scale * p + offset.

    Pure floats, unit-testable without pyvista. Returns (1.0, (0,0,0))
    when the box already sits inside the chassis (within FIT_SLACK_FRAC
    of the chassis diagonal) - authored in chassis metres, leave alone.
    Otherwise the scale is UNIFORM (min over axes, so the box just fits;
    anisotropic scaling would distort the model) and the offset centres
    the scaled box on the chassis centre. Raises ValueError for a
    degenerate (zero-extent) box."""
    W, H, L = dims
    slack = FIT_SLACK_FRAC * (W * W + H * H + L * L) ** 0.5
    lo = tuple(float(v) for v in lo)
    hi = tuple(float(v) for v in hi)
    if all(l >= -slack and h <= d + slack
           for l, h, d in zip(lo, hi, (W, H, L))):
        return 1.0, (0.0, 0.0, 0.0)
    extents = [h - l for l, h in zip(lo, hi)]
    usable = [(d, e) for d, e in zip((W, H, L), extents) if e > 1e-9]
    if not usable:
        raise ValueError("degenerate bounding box")
    scale = min(d / e for d, e in usable)
    offset = tuple(0.5 * d - scale * 0.5 * (l + h)
                   for d, l, h in zip((W, H, L), lo, hi))
    return scale, offset


def load_asset_mesh(path, dims):
    """pyvista.PolyData for one asset, scaled/placed per fit_transform,
    or None behind exactly one log line. Never raises."""
    try:
        import numpy as np
        import pyvista as pv
        verts, faces = load_asset_geometry(path)
        if dims is not None:
            scale, offset = fit_transform(verts.min(axis=0),
                                          verts.max(axis=0), dims)
            if scale != 1.0 or any(offset):
                verts = verts * scale + np.asarray(offset)
                log.info("hardware asset %s fitted to chassis dims: "
                         "scale x%.4g, centred", path, scale)
        else:
            log.warning("hardware asset %s drawn untransformed - the "
                        "manifest has no usable geometry dims to fit "
                        "against", path)
        vtk_faces = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64),
                               faces]).ravel()
        mesh = pv.PolyData(verts, vtk_faces)
        log.info("hardware asset: %s (%d triangles)", path, mesh.n_cells)
        return mesh
    except Exception as exc:
        log.warning("could not load hardware asset %s (%s: %s) - falling "
                    "back to the procedural chassis", path,
                    exc.__class__.__name__, exc)
        return None


# ------------------------------------------------------------------------------
#  Procedural chassis from the manifest geometry block
# ------------------------------------------------------------------------------

def procedural_boxes(geom):
    """(shell_bounds, components) from a manifest geometry block - pure
    data, unit-testable without pyvista.

    shell_bounds is (0, W, 0, H, 0, L) in pyvista bounds order, or None
    for a missing/malformed block. components is a list of
    {"name", "label", "kind", "bounds"} dicts, bounds also in pyvista
    order, converted from the manifest's [x0,y0,z0,x1,y1,z1]; malformed
    entries are skipped, inverted boxes dropped."""
    dims = _geometry_dims(geom)
    if dims is None:
        return None, []
    W, H, L = dims
    comps = []
    for c in geom.get("components") or []:
        if not isinstance(c, dict):
            continue
        box = c.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 6:
            continue
        try:
            x0, y0, z0, x1, y1, z1 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if not (x0 < x1 and y0 < y1 and z0 < z1):
            continue
        comps.append({"name": str(c.get("name") or f"component_{len(comps)}"),
                      "label": str(c.get("label") or ""),
                      "kind": "solid" if c.get("kind") == "solid"
                      else "porous",
                      "bounds": (x0, x1, y0, y1, z0, z1)})
    return (0.0, W, 0.0, H, 0.0, L), comps


# ------------------------------------------------------------------------------
#  The one entry point both renderers use
# ------------------------------------------------------------------------------

def hardware_boundary_layers(watch_dir, man):
    """[(mesh, add_mesh_kwargs)] for the hardware-boundary layer of one
    run: the per-profile CAD asset when one resolves, else the
    procedural chassis from the manifest geometry block, else []. The 2d
    engine's planar export lives in its own coordinate frame, so it gets
    no 3-D chassis. Never raises - a failure is one log line and a
    fallback or []."""
    try:
        if not isinstance(man, dict) or man.get("engine") == "2d":
            return []
        geom = man.get("geometry")
        dims = _geometry_dims(geom)
        path = find_hardware_asset(watch_dir, derive_profile(watch_dir, man))
        if path:
            mesh = load_asset_mesh(path, dims)
            if mesh is not None:
                return [(mesh, dict(color="#d0d0d8", opacity=0.5,
                                    name="hardware_asset"))]
            # load_asset_mesh already logged; fall through to procedural
        shell, comps = procedural_boxes(geom)
        if shell is None:
            return []
        import pyvista as pv
        layers = [(pv.Box(bounds=shell),
                   dict(style="wireframe", color="#c8c8d0", line_width=1,
                        name="chassis_shell"))]
        for c in comps:
            box = pv.Box(bounds=c["bounds"])
            if c["kind"] == "solid":
                # solids are voids in the solver mesh - the zone layer
                # cannot show them; a filled box restores them visually
                kwargs = dict(color="#b9b9c2", opacity=0.85,
                              show_edges=True, edge_color="#41414b",
                              name=f"hw_{c['name']}")
            else:
                # porous zones ARE in the zone layer already - only frame
                # them so the two layers complement instead of fighting
                kwargs = dict(style="wireframe", color="#8d8d97",
                              line_width=2, name=f"hw_{c['name']}")
            layers.append((box, kwargs))
        return layers
    except Exception as exc:
        log.warning("hardware boundary layer skipped (%s: %s)",
                    exc.__class__.__name__, exc)
        return []
