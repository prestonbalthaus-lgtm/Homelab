"""Validation gate for staging/profiles_hpe_smc.json (HPE ProLiant G7 to
Gen10 Plus + Supermicro WIO profile expansion).

Every staged profile must build through chassis_cfd.build_geometry() at its
default PCIe population, empty (0 cards - risers persist), and at its
pcie_max_slots ceiling (the wizard/apply_hw_overrides can populate up to
that many cards at runtime). The staging file is merged into
server_configs.json by the orchestrator; once merged, conftest picks the
profiles up automatically and this module can be retired.
"""
import copy
import json

import pytest

from conftest import REPO, cc

STAGING = REPO / "staging" / "profiles_hpe_smc.json"

EXPECTED_KEYS = {
    "DL360G7", "DL360G8", "DL360G9", "DL360G10P",
    "DL380G7", "DL380G8", "DL380G9", "DL380G10P",
    "1029P", "6029P",
}

pytestmark = pytest.mark.skipif(
    not STAGING.exists(),
    reason="staging/profiles_hpe_smc.json already merged and removed",
)


def _profiles():
    if not STAGING.exists():
        return {}
    with STAGING.open() as fh:
        return json.load(fh)["servers"]


PROFILES = _profiles()


def test_expected_profile_set():
    assert set(PROFILES) == EXPECTED_KEYS


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
    """The bare DL360/DL380 keys are the PRE-EXISTING Gen10 entries; the
    staged generations use suffixed keys so nothing shadows them."""
    with (REPO / "server_configs.json").open() as fh:
        existing = set(json.load(fh)["servers"])
    assert not (set(PROFILES) & existing), (
        "staged HPE/Supermicro profiles must not shadow existing "
        "server_configs.json entries (DL360/DL380 Gen10 and 6029U are "
        "pre-existing and excluded by design)"
    )


def test_bare_dl360_dl380_not_restaged():
    assert "DL360" not in PROFILES and "DL380" not in PROFILES


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_1u_shorter_than_2u_and_heat_tracks_generation(name):
    """Internal-consistency guards from the research brief: a 1U must be
    shorter in height than its 2U sibling, and heat loads must rise with
    the generation's CPU TDP class."""
    cfg = PROFILES[name]
    if cfg["form_factor"] == "1U":
        assert cfg["chassis_height"] < 0.05
    else:
        assert cfg["chassis_height"] > 0.08
    gens = ["G7", "G8", "G9", "G10P"]
    for fam in ("DL360", "DL380"):
        loads = [PROFILES[fam + g]["heat_load"] for g in gens]
        assert loads == sorted(loads), f"{fam} heat_load not monotonic"
