"""hardware_assets.py: per-profile asset lookup, Draco decoding, the
procedural manifest-geometry fallback, and the unit/placement rule.

The repo ships no CAD assets, so every asset these tests touch is a
GENERATED fixture built into tmp_path at test time: a plain .glb exported
by trimesh, and a Draco-compressed .glb hand-assembled around
DracoPy.encode() (trimesh 5.0 has no Draco backend - it "loads" such
files with placeholder ZERO vertices, which is exactly the silent
failure the loader must route around). Nothing is written where the
loader of a normal run would pick it up.
"""
import json
import logging
import struct
import subprocess
import sys

import numpy as np
import pytest

from conftest import REPO  # noqa: F401  (bootstraps sys.path to the repo)

import hardware_assets as ha

DIMS = [0.43, 0.08, 0.7]         # the 6029U chassis, front(z=0)->back
GEOM = {
    "dims": DIMS,
    "fan_z": 0.25,
    "components": [
        {"name": "drive_array", "label": "DRIVES",
         "box": [0.0, 0.0, 0.05, 0.43, 0.08, 0.2], "kind": "porous"},
        {"name": "cpu1_heatsink", "label": "CPU 1",
         "box": [0.102, 0.005, 0.35, 0.184, 0.056, 0.45], "kind": "porous"},
        {"name": "dimm_bank_0", "label": "RAM",
         "box": [0.02, 0.005, 0.35, 0.089, 0.044, 0.45], "kind": "solid"},
        {"name": "pcie_card_1", "label": "PCIe",
         "box": [0.02, 0.01, 0.55, 0.205, 0.066, 0.65], "kind": "solid"},
    ],
}
MAN = {"type": "viz", "engine": "3d", "geometry": GEOM}


# ------------------------------------------------------------------------------
#  Generated .glb fixtures
# ------------------------------------------------------------------------------

def write_plain_glb(path, extents=(0.4, 0.07, 0.6), offset=(0, 0, 0)):
    """Ordinary (uncompressed) box .glb via trimesh's own exporter."""
    import trimesh
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(offset)
    box.export(str(path))
    return path


def write_draco_glb(path, extents=(0.4, 0.07, 0.6), node_translation=None):
    """Minimal spec-valid .glb whose single primitive is Draco-encoded
    (KHR_draco_mesh_compression), assembled by hand around
    DracoPy.encode(). Optionally puts the mesh under a translated node
    to exercise the scene-graph transform path."""
    import DracoPy
    import trimesh
    box = trimesh.creation.box(extents=extents)
    verts = np.asarray(box.vertices, dtype=np.float32)
    faces = np.asarray(box.faces, dtype=np.uint32)
    draco = DracoPy.encode(verts, faces=faces)
    bin_chunk = draco + b"\x00" * ((-len(draco)) % 4)
    node = {"mesh": 0}
    if node_translation is not None:
        node["translation"] = list(node_translation)
    gltf = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_draco_mesh_compression"],
        "extensionsRequired": ["KHR_draco_mesh_compression"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [node],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0},
            "indices": 1,
            "extensions": {"KHR_draco_mesh_compression": {
                "bufferView": 0, "attributes": {"POSITION": 0}}}}]}],
        "accessors": [
            {"componentType": 5126, "count": int(len(verts)), "type": "VEC3",
             "min": verts.min(0).tolist(), "max": verts.max(0).tolist()},
            {"componentType": 5125, "count": int(faces.size),
             "type": "SCALAR"}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0,
                         "byteLength": len(draco)}],
        "buffers": [{"byteLength": len(bin_chunk)}],
    }
    js = json.dumps(gltf).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_chunk)
    blob = (b"glTF" + struct.pack("<II", 2, total)
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk)
    path.write_bytes(blob)
    return path


def make_watch(tmp_path, geom=GEOM):
    """A watch dir with a manifest; returns (watch_dir, manifest_dict)."""
    man = {"type": "viz", "engine": "3d"}
    if geom is not None:
        man["geometry"] = geom
    (tmp_path / "viz_manifest.json").write_text(json.dumps(man))
    return tmp_path, man


# ------------------------------------------------------------------------------
#  Asset search order
# ------------------------------------------------------------------------------

