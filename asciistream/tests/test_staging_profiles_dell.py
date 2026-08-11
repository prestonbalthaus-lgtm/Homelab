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


def test_staged_profiles_were_merged_faithfully():
    """This was a pre-merge collision guard. The staging profiles have now
    been merged into server_configs.json, so the useful invariant flips:
    every staged profile must be PRESENT in the live config and byte-equal
    to the staged definition. That keeps staging/ as honest provenance for
    the SOURCES ledger and catches silent drift between the two."""
    with (REPO / "server_configs.json").open() as fh:
        live = json.load(fh)["servers"]
    missing = sorted(set(PROFILES) - set(live))
    assert not missing, f"staged Dell profiles absent from live config: {missing}"
    drifted = [k for k in PROFILES if live[k] != PROFILES[k]]
    assert not drifted, (
        f"staged and live definitions disagree for {drifted} - staging/ no "
        "longer documents what actually ships")


def test_r640_r740xd_not_restaged():
    assert "R640" not in PROFILES and "R740xd" not in PROFILES
