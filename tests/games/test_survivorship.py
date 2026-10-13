"""Tests for helpers/games/survivorship.py (the games-survivorship-matrix skill).

Fixtures are small and hand-checkable. Dates are in 2026; 2026-08-31 is a
Monday and starts ISO week 36. Unless a test says otherwise, the day boundary
is UTC and as_of is late enough for every cell to be mature.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from helpers.games.metrics import dn_retention
from helpers.games.survivorship import (
    AVERAGE_LABEL,
    BALANCED_LABEL,
    GOLDEN_LABEL,
    build_survivorship_matrix,
    cohort_label,
    cohort_start,
    last_mature_day,
    make_windows,
)

UTC = timezone.utc
MON = date(2026, 8, 31)          # Monday, ISO 2026-W36
SUN = date(2026, 9, 6)           # Sunday, same ISO week
LATE = datetime(2027, 6, 1, tzinfo=UTC)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / ".claude" / "skills" / "games-survivorship-matrix" / "SKILL.md"


def activity(rows):
    """rows: (player, first_session_date, [day offsets active]) -> activity frame."""
    out = []
    for player, first, offsets in rows:
        out.append((player, first))
        out += [(player, first + timedelta(days=o)) for o in offsets]
    return pd.DataFrame(out, columns=["account_id", "activity_date"])


def cell(m, row_label, period):
    c = m.cells[(m.cells["row_label"] == row_label) & (m.cells["period"] == period)]
    assert len(c) == 1, f"no single cell for {row_label} {period}"
    return c.iloc[0]


# --- Columns, cohorts, labels ----------------------------------------------

def test_week_defaults_are_w0_to_w12_relative_brackets():
    w = make_windows("week")
    assert [x.label for x in w] == [f"W{k}" for k in range(13)]
    assert (w[1].start, w[1].end) == (7, 13)
    assert (w[12].start, w[12].end) == (84, 90)


def test_day_defaults_are_the_standard_columns():
    assert [x.label for x in make_windows("day")] == ["D0", "D1", "D7", "D30", "D90"]
    assert all(x.start == x.end for x in make_windows("day"))


def test_caller_chooses_columns_and_they_are_sorted():
    assert [x.label for x in make_windows("day", [14, 1, 3])] == ["D1", "D3", "D14"]
    assert [x.label for x in make_windows("week", [4, 0])] == ["W0", "W4"]


@pytest.mark.parametrize("period, periods", [
    ("month", None), ("day", []), ("day", [1, 1]), ("week", [-1]), ("day", [1.5]),
])
def test_bad_columns_raise(period, periods):
    with pytest.raises(ValueError):
        make_windows(period, periods)


def test_cohort_start_and_labels():
    assert cohort_start(SUN, "week") == MON
    assert cohort_label(MON, "week") == "2026-W36"
    assert cohort_label(cohort_start(SUN, "month"), "month") == "2026-09"
    assert cohort_label(SUN, "day") == "2026-09-06"
    # ISO year boundary: Thu 2026-01-01 is in the week starting Mon 2025-12-29, ISO 2026-W01.
    assert cohort_label(cohort_start(date(2026, 1, 1), "week"), "week") == "2026-W01"
    with pytest.raises(ValueError):
        cohort_start(MON, "quarter")


# --- Cell definitions -------------------------------------------------------

def test_d0_and_w0_are_100_percent():
    df = activity([("a", MON, []), ("b", MON, [3])])
    for period in ("day", "week"):
        m = build_survivorship_matrix(df, as_of=LATE, period=period, min_cohort_size=1)
        first = m.period_labels[0]
        assert cell(m, "2026-W36", first)["value"] == 1.0


def test_daily_cells_are_strict_exact_day_n():
    # a: active day 1 and day 8 -> D1 yes, D7 no. b: active day 7 only.
    df = activity([("a", MON, [1, 8]), ("b", MON, [7])])
    m = build_survivorship_matrix(df, as_of=LATE, period="day", periods=[0, 1, 7],
                                  min_cohort_size=1)
    assert cell(m, "2026-W36", "D1")["value"] == pytest.approx(0.5)
    assert cell(m, "2026-W36", "D7")["value"] == pytest.approx(0.5)
    assert m.meta["metric_id"] == "retention_dn_strict"
    assert m.meta["retention_type"] == "strict"


def test_weeks_are_relative_to_each_players_own_first_session():
    # Sunday joiner, active 8 days later (Mon 2026-09-14). Relative: day 8 is W1.
    # Calendar weeks would have put it two weeks after the cohort week (W2).
    df = activity([("s", SUN, [8])])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1, 2], min_cohort_size=1)
    assert cell(m, "2026-W36", "W1")["value"] == 1.0
    assert cell(m, "2026-W36", "W2")["value"] == 0.0
    assert m.meta["metric_id"] == "retention_dn_bracket"


def test_week_bracket_edges():
    # Day 13 is the last day of W1; day 14 is the first day of W2.
    df = activity([("a", MON, [13]), ("b", MON, [14])])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1, 2], min_cohort_size=1)
    assert cell(m, "2026-W36", "W1")["value"] == pytest.approx(0.5)
    assert cell(m, "2026-W36", "W2")["value"] == pytest.approx(0.5)


def test_daily_cells_match_dn_retention_helper():
    # Independent path: a single-day cohort computed by the tested dn_retention.
    df = activity([("a", MON, [1, 7]), ("b", MON, [1]), ("c", MON, [30]), ("d", MON, [])])
    m = build_survivorship_matrix(df, as_of=LATE, period="day", periods=[1, 7, 30],
                                  cohort_grain="day", min_cohort_size=1)
    for n, active in [(1, 2), (7, 1), (30, 1)]:
        expected = dn_retention(active, 4, n=n, cohort_date=MON, as_of=LATE).value
        assert cell(m, "2026-08-31", f"D{n}")["value"] == pytest.approx(expected)


# --- Maturity -----------------------------------------------------------------

def test_immature_cell_is_none_not_zero():
    first = date(2026, 9, 21)
    df = activity([("a", first, [])])
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 25, tzinfo=UTC),
                                  periods=[0, 1], min_cohort_size=1)
    c = cell(m, cohort_label(cohort_start(first, "week"), "week"), "W1")
    assert c["value"] is None
    assert c["status"] == "immature"
    assert c["eligible"] == 0


def test_partial_cell_uses_only_members_old_enough():
    # Monday joiner's W1 ends 2026-09-13 (mature); Sunday joiner's ends 2026-09-19 (not).
    # The Sunday joiner's day-8 activity must not count before their week is over.
    df = activity([("mon", MON, [8]), ("sun", SUN, [8])])
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 14, tzinfo=UTC),
                                  periods=[0, 1], min_cohort_size=1)
    c = cell(m, "2026-W36", "W1")
    assert c["status"] == "partial"
    assert (c["eligible"], c["active"]) == (1, 1)
    assert c["value"] == 1.0
    assert m.to_display().loc["2026-W36", "W1"] == "100%*"


def test_complete_once_every_member_is_old_enough():
    df = activity([("mon", MON, [8]), ("sun", SUN, [])])
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 20, tzinfo=UTC),
                                  periods=[0, 1], min_cohort_size=1)
    c = cell(m, "2026-W36", "W1")
    assert c["status"] == "complete"
    assert c["value"] == pytest.approx(0.5)


def test_maturity_follows_the_day_boundary():
    # 05:00 UTC on 2026-09-14 is still 2026-09-13 in Los Angeles.
    as_of = datetime(2026, 9, 14, 5, tzinfo=UTC)
    la = ZoneInfo("America/Los_Angeles")
    assert last_mature_day(as_of, "utc") == date(2026, 9, 13)
    assert last_mature_day(as_of, "reporting_tz", la) == date(2026, 9, 12)
    assert last_mature_day(as_of, "player_local") == date(2026, 9, 12)   # needs 12:00 UTC
    df = activity([("mon", MON, [8])])
    m_utc = build_survivorship_matrix(df, as_of=as_of, periods=[0, 1], min_cohort_size=1)
    m_la = build_survivorship_matrix(df, as_of=as_of, periods=[0, 1], min_cohort_size=1,
                                     day_boundary="reporting_tz", reporting_tz=la)
    assert cell(m_utc, "2026-W36", "W1")["value"] == 1.0
    assert cell(m_la, "2026-W36", "W1")["value"] is None
    assert m_la.meta["day_boundary"] == "reporting_tz"
    assert m_la.meta["reporting_tz"] == "America/Los_Angeles"


def test_reporting_tz_requires_a_timezone():
    df = activity([("a", MON, [])])
    with pytest.raises(ValueError):
        build_survivorship_matrix(df, as_of=LATE, day_boundary="reporting_tz")


# --- Rows: small cohorts, golden cohort, averages ------------------------------

def test_small_cohort_is_flagged_and_bracketed():
    df = activity([("a", MON, [8])])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1])    # default min 100
    row = m.rows.set_index("row_label").loc["2026-W36"]
    assert bool(row["small"]) is True
    disp = m.to_display()
    assert disp.loc["2026-W36", "players"] == "1 (small)"
    assert disp.loc["2026-W36", "W1"] == "[100%]"


def test_golden_row_is_launch_window_and_left_out_of_averages():
    release = date(2026, 9, 2)                       # Wednesday; window 09-02..09-08
    df = activity([
        ("before", date(2026, 9, 1), [7]),           # W36, not golden
        ("g1", date(2026, 9, 2), [7]),               # golden
        ("g2", date(2026, 9, 8), []),                # golden (last day of window), W37
        ("after", date(2026, 9, 9), [7]),            # W37, not golden
    ])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1,
                                  release_date=release)
    rows = m.rows.set_index("row_label")
    assert list(m.rows["row_label"])[0] == GOLDEN_LABEL
    assert rows.loc[GOLDEN_LABEL, "players"] == 2
    assert cell(m, GOLDEN_LABEL, "W1")["value"] == pytest.approx(0.5)
    # Average pools the 4 cohort players once each; the golden row is not added again.
    assert rows.loc[AVERAGE_LABEL, "players"] == 4
    assert cell(m, AVERAGE_LABEL, "W1")["value"] == pytest.approx(3 / 4)
    assert m.meta["launch_window_days"] == 7


def test_golden_row_skipped_with_warning_when_nobody_launched_in_window():
    df = activity([("a", MON, [])])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1,
                                  release_date=date(2025, 1, 1))
    assert GOLDEN_LABEL not in set(m.rows["row_label"])
    assert any("golden" in w for w in m.meta["warnings"])


def test_average_row_is_ratio_of_sums_not_mean_of_rates():
    # Cohort W36: 4 players, 2 return in W1 (50%). Cohort W37: 1 player, returns (100%).
    # Pooled = 3/5 = 60%; the mean of the two rates would be 75%.
    w37 = MON + timedelta(days=7)
    df = activity([("a", MON, [8]), ("b", MON, [8]), ("c", MON, []), ("d", MON, []),
                   ("e", w37, [8])])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1)
    avg = cell(m, AVERAGE_LABEL, "W1")
    assert avg["value"] == pytest.approx(0.6)
    assert avg["n_cohorts"] == 2
    assert m.to_display().loc[AVERAGE_LABEL, "W1"] == "60% (2)"


def test_average_row_counts_only_cohorts_with_data_in_that_column():
    w37 = MON + timedelta(days=7)
    df = activity([("a", MON, [8]), ("b", w37, [])])
    # as_of: W36's W1 is mature (ends 09-13), W37's W1 is not (ends 09-20).
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 15, tzinfo=UTC),
                                  periods=[0, 1], min_cohort_size=1)
    avg = cell(m, AVERAGE_LABEL, "W1")
    assert avg["n_cohorts"] == 1
    assert avg["value"] == 1.0
    assert cell(m, AVERAGE_LABEL, "W0")["n_cohorts"] == 2


def test_balanced_row_uses_only_cohorts_complete_in_every_column():
    w37 = MON + timedelta(days=7)
    df = activity([("a", MON, [8]), ("b", MON, []), ("c", w37, [8])])
    as_of = datetime(2026, 9, 15, tzinfo=UTC)        # W37's W1 not mature yet
    m = build_survivorship_matrix(df, as_of=as_of, periods=[0, 1], min_cohort_size=1,
                                  balanced=True)
    rows = m.rows.set_index("row_label")
    assert rows.loc[BALANCED_LABEL, "players"] == 2  # only the W36 cohort
    assert cell(m, BALANCED_LABEL, "W1")["value"] == pytest.approx(0.5)


# --- Input handling ------------------------------------------------------------

def test_duplicate_activity_rows_do_not_double_count():
    df = activity([("a", MON, [8, 8, 9]), ("b", MON, [])])
    df = pd.concat([df, df], ignore_index=True)
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1)
    assert cell(m, "2026-W36", "W1")["value"] == pytest.approx(0.5)
    assert m.rows.set_index("row_label").loc["2026-W36", "players"] == 2


def test_null_keys_are_dropped_and_reported():
    df = activity([("a", MON, [8])])
    df.loc[len(df)] = [None, MON]
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1)
    assert m.rows.set_index("row_label").loc["2026-W36", "players"] == 1
    assert any("missing player key" in w for w in m.meta["warnings"])


def test_first_sessions_override_and_left_censoring_warning():
    # Player 'a' really started on 2026-08-24 (W35), before the activity extract begins.
    df = activity([("a", MON, [8]), ("b", MON, []), ("x", MON, [])])
    fs = pd.Series({"a": date(2026, 8, 24), "b": MON})
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1,
                                  first_sessions=fs)
    labels = list(m.rows["row_label"])
    assert "2026-W35" in labels and "2026-W36" in labels
    assert any("no first session" in w for w in m.meta["warnings"])        # 'x' left out
    # Without first_sessions, the helper warns that first session = first activity.
    m2 = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1)
    assert any("first activity in the data" in w for w in m2.meta["warnings"])


def test_activity_before_first_session_is_ignored():
    df = activity([("a", MON, [8])])
    df.loc[len(df)] = ["a", MON - timedelta(days=3)]
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1,
                                  first_sessions=pd.Series({"a": MON}))
    assert list(m.rows["row_label"])[0] == "2026-W36"
    assert any("before the player's first session" in w for w in m.meta["warnings"])


def test_first_sessions_players_without_activity_are_cohort_members():
    # Review finding: players who started early and never came back have no activity rows.
    # They must stay in the denominator, or retention is inflated.
    jan1 = date(2026, 1, 1)
    fs = pd.Series({"p1": jan1, "p2": jan1, "p3": jan1, "p4": jan1})
    df = pd.DataFrame({"account_id": ["p1", "p2"], "activity_date": [jan1 + timedelta(days=7)] * 2})
    m = build_survivorship_matrix(df, as_of=LATE, period="day", periods=[0, 7],
                                  cohort_grain="day", min_cohort_size=1,
                                  first_sessions=fs, data_start=jan1)
    assert m.rows.set_index("row_label").loc["2026-01-01", "players"] == 4
    assert cell(m, "2026-01-01", "D7")["value"] == pytest.approx(0.5)


def test_windows_before_the_data_starts_are_unknown_not_zero():
    # Review finding: activity starts 2026-01-08 but players started 2026-01-01.
    # D1 (2026-01-02) is outside the data: unknown, not 0%.
    jan1 = date(2026, 1, 1)
    fs = pd.Series({"p1": jan1, "p2": jan1})
    df = pd.DataFrame({"account_id": ["p1"], "activity_date": [jan1 + timedelta(days=7)]})
    m = build_survivorship_matrix(df, as_of=LATE, period="day", periods=[0, 1, 7],
                                  cohort_grain="day", min_cohort_size=1, first_sessions=fs)
    assert m.meta["data_start"] == "2026-01-08"
    assert cell(m, "2026-01-01", "D1")["value"] is None
    assert cell(m, "2026-01-01", "D1")["status"] == "immature"
    assert cell(m, "2026-01-01", "D7")["value"] == pytest.approx(0.5)
    assert any("started before the data begins" in w for w in m.meta["warnings"])


def test_duplicate_first_session_keys_raise():
    fs = pd.Series([MON, MON, MON], index=["a", "a", "b"])
    with pytest.raises(ValueError, match="duplicate"):
        build_survivorship_matrix(activity([("a", MON, []), ("b", MON, [])]), as_of=LATE,
                                  first_sessions=fs)


def test_missing_first_session_dates_are_dropped_with_warning():
    fs = pd.Series({"a": MON, "b": None})
    m = build_survivorship_matrix(activity([("a", MON, [8]), ("b", MON, [])]), as_of=LATE,
                                  periods=[0, 1], min_cohort_size=1, first_sessions=fs)
    assert m.rows.set_index("row_label").loc["2026-W36", "players"] == 1
    assert any("have no date" in w for w in m.meta["warnings"])


def test_release_date_accepts_timestamps():
    df = activity([("a", MON, [8])])
    m = build_survivorship_matrix(df, as_of=LATE, periods=[0, 1], min_cohort_size=1,
                                  release_date=pd.Timestamp(MON))
    assert GOLDEN_LABEL in set(m.rows["row_label"])


@pytest.mark.parametrize("stamp", [
    pd.Timestamp("2026-08-31 18:30"),                       # time of day
    pd.Timestamp("2026-08-31", tz="UTC"),                   # timezone-aware
])
def test_timestamps_must_be_converted_to_dates_first(stamp):
    df = pd.DataFrame({"account_id": ["a"], "activity_date": [stamp]})
    with pytest.raises(ValueError, match="calendar dates"):
        build_survivorship_matrix(df, as_of=LATE)


def test_average_row_is_starred_when_it_pools_a_partial_cell():
    df = activity([("mon", MON, [8]), ("sun", SUN, [8])])
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 14, tzinfo=UTC),
                                  periods=[0, 1], min_cohort_size=1)
    assert m.to_display().loc[AVERAGE_LABEL, "W1"] == "100%* (1)"


@pytest.mark.parametrize("kwargs", [
    dict(as_of=datetime(2027, 1, 1)),                     # naive datetime
    dict(as_of=LATE, cohort_grain="quarter"),
    dict(as_of=LATE, min_cohort_size=0),
    dict(as_of=LATE, launch_window_days=0, release_date=MON),
])
def test_bad_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        build_survivorship_matrix(activity([("a", MON, [])]), **kwargs)


def test_missing_columns_and_empty_input_raise():
    with pytest.raises(ValueError):
        build_survivorship_matrix(pd.DataFrame({"player": ["a"]}), as_of=LATE)
    with pytest.raises(ValueError):
        build_survivorship_matrix(pd.DataFrame(columns=["account_id", "activity_date"]),
                                  as_of=LATE)


# --- Outputs -------------------------------------------------------------------

def test_values_table_has_players_first_and_none_for_immature():
    df = activity([("a", date(2026, 9, 21), [])])
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 25, tzinfo=UTC),
                                  periods=[0, 1], min_cohort_size=1)
    v = m.values()
    assert list(v.columns) == ["players", "W0", "W1"]
    assert v.iloc[0]["W1"] is None


def test_meta_carries_the_definition_labels():
    m = build_survivorship_matrix(activity([("a", MON, [])]), as_of=LATE, min_cohort_size=1)
    for key in ("metric_id", "retention_type", "window", "day_boundary", "as_of",
                "last_mature_day", "identity", "cohort_grain"):
        assert m.meta[key] is not None
    assert m.meta["identity"] == {"cohort_event": "first_session", "player_key": "account_id"}


def test_heatmap_renders_with_definition_in_title():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    df = activity([("mon", MON, [8]), ("sun", SUN, [8])])
    m = build_survivorship_matrix(df, as_of=datetime(2026, 9, 14, tzinfo=UTC),
                                  periods=[0, 1, 2], release_date=MON)
    fig, ax = m.plot()
    title = ax.get_title()
    assert "retention_dn_bracket" in title and "day boundary: utc" in title
    import matplotlib.pyplot as plt
    plt.close(fig)


# --- The skill file ------------------------------------------------------------

def _frontmatter():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---")
    return yaml.safe_load(text[3:text.index("\n---", 3)])


def test_skill_file_name_matches_folder():
    assert _frontmatter()["name"] == "games-survivorship-matrix"


def test_skill_description_is_written_and_within_budget():
    desc = str(_frontmatter()["description"])
    assert "___" not in desc and "TODO" not in desc, "fill in the SKILL.md description"
    assert len(desc) // 4 <= 220, f"description ~{len(desc) // 4} tokens; cap is 220"
    assert "not" in desc.lower(), "say what this skill is NOT for (the 'not me' line)"