def test_profile_asset_beats_default(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    write_plain_glb(assets / "6029U.glb")
    write_plain_glb(assets / "default.glb")
    hit = ha.find_hardware_asset(str(tmp_path), "6029U")
    assert hit == str(assets / "6029U.glb")


def test_glb_beats_gltf_for_the_same_stem(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "6029U.gltf").write_text("{}")
    write_plain_glb(assets / "6029U.glb")
    assert ha.find_hardware_asset(str(tmp_path), "6029U").endswith(
        "6029U.glb")


def test_default_used_when_no_profile_asset(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    write_plain_glb(assets / "default.glb")
    hit = ha.find_hardware_asset(str(tmp_path), "6029U")
    assert hit == str(assets / "default.glb")


def test_none_when_assets_dir_missing_or_empty(tmp_path):
    assert ha.find_hardware_asset(str(tmp_path), "6029U") is None
    (tmp_path / "assets").mkdir()
    assert ha.find_hardware_asset(str(tmp_path), "6029U") is None


def test_loose_glb_at_watch_root_is_ignored(tmp_path):
    """The pre-per-profile behaviour (first *.glb anywhere in the watch
    dir) is gone: a stray download must not become the chassis model."""
    write_plain_glb(tmp_path / "random_download.glb")
    assert ha.find_hardware_asset(str(tmp_path), None) is None
    assert ha.hardware_boundary_layers(str(tmp_path), {"engine": "3d"}) == []


def test_profile_name_cannot_escape_the_assets_dir(tmp_path):
    write_plain_glb(tmp_path / "evil.glb")          # outside assets/
    assert ha.find_hardware_asset(str(tmp_path), "../evil") is None
    assert ha.find_hardware_asset(str(tmp_path), "..") is None


# ------------------------------------------------------------------------------
#  Profile derivation
# ------------------------------------------------------------------------------

def _write_config(tmp_path, servers):
    (tmp_path / ha.CONFIG_NAME).write_text(json.dumps({"servers": servers}))


def test_manifest_profile_key_wins(tmp_path):
    # forward-compatible: no config, no geometry needed
    assert ha.derive_profile(str(tmp_path), {"profile": "R640"}) == "R640"


def test_profile_derived_from_unique_dims_match(tmp_path):
    _write_config(tmp_path, {
        "6029U": {"chassis_width": 0.43, "chassis_height": 0.08,
                  "chassis_length": 0.7},
        "R640": {"chassis_width": 0.434, "chassis_height": 0.043,
                 "chassis_length": 0.734}})
    assert ha.derive_profile(str(tmp_path), MAN) == "6029U"


def test_ambiguous_dims_resolve_no_profile(tmp_path):
    twin = {"chassis_width": 0.43, "chassis_height": 0.08,
            "chassis_length": 0.7}
    _write_config(tmp_path, {"A": dict(twin), "B": dict(twin)})
    assert ha.derive_profile(str(tmp_path), MAN) is None


def test_no_config_or_no_geometry_resolve_no_profile(tmp_path):
    assert ha.derive_profile(str(tmp_path), MAN) is None       # no config
    _write_config(tmp_path, {"6029U": {"chassis_width": 0.43,
                                       "chassis_height": 0.08,
                                       "chassis_length": 0.7}})
    assert ha.derive_profile(str(tmp_path), {"engine": "3d"}) is None
    assert ha.derive_profile(str(tmp_path), None) is None


def _dims_index(servers):
    idx = {}
    for name, s in servers.items():
        key = (round(s["chassis_width"], 6), round(s["chassis_height"], 6),
               round(s["chassis_length"], 6))
        idx.setdefault(key, []).append(name)
    return idx


def test_shared_chassis_dims_resolve_to_no_profile_not_a_wrong_one():
    """Chassis dims are NOT unique across the profile library, and cannot
    be made unique: vendors genuinely reuse a chassis across generations
    (R620/R630, R720/R730/R730xd, R740/R7425, R650/R6525, DL360 Gen8/Gen9
    are all real shared bodies). This test used to assert uniqueness; that
    premise died when the library grew from 10 to 39 profiles.

    The invariant that actually matters is that ambiguity is HANDLED:
    `derive_profile` must resolve a shared-dims manifest to None - falling
    through to assets/default.* - rather than silently picking whichever
    profile it happened to iterate first and loading the wrong chassis
    model. The exact route is `manifest["profile"]`, which the solver now
    publishes; dims matching is only the fallback for older manifests."""
    with open(REPO / ha.CONFIG_NAME) as f:
        servers = json.load(f)["servers"]
    shared = {k: v for k, v in _dims_index(servers).items() if len(v) > 1}
    assert shared, ("expected at least one shared-chassis family in the "
                    "library; if this ever fails, the fallback is "
                    "unambiguous again and this test can go back to "
                    "asserting uniqueness")
    for dims, names in shared.items():
        man = {"geometry": {"dims": list(dims)}}
        got = ha.derive_profile(str(REPO), man)
        assert got is None, (
            f"dims {dims} are shared by {sorted(names)} but derive_profile "
            f"returned {got!r} - it must refuse to guess")


def test_unique_dims_still_resolve_exactly(cfg):
    """The fallback must still work for the profiles that ARE unique."""
    with open(REPO / ha.CONFIG_NAME) as f:
        servers = json.load(f)["servers"]
    unique = {k: v[0] for k, v in _dims_index(servers).items()
              if len(v) == 1}
    assert len(unique) >= 20, "expected many unambiguous profiles"
    for dims, name in list(unique.items())[:12]:
        man = {"geometry": {"dims": list(dims)}}
        assert ha.derive_profile(str(REPO), man) == name


def test_explicit_profile_key_beats_ambiguous_dims():
    """A manifest carrying `profile` must win outright, which is how a
    shared-chassis machine gets its own asset at all."""
    with open(REPO / ha.CONFIG_NAME) as f:
        servers = json.load(f)["servers"]
    shared = [v for v in _dims_index(servers).values() if len(v) > 1][0]
    man = {"profile": shared[0], "geometry": {"dims": [0.434, 0.0428, 0.731]}}
    assert ha.derive_profile(str(REPO), man) == shared[0]


# ------------------------------------------------------------------------------
#  Procedural fallback (pure data)
# ------------------------------------------------------------------------------

def test_procedural_boxes_shell_and_conversion():
    shell, comps = ha.procedural_boxes(GEOM)
    assert shell == (0.0, 0.43, 0.0, 0.08, 0.0, 0.7)
    assert [c["name"] for c in comps] == [
        "drive_array", "cpu1_heatsink", "dimm_bank_0", "pcie_card_1"]
    # manifest box [x0,y0,z0,x1,y1,z1] -> pyvista (x0,x1,y0,y1,z0,z1)
    assert comps[0]["bounds"] == (0.0, 0.43, 0.0, 0.08, 0.05, 0.2)
    assert comps[2]["kind"] == "solid"
    assert comps[0]["kind"] == "porous"
    W, H, L = DIMS
    for c in comps:
        x0, x1, y0, y1, z0, z1 = c["bounds"]
        assert 0 <= x0 < x1 <= W and 0 <= y0 < y1 <= H and 0 <= z0 < z1 <= L


@pytest.mark.parametrize("bad", [
    None, {}, {"dims": [0.4, 0.1]}, {"dims": [0.4, -0.1, 0.7]},
    {"dims": ["a", "b", "c"]}])
def test_procedural_boxes_malformed_block(bad):
    shell, comps = ha.procedural_boxes(bad)
    assert shell is None and comps == []


def test_procedural_boxes_skips_malformed_components():
    geom = {"dims": DIMS, "components": [
        "not a dict",
        {"name": "short_box", "box": [0, 0, 0], "kind": "solid"},
        {"name": "inverted", "box": [0.2, 0, 0, 0.1, 0.05, 0.1],
         "kind": "solid"},
        {"name": "nan_box", "box": [0, 0, 0, "x", 0.05, 0.1]},
        {"name": "good", "box": [0.1, 0.0, 0.1, 0.2, 0.05, 0.3],
         "kind": "weird-kind"}]}
    shell, comps = ha.procedural_boxes(geom)
    assert shell is not None
    assert [c["name"] for c in comps] == ["good"]
    assert comps[0]["kind"] == "porous"        # unknown kind -> porous


# ------------------------------------------------------------------------------
#  Units / placement rule
# ------------------------------------------------------------------------------

def test_fit_identity_when_already_in_chassis_metres():
    scale, off = ha.fit_transform((0.01, 0.0, 0.05), (0.42, 0.08, 0.69),
                                  tuple(DIMS))
    assert scale == 1.0 and off == (0.0, 0.0, 0.0)


def test_fit_scales_uniformly_and_centres():
    # a 10 m box (mm-scale CAD exported wrong, say) must come down
    # UNIFORMLY and land centred inside [0,W]x[0,H]x[0,L]
    lo, hi = (-5.0, -5.0, -5.0), (5.0, 5.0, 5.0)
    W, H, L = DIMS
    scale, off = ha.fit_transform(lo, hi, (W, H, L))
    assert scale == pytest.approx(min(W, H, L) / 10.0)
    new_lo = [l * scale + o for l, o in zip(lo, off)]
    new_hi = [h * scale + o for h, o in zip(hi, off)]
    for nl, nh, d in zip(new_lo, new_hi, (W, H, L)):
        assert nl >= -1e-9 and nh <= d + 1e-9
        assert (nl + nh) / 2 == pytest.approx(d / 2)   # centred per axis


def test_fit_rejects_degenerate_box():
    with pytest.raises(ValueError):
        ha.fit_transform((3.0, 3.0, 3.0), (3.0, 3.0, 3.0), tuple(DIMS))


# ------------------------------------------------------------------------------
#  Asset loading (plain + Draco + broken)
# ------------------------------------------------------------------------------

def test_plain_glb_loads(tmp_path):
    path = write_plain_glb(tmp_path / "box.glb", extents=(0.4, 0.07, 0.6))
    verts, faces = ha.load_asset_geometry(str(path))
    assert len(verts) == 8 and len(faces) == 12
    assert np.ptp(verts, axis=0) == pytest.approx([0.4, 0.07, 0.6], rel=1e-5)


def test_draco_glb_decodes_with_real_coordinates(tmp_path):
    """trimesh silently yields all-zero vertices for Draco files; the
    loader must instead decode real coordinates via DracoPy."""
    path = write_draco_glb(tmp_path / "draco.glb", extents=(0.4, 0.07, 0.6))
    verts, faces = ha.load_asset_geometry(str(path))
    assert len(faces) == 12
    assert np.ptp(verts, axis=0) == pytest.approx([0.4, 0.07, 0.6], rel=1e-2)
    assert np.abs(verts).max() > 0.0       # NOT the placeholder zeros


def test_draco_node_transform_is_applied(tmp_path):
    path = write_draco_glb(tmp_path / "moved.glb", extents=(0.2, 0.2, 0.2),
                           node_translation=(1.0, 2.0, 3.0))
    verts, _ = ha.load_asset_geometry(str(path))
    assert verts.mean(axis=0) == pytest.approx([1.0, 2.0, 3.0], abs=1e-2)


def test_draco_mesh_is_fitted_into_the_chassis(tmp_path):
    # authored around the origin -> outside [0,W]x[0,H]x[0,L] -> fitted
    path = write_draco_glb(tmp_path / "draco.glb", extents=(4.0, 0.7, 6.0))
    mesh = ha.load_asset_mesh(str(path), tuple(DIMS))
    assert mesh is not None and mesh.n_cells == 12
    x0, x1, y0, y1, z0, z1 = mesh.bounds
    W, H, L = DIMS
    assert x0 >= -1e-9 and x1 <= W + 1e-9
    assert y0 >= -1e-9 and y1 <= H + 1e-9
    assert z0 >= -1e-9 and z1 <= L + 1e-9


def test_corrupt_asset_is_one_log_line_and_none(tmp_path, caplog):
    path = tmp_path / "junk.glb"
    path.write_bytes(b"glTF" + b"\x99" * 64)
    with caplog.at_level(logging.DEBUG, logger="hardware_assets"):
        mesh = ha.load_asset_mesh(str(path), tuple(DIMS))
    assert mesh is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None        # a line, not a traceback


def test_undecodable_draco_is_one_log_line_and_none(tmp_path, caplog):
    """Declares KHR_draco_mesh_compression but the blob is garbage."""
    path = write_draco_glb(tmp_path / "bad.glb")
    blob = bytearray(path.read_bytes())
    blob[-40:] = b"\xff" * 40                  # corrupt the BIN chunk
    path.write_bytes(bytes(blob))
    with caplog.at_level(logging.DEBUG, logger="hardware_assets"):
        mesh = ha.load_asset_mesh(str(path), tuple(DIMS))
    assert mesh is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1


def test_triangle_free_asset_is_rejected(tmp_path):
    js = json.dumps({"asset": {"version": "2.0"}}).encode()
    js += b" " * ((-len(js)) % 4)
    blob = (b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js))
            + struct.pack("<II", len(js), 0x4E4F534A) + js)
    path = tmp_path / "empty.glb"
    path.write_bytes(blob)
    assert ha.load_asset_mesh(str(path), tuple(DIMS)) is None


