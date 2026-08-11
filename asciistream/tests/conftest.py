"""Shared fixtures for the ASCIISTREAM host regression suite.

The suite runs ON THE HOST with no container: chassis_cfd.py imports its
heavy solver dependencies (gmsh/dolfinx/mpi4py/petsc4py) lazily inside
functions, so the geometry/config/renderer layer needs only numpy + rich.
test_import_hygiene.py enforces that invariant - everything else here
depends on it.

Run with the viewer venv (see ../setup_host_viewer.sh):

    .venv-viewer/bin/python -m pytest tests/ -q
"""
import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import chassis_cfd as cc  # noqa: E402  (path bootstrap must run first)

CONFIG_PATH = REPO / "server_configs.json"

# Profile names resolved at collection time so tests parametrize over
# EVERY profile in server_configs.json - a profile added later is covered
# automatically, a profile that breaks build_geometry fails loudly.
with open(CONFIG_PATH) as _f:
    _RAW = json.load(_f)
PROFILE_NAMES = sorted(_RAW["servers"])
FAN_NAMES = sorted(_RAW["fans"])


@pytest.fixture(scope="session")
def cfg():
    """The real config, loaded through the code path under test."""
    return cc.load_config(str(CONFIG_PATH))


@pytest.fixture()
def profile(cfg, request):
    """Deep copy of one server profile - tests may mutate freely."""
    return copy.deepcopy(cfg["servers"][request.param])


def all_component_boxes(geo):
    """Every named component box in a geometry dict: drives, CPU sinks,
    custom porous zones, and solids (RAM banks, PCIe cards, PSUs...)."""
    boxes = []
    if geo["drives"]:
        boxes.append((geo["drives"][0], geo["drives"][1]))
    boxes += [(n, b) for n, b, _k, _c2 in geo["cpus"]]
    boxes += [(z["name"], z["box"]) for z in geo["extra_porous"]]
    boxes += list(geo["solids"])
    return boxes
