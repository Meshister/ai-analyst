"""Contract for registry/_backlog.yaml: every KPI in the reference docs, tracked.

The backlog is how metrics from the docs get into the registry phase by phase
without being lost. These tests keep it honest: a KPI marked built must point
at a real registry file, and a KPI still in the backlog must not claim an ID
that already exists (that means someone built it and forgot to update the backlog).
"""
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / ".claude" / "skills" / "games-metric-dictionary" / "registry"

with open(REGISTRY / "_backlog.yaml", encoding="utf-8") as f:
    BACKLOG = yaml.safe_load(f)
ENTRIES = BACKLOG["entries"]
REGISTRY_IDS = {p.stem for p in REGISTRY.glob("*.yaml") if not p.name.startswith("_")}

REQUIRED = ["source", "section", "kpi", "planned_id", "owner", "category", "phase", "status"]
STATUSES = {"backlog", "built"}
CATEGORIES = {"game_design", "technical", "social", "commercial", "publisher", "experimentation"}
SOURCES = {"game-analytics-doc", "cheat-sheet", "game-analytics-matrix", "gameanalytics-metrics", "2k-doc"}


def test_backlog_is_not_empty():
    assert ENTRIES, "backlog has no entries"


def test_entries_have_required_fields():
    bad = [(i, [k for k in REQUIRED if not e.get(k) and e.get(k) != 0]) for i, e in enumerate(ENTRIES)]
    bad = [(i, missing) for i, missing in bad if missing]
    assert not bad, f"entries missing fields (index, fields): {bad[:10]}"


@pytest.mark.parametrize("field, allowed", [("status", STATUSES), ("category", CATEGORIES), ("source", SOURCES)])
def test_enumerated_values(field, allowed):
    bad = sorted({e[field] for e in ENTRIES} - allowed)
    assert not bad, f"unknown {field} values: {bad}"


def test_phases_are_plan_phases():
    bad = sorted({e["phase"] for e in ENTRIES if e["phase"] not in range(0, 7)})
    assert not bad, f"phases outside 0-6: {bad}"


def test_owners_are_games_agents():
    bad = sorted({e["owner"] for e in ENTRIES if not e["owner"].startswith("games-")})
    assert not bad, f"owners outside the games namespace: {bad}"


def test_built_entries_point_to_registry_files():
    bad = [(e["kpi"], e["planned_id"]) for e in ENTRIES
           if e["status"] == "built" and e["planned_id"] not in REGISTRY_IDS]
    assert not bad, f"marked built but no registry file: {bad}"


def test_backlog_entries_do_not_claim_registry_ids():
    bad = [(e["kpi"], e["planned_id"]) for e in ENTRIES
           if e["status"] == "backlog" and e["planned_id"] in REGISTRY_IDS]
    assert not bad, f"registry file exists; mark these built: {bad}"