# ------------------------------------------------------------------------------
#  hardware_boundary_layers: the entry point both renderers share
# ------------------------------------------------------------------------------

def test_procedural_layers_when_no_asset(tmp_path):
    watch, man = make_watch(tmp_path)
    layers = ha.hardware_boundary_layers(str(watch), man)
    # chassis shell + one box per component
    assert len(layers) == 1 + len(GEOM["components"])
    shell_mesh, shell_kwargs = layers[0]
    assert shell_kwargs["name"] == "chassis_shell"
    assert shell_kwargs["style"] == "wireframe"
    assert shell_mesh.bounds == pytest.approx(
        (0.0, 0.43, 0.0, 0.08, 0.0, 0.7))
    by_name = {kw["name"]: (m, kw) for m, kw in layers[1:]}
    # solids are FILLED (they are voids in the solver mesh, invisible to
    # the zone-tag layer); porous zones are wireframe-only (the zone-tag
    # layer already paints their cells - complement, don't double-paint)
    assert "opacity" in by_name["hw_dimm_bank_0"][1]
    assert by_name["hw_dimm_bank_0"][1].get("style") != "wireframe"
    assert by_name["hw_drive_array"][1]["style"] == "wireframe"
    m, _ = by_name["hw_cpu1_heatsink"]
    assert m.bounds == pytest.approx((0.102, 0.184, 0.005, 0.056,
                                      0.35, 0.45))


