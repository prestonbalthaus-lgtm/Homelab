"""Validation gate for staging/profiles_dell.json (Dell profile expansion).

Every staged Dell profile must build through chassis_cfd.build_geometry()
at its default PCIe population, empty (0 cards - risers persist), and at
its pcie_max_slots ceiling (the wizard/apply_hw_overrides can populate up
to that many cards at runtime). The staging file is merged into
server_configs.json by the orchestrator; once merged, conftest picks the
profiles up automatically and this module can be retired.
"""
import copy
import json

import pytest

from conftest import REPO, cc

STAGING = REPO / "staging" / "profiles_dell.json"

pytestmark = pytest.mark.skipif(
    not STAGING.exists(),
    reason="staging/profiles_dell.json already merged and removed",
)


def _profiles():
    if not STAGING.exists():
        return {}
    with STAGING.open() as fh:
        return json.load(fh)["servers"]


PROFILES = _profiles()


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_staged_profile_builds(name):
    cc.build_geometry(PROFILES[name])


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_staged_profile_builds_empty_and_full(name):
    for n_cards in (0, int(PROFILES[name].get("pcie_max_slots", 8))):
        cfg = copy.deepcopy(PROFILES[name])
        cfg["populated_pcie_slots"] = n_cards
        cc.build_geometry(cfg)


def test_no_collision_with_existing_profiles():
    with (REPO / "server_configs.json").open() as fh:
        existing = set(json.load(fh)["servers"])
    assert not (set(PROFILES) & existing), (
        "staged Dell profiles must not shadow existing server_configs.json "
        "entries (R640/R740xd are pre-existing and excluded by design)"
    )


def test_r640_r740xd_not_restaged():
    assert "R640" not in PROFILES and "R740xd" not in PROFILES
