"""Schema contract for the games metric registry.

Every metric file in .claude/skills/games-metric-dictionary/registry/ must
satisfy the rules in registry/_schema.yaml. Agents read that same file, so the
rules they are told and the rules that are enforced cannot drift apart.
"""
from pathlib import Path

import pytest
import yaml

from helpers.validation.metric_validator import validate_metric_definition

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / ".claude" / "skills" / "games-metric-dictionary" / "registry"


def _load(path):
    # Explicit UTF-8: Windows' default code page breaks on Σ, ≥ and similar.
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


SCHEMA = _load(REGISTRY / "_schema.yaml")
NA = SCHEMA["not_applicable"]
ENUMS = SCHEMA["enums"]
METRIC_FILES = sorted(p for p in REGISTRY.glob("*.yaml") if not p.name.startswith("_"))


def _blank(value):
    return value is None or value == "" or value == [] or value == {}


def test_registry_is_not_empty():
    assert METRIC_FILES, f"no metric files in {REGISTRY}"


@pytest.fixture(params=METRIC_FILES, ids=lambda p: p.stem)
def metric(request):
    data = _load(request.param)
    assert isinstance(data, dict), f"{request.param.name} is not a YAML mapping"
    return request.param.name, data


def test_upstream_validator_accepts(metric):
    fname, m = metric
    result = validate_metric_definition(m)
    assert result["ok"], f"{fname}: {result['errors']}"


def test_name_matches_filename(metric):
    fname, m = metric
    assert m["name"] == Path(fname).stem, f"{fname}: name is {m['name']!r}"


def test_schema_version_matches(metric):
    fname, m = metric
    assert m.get("schema_version") == SCHEMA["schema_version"], fname


def test_required_fields_present_and_filled(metric):
    fname, m = metric
    required = SCHEMA["required_upstream"] + SCHEMA["required_games"]
    missing = [f for f in required if f not in m]
    blank = [f for f in required if f in m and _blank(m[f])]
    assert not missing, f"{fname} missing required fields: {missing}"
    assert not blank, f"{fname} left blank (write '{NA}' if it does not apply): {blank}"


@pytest.mark.parametrize("field", ["status", "retention_type", "payer_window"])
def test_scalar_enums(metric, field):
    fname, m = metric
    assert m[field] in ENUMS[field], f"{fname}: {field}={m[field]!r} not in {ENUMS[field]}"


def test_day_boundary(metric):
    fname, m = metric
    db = m["day_boundary"]
    if db == NA:
        return
    assert isinstance(db, dict) and set(db) == {"supported", "default"}, \
        f"{fname}: day_boundary needs exactly 'supported' and 'default'"
    bad = set(db["supported"]) - set(ENUMS["day_boundary"])
    assert db["supported"] and not bad, f"{fname}: unknown day boundaries {bad}"
    assert db["default"] in db["supported"], f"{fname}: default not in supported"


def test_revenue_scope(metric):
    fname, m = metric
    rs = m["revenue_scope"]
    if rs == NA:
        return
    assert isinstance(rs, dict) and set(rs) == {"streams", "basis"}, \
        f"{fname}: revenue_scope is '{NA}' or a mapping with 'streams' and 'basis'"
    bad = set(rs["streams"]) - set(ENUMS["revenue_stream"])
    assert rs["streams"] and not bad, f"{fname}: unknown revenue streams {bad}"
    assert rs["basis"] in ENUMS["revenue_basis"], f"{fname}: basis={rs['basis']!r}"


def test_identity_basis(metric):
    fname, m = metric
    ib = m["identity_basis"]
    if ib == NA:
        return
    assert isinstance(ib, dict) and set(ib) == {"cohort_event", "player_key"}, \
        f"{fname}: identity_basis needs exactly 'cohort_event' and 'player_key'"
    assert ib["cohort_event"] in ENUMS["cohort_event"], f"{fname}: cohort_event={ib['cohort_event']!r}"
    assert ib["player_key"] in ENUMS["player_key"], f"{fname}: player_key={ib['player_key']!r}"


def test_tags(metric):
    fname, m = metric
    tags = m["tags"]
    assert set(tags) == {"family", "scope", "horizon", "business_model", "platform"}, \
        f"{fname}: tags keys are {sorted(tags)}"
    assert tags["family"] in ENUMS["family"], f"{fname}: family={tags['family']!r}"
    assert tags["scope"] in ENUMS["scope"], f"{fname}: scope={tags['scope']!r}"
    for key in ("horizon", "business_model", "platform"):
        values = tags[key]
        assert isinstance(values, list) and values, f"{fname}: tags.{key} must be a non-empty list"
        bad = set(values) - set(ENUMS[key])
        assert not bad, f"{fname}: unknown tags.{key} values {bad}"


def test_maturity_rule_when_time_dependent(metric):
    fname, m = metric
    if "parameters" in m or m["retention_type"] != NA:
        assert not _blank(m.get("maturity_rule")), \
            f"{fname}: cohort or windowed metric needs a maturity_rule (when is it null, not 0?)"


def test_caveats_and_sources_shape(metric):
    fname, m = metric
    assert all(isinstance(c, str) and c.strip() for c in m["caveats"]), f"{fname}: empty caveat"
    for s in m["sources"]:
        assert isinstance(s, dict) and s.get("ref") and s.get("note"), \
            f"{fname}: each source needs 'ref' and 'note'"


def test_distinct_from_excludes_self(metric):
    fname, m = metric
    assert m["name"] not in m.get("distinct_from", []), f"{fname} lists itself in distinct_from"