def test_asset_layer_replaces_procedural_boxes(tmp_path):
    watch, man = make_watch(tmp_path)
    assets = watch / "assets"
    assets.mkdir()
    write_draco_glb(assets / "default.glb", extents=(4.0, 0.7, 6.0))
    layers = ha.hardware_boundary_layers(str(watch), man)
    assert len(layers) == 1
    mesh, kwargs = layers[0]
    assert kwargs["name"] == "hardware_asset"
    assert mesh.bounds[1] <= DIMS[0] + 1e-9        # fitted into the chassis


def test_broken_asset_falls_back_to_procedural(tmp_path, caplog):
    watch, man = make_watch(tmp_path)
    assets = watch / "assets"
    assets.mkdir()
    (assets / "default.glb").write_bytes(b"not a mesh at all")
    with caplog.at_level(logging.DEBUG, logger="hardware_assets"):
        layers = ha.hardware_boundary_layers(str(watch), man)
    assert len(layers) == 1 + len(GEOM["components"])
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1


def test_2d_engine_and_missing_geometry_yield_no_layers(tmp_path):
    watch, man = make_watch(tmp_path)
    assert ha.hardware_boundary_layers(
        str(watch), dict(man, engine="2d")) == []
    watch2 = tmp_path / "nogeom"
    watch2.mkdir()
    assert ha.hardware_boundary_layers(str(watch2), {"engine": "3d"}) == []
    assert ha.hardware_boundary_layers(str(watch2), None) == []


# ------------------------------------------------------------------------------
#  Dormancy: importing the module must stay near-free
# ------------------------------------------------------------------------------

def test_import_pulls_no_heavy_stack():
    """The sidecar imports hardware_assets while DORMANT (it is spawned
    on every interactive run): numpy/pyvista/vtk/trimesh/DracoPy must
    stay lazy inside functions, exactly like the solver stack in
    chassis_cfd."""
    heavy = ("numpy", "pyvista", "vtk", "trimesh", "DracoPy", "PIL")
    code = (
        "import sys\n"
        "import hardware_assets\n"
        f"heavy = {heavy!r}\n"
        "bad = sorted(m for m in sys.modules"
        " if m.split('.')[0] in heavy)\n"
        "print(','.join(bad))\n"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "", (
        "importing hardware_assets pulled in heavy modules: "
        f"{res.stdout.strip()}")
