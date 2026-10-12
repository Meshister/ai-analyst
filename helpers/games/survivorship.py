"""Survivorship matrix: the retention triangle (cohorts x periods since first session).

Used by the games-survivorship-matrix skill. Tested in
tests/games/test_survivorship.py.

Definitions follow the metric registry
(.claude/skills/games-metric-dictionary/registry/):
- period="day":  cell = retention_dn_strict, active on exactly day n after the
  player's first session.
- period="week": cell = retention_dn_bracket with 7-day brackets, active at
  least once on days 7k..7k+6 after the player's OWN first session (relative
  weeks, not calendar weeks).
- D0 and W0 are 100% by definition: every member plays on day 0.

Conventions (shared with helpers/games/metrics.py):
- A cell nobody in the cohort is old enough for is None ("immature"), never 0.
- A cell only some members are old enough for is computed on those members
  and marked "partial"; its eligible count is reported.
- Cross-cohort rows are ratios of sums (pooled by player), not means of rates.
- Activity dates must already be calendar dates under the stated day boundary;
  the boundary is used for maturity and printed on every output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Literal, Optional, Sequence

import pandas as pd

from helpers.games.metrics import DayBoundary, _check_boundary, is_day_mature

Period = Literal["day", "week"]
CohortGrain = Literal["day", "week", "month"]

DEFAULT_DAY_PERIODS = (0, 1, 7, 30, 90)
DEFAULT_WEEK_PERIODS = tuple(range(13))  # W0..W12
DEFAULT_MIN_COHORT_SIZE = 100

GOLDEN_LABEL = "Golden (launch)"
AVERAGE_LABEL = "Weighted avg"
BALANCED_LABEL = "Balanced avg"

ROW_TYPES = ("golden", "cohort", "average", "balanced")
STATUSES = ("complete", "partial", "immature")


@dataclass(frozen=True)
class Window:
    """One column: days [start, end] after first session (inclusive)."""
    label: str
    start: int
    end: int


def make_windows(period: Period, periods: Optional[Sequence[int]] = None) -> list[Window]:
    """Columns for the matrix. Days: exact day n. Weeks: days 7k..7k+6."""
    if period not in ("day", "week"):
        raise ValueError(f"period must be 'day' or 'week', got {period!r}")
    if periods is None:
        periods = DEFAULT_DAY_PERIODS if period == "day" else DEFAULT_WEEK_PERIODS
    values = list(periods)
    if not values:
        raise ValueError("periods must not be empty")
    if any(not isinstance(p, int) or isinstance(p, bool) or p < 0 for p in values):
        raise ValueError("periods must be non-negative integers")
    if len(set(values)) != len(values):
        raise ValueError("periods must not repeat")
    values.sort()
    if period == "day":
        return [Window(f"D{n}", n, n) for n in values]
    return [Window(f"W{k}", 7 * k, 7 * k + 6) for k in values]


def cohort_start(d: date, grain: CohortGrain) -> date:
    """First day of the cohort containing date d (weeks start Monday, ISO)."""
    if grain == "day":
        return d
    if grain == "week":
        return d - timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    raise ValueError(f"cohort_grain must be 'day', 'week' or 'month', got {grain!r}")


def cohort_label(start: date, grain: CohortGrain) -> str:
    """'2026-W36' for weeks, '2026-09' for months, '2026-09-01' for days."""
    if grain == "week":
        iso = start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if grain == "month":
        return start.strftime("%Y-%m")
    return start.isoformat()


def last_mature_day(as_of: datetime, day_boundary: DayBoundary,
                    reporting_tz: Optional[tzinfo] = None) -> date:
    """Latest calendar day that has fully elapsed at `as_of` under the boundary."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    _check_boundary(day_boundary, reporting_tz)
    d = as_of.astimezone(timezone.utc).date() + timedelta(days=1)
    while not is_day_mature(d, as_of, day_boundary, reporting_tz):
        d -= timedelta(days=1)
    return d


@dataclass(frozen=True)
class SurvivorshipMatrix:
    """Result of build_survivorship_matrix.

    rows:  one line per row (golden, cohorts, average rows): row_label, row_type,
           cohort_start, players, small.
    cells: one line per row x column: row_label, period, eligible, active,
           value (None if immature), status, n_cohorts (average rows only).
    meta:  definition labels every output must carry (metric, retention type,
           day boundary, as_of, identity, warnings).
    """
    rows: pd.DataFrame
    cells: pd.DataFrame
    period_labels: tuple[str, ...]
    meta: dict

    def values(self) -> pd.DataFrame:
        """Wide table: 'players' then one column per period; None where immature."""
        wide = self.cells.pivot(index="row_label", columns="period", values="value")
        wide = wide.reindex(index=list(self.rows["row_label"]), columns=list(self.period_labels))
        wide = wide.astype(object).where(wide.notna(), None)
        wide.insert(0, "players", list(self.rows["players"]))
        wide.index.name = None
        wide.columns.name = None
        return wide

    def to_display(self) -> pd.DataFrame:
        """Stepped table as strings, for a report.

        '38%'      complete cell
        '38%*'     partial: computed on the members old enough for it
        ''         immature
        '[38%]'    row below min_cohort_size (greyed out in the heatmap)
        '38% (4)'  average row: value and number of cohorts in it
                   ('38%* (4)' if any cohort in it is partial)
        """
        table: dict[str, list[str]] = {"players": []}
        for p in self.period_labels:
            table[p] = []
        cell_index = self.cells.set_index(["row_label", "period"])
        for _, row in self.rows.iterrows():
            players = row["players"]
            if row["row_type"] in ("average", "balanced"):
                table["players"].append(f"{players:,}")
            else:
                table["players"].append(f"{players:,}" + (" (small)" if row["small"] else ""))
            for p in self.period_labels:
                c = cell_index.loc[(row["row_label"], p)]
                if c["value"] is None or pd.isna(c["value"]):
                    table[p].append("")
                    continue
                text = f"{c['value']:.0%}"
                if c["status"] == "partial":
                    text += "*"
                if row["row_type"] in ("average", "balanced"):
                    text += f" ({int(c['n_cohorts'])})"
                if row["small"]:
                    text = f"[{text}]"
                table[p].append(text)
        out = pd.DataFrame(table, index=list(self.rows["row_label"]))
        return out

    def plot(self, ax=None):
        """Heatmap of the matrix, reusing upstream's retention_heatmap.

        Immature cells and rows below min_cohort_size are grey (small rows keep
        their numbers in grey text); partial cells are hatched. The title carries
        the metric, day boundary and as_of. Returns (fig, ax).
        """
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        from helpers.viz.chart_helpers import COLORS, retention_heatmap

        if ax is None:
            n_rows = len(self.rows)
            _, ax = plt.subplots(figsize=(1.0 + 0.75 * len(self.period_labels), 0.9 + 0.45 * n_rows))
        vals = self.values()
        small = list(self.rows["small"])
        matrix = []
        for i, (_, r) in enumerate(vals.iterrows()):
            line = []
            for p in self.period_labels:
                v = r[p]
                line.append(float("nan") if v is None or small[i] else float(v))
            matrix.append(line)
        labels = [
            f"{lab}  n={n:,}" for lab, n in zip(self.rows["row_label"], self.rows["players"])
        ]
        fig, ax = retention_heatmap(ax, labels, list(self.period_labels), matrix)

        cell_index = self.cells.set_index(["row_label", "period"])
        for i, row_label in enumerate(self.rows["row_label"]):
            for j, p in enumerate(self.period_labels):
                c = cell_index.loc[(row_label, p)]
                has_value = c["value"] is not None and not pd.isna(c["value"])
                if small[i] and has_value:
                    ax.text(j, i, f"{c['value']:.0%}", ha="center", va="center",
                            fontsize=9, color=COLORS["gray600"])
                if c["status"] == "partial":
                    ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False,
                                           hatch="///", edgecolor=COLORS["gray400"],
                                           linewidth=0))
        m = self.meta
        ax.set_title(
            f"{m['metric_id']} ({m['window']}) | day boundary: {m['day_boundary']} | "
            f"as of {m['as_of'][:10]}",
            fontsize=9, color=COLORS["gray600"], pad=28,
        )
        return fig, ax


def _cells_for_group(group: pd.DataFrame, row_label: str, windows: list[Window]) -> list[dict]:
    size = len(group)
    out = []
    for i, w in enumerate(windows):
        if w.start == 0:
            # D0 / W0: every member plays on day 0 by definition.
            out.append(dict(row_label=row_label, period=w.label, eligible=size, active=size,
                            value=1.0 if size else None,
                            status="complete" if size else "immature", n_cohorts=None))
            continue
        elig = group[f"elig_{i}"]
        eligible = int(elig.sum())
        active = int((group[f"act_{i}"] & elig).sum())
        if eligible == 0:
            value, status = None, "immature"
        else:
            value = active / eligible
            status = "complete" if eligible == size else "partial"
        out.append(dict(row_label=row_label, period=w.label, eligible=eligible, active=active,
                        value=value, status=status, n_cohorts=None))
    return out


def _pooled_cells(cohort_cells: pd.DataFrame, row_label: str, windows: list[Window],
                  include: Optional[set] = None) -> list[dict]:
    out = []
    for w in windows:
        c = cohort_cells[cohort_cells["period"] == w.label]
        if include is not None:
            c = c[c["row_label"].isin(include)]
        c = c[c["eligible"] > 0]
        eligible = int(c["eligible"].sum())
        active = int(c["active"].sum())
        if not eligible:
            status = "immature"
        else:
            status = "partial" if (c["status"] == "partial").any() else "complete"
        out.append(dict(row_label=row_label, period=w.label, eligible=eligible, active=active,
                        value=(active / eligible) if eligible else None,
                        status=status, n_cohorts=int(len(c))))
    return out


def _to_dates(values: pd.Series, what: str) -> pd.Series:
    """Parse to calendar dates; refuse timestamps (they must be converted under the boundary first)."""
    parsed = pd.to_datetime(values)
    if getattr(parsed.dt, "tz", None) is not None:
        raise ValueError(f"{what} holds timezone-aware timestamps; convert them to calendar "
                         "dates in the chosen day boundary first")
    clean = parsed.dropna()
    if (clean != clean.dt.normalize()).any():
        raise ValueError(f"{what} holds times of day; convert them to calendar dates in the "
                         "chosen day boundary first")
    return parsed.dt.date


def _as_date(value, what: str) -> date:
    if isinstance(value, datetime):          # includes pd.Timestamp
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"{what} must be a date")


def build_survivorship_matrix(
    activity: pd.DataFrame,
    *,
    as_of: datetime,
    period: Period = "week",
    periods: Optional[Sequence[int]] = None,
    cohort_grain: CohortGrain = "week",
    day_boundary: DayBoundary = "utc",
    reporting_tz: Optional[tzinfo] = None,
    min_cohort_size: int = DEFAULT_MIN_COHORT_SIZE,
    release_date: Optional[date] = None,
    launch_window_days: int = 7,
    balanced: bool = False,
    first_sessions: Optional[pd.Series] = None,
    data_start: Optional[date] = None,
    player_col: str = "account_id",
    date_col: str = "activity_date",
) -> SurvivorshipMatrix:
    """Build the retention triangle from player activity.

    activity: one row per player per active day (duplicates are fine), with
        `player_col` and `date_col` (calendar dates under `day_boundary`).
    as_of: timezone-aware instant the data runs to; decides which cells are mature.
    period / periods: 'week' (default, W0..W12) or 'day' (default D0, D1, D7,
        D30, D90); `periods` overrides the columns (week numbers or day numbers).
    cohort_grain: 'week' (default, ISO weeks from Monday), 'day' or 'month'.
    min_cohort_size: rows smaller than this are flagged small (greyed out).
    release_date: adds the golden (launch) cohort row: first session within
        `launch_window_days` of release. It overlaps the first cohorts, so it is
        a comparison row and is left out of the average rows.
    balanced: also add a row pooled only over cohorts complete in every column.
    first_sessions: optional Series player -> first-session date from an
        authoritative source. Without it, first session = first activity in the
        data, which is wrong for players who started before the data does.
        Every player in it is a cohort member, with or without activity rows.
    data_start: first day the activity data covers (default: earliest activity
        date). A member counts for a column only if the whole window falls on or
        after it; earlier windows are unknown, not zero.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    _check_boundary(day_boundary, reporting_tz)
    windows = make_windows(period, periods)
    cohort_start(date(2000, 1, 1), cohort_grain)  # validates the grain
    if not isinstance(min_cohort_size, int) or min_cohort_size < 1:
        raise ValueError("min_cohort_size must be a positive integer")
    if launch_window_days < 1:
        raise ValueError("launch_window_days must be >= 1")
    missing = [c for c in (player_col, date_col) if c not in activity.columns]
    if missing:
        raise ValueError(f"activity is missing columns: {missing}")

    if release_date is not None:
        release_date = _as_date(release_date, "release_date")

    warnings: list[str] = []
    df = activity[[player_col, date_col]].copy()
    n_null = int(df.isna().any(axis=1).sum())
    df = df.dropna()
    if n_null:
        warnings.append(f"{n_null} activity rows dropped for a missing player key or date")
    df[date_col] = _to_dates(df[date_col], "activity dates")
    df = df.drop_duplicates()
    if df.empty:
        raise ValueError("no activity rows to build a matrix from")
    data_start = _as_date(data_start, "data_start") if data_start is not None else min(df[date_col])

    if first_sessions is not None:
        if first_sessions.index.duplicated().any():
            raise ValueError("first_sessions has duplicate player keys; deduplicate it first")
        fs = _to_dates(pd.Series(first_sessions), "first_sessions").rename("first_session")
        n_missing = int(fs.isna().sum())
        if n_missing:
            warnings.append(f"{n_missing} players in first_sessions have no date; left out")
            fs = fs.dropna()
        unknown = ~df[player_col].isin(fs.index)
        if unknown.any():
            warnings.append(
                f"{int(df.loc[unknown, player_col].nunique())} players have activity but no "
                "first session in first_sessions; left out"
            )
            df = df[~unknown]
        players = fs.to_frame()
        n_early = int((players["first_session"] < data_start).sum())
        if n_early:
            warnings.append(
                f"{n_early} players started before the data begins ({data_start.isoformat()}); "
                "their columns before that date are unknown and left out"
            )
        df = df.join(fs, on=player_col)
        before = df[date_col] < df["first_session"]
        if before.any():
            warnings.append(f"{int(before.sum())} activity rows before the player's first session ignored")
            df = df[~before]
    else:
        players = df.groupby(player_col)[date_col].min().rename("first_session").to_frame()
        df = df.join(players["first_session"], on=player_col)
        warnings.append(
            "first session = first activity in the data; players who started before the "
            "data begins are misread as new (pass first_sessions to avoid this)"
        )

    df["offset"] = (pd.to_datetime(df[date_col]) - pd.to_datetime(df["first_session"])).dt.days
    mature = last_mature_day(as_of, day_boundary, reporting_tz)
    first_ts = pd.to_datetime(players["first_session"])
    for i, w in enumerate(windows):
        in_window = df[(df["offset"] >= w.start) & (df["offset"] <= w.end)][player_col].unique()
        players[f"act_{i}"] = players.index.isin(in_window)
        covered = (first_ts + pd.Timedelta(days=w.start)) >= pd.Timestamp(data_start)
        ended = (first_ts + pd.Timedelta(days=w.end)) <= pd.Timestamp(mature)
        players[f"elig_{i}"] = covered & ended
    players["cohort_start"] = [cohort_start(d, cohort_grain) for d in players["first_session"]]

    row_records: list[dict] = []
    cell_records: list[dict] = []

    if release_date is not None:
        window_end = release_date + timedelta(days=launch_window_days - 1)
        golden = players[(players["first_session"] >= release_date)
                         & (players["first_session"] <= window_end)]
        if golden.empty:
            warnings.append("no players had a first session in the launch window; golden row left out")
        else:
            row_records.append(dict(row_label=GOLDEN_LABEL, row_type="golden", cohort_start=release_date,
                                    players=len(golden), small=len(golden) < min_cohort_size))
            cell_records += _cells_for_group(golden, GOLDEN_LABEL, windows)

    cohort_labels: list[str] = []
    for start, group in sorted(players.groupby("cohort_start"), key=lambda kv: kv[0]):
        label = cohort_label(start, cohort_grain)
        cohort_labels.append(label)
        row_records.append(dict(row_label=label, row_type="cohort", cohort_start=start,
                                players=len(group), small=len(group) < min_cohort_size))
        cell_records += _cells_for_group(group, label, windows)

    cells = pd.DataFrame(cell_records)
    cohort_cells = cells[cells["row_label"].isin(cohort_labels)]
    total_players = int(sum(r["players"] for r in row_records if r["row_type"] == "cohort"))
    row_records.append(dict(row_label=AVERAGE_LABEL, row_type="average", cohort_start=None,
                            players=total_players, small=False))
    extra = _pooled_cells(cohort_cells, AVERAGE_LABEL, windows)

    if balanced:
        status_by_row = cohort_cells.groupby("row_label")["status"].agg(lambda s: set(s) == {"complete"})
        keep = set(status_by_row[status_by_row].index)
        if not keep:
            warnings.append("no cohort is complete in every column; balanced row is empty")
        bal_players = int(sum(r["players"] for r in row_records
                              if r["row_type"] == "cohort" and r["row_label"] in keep))
        row_records.append(dict(row_label=BALANCED_LABEL, row_type="balanced", cohort_start=None,
                                players=bal_players, small=False))
        extra += _pooled_cells(cohort_cells, BALANCED_LABEL, windows, include=keep)

    cells = pd.concat([cells, pd.DataFrame(extra)], ignore_index=True)
    cells["value"] = cells["value"].astype(object).where(cells["value"].notna(), None)

    if period == "day":
        metric_id, retention_type, window_text = "retention_dn_strict", "strict", "active on exactly day n"
    else:
        metric_id, retention_type, window_text = (
            "retention_dn_bracket", "bracket",
            "active on any of days 7k..7k+6 after own first session",
        )
    meta = dict(
        metric_id=metric_id,
        retention_type=retention_type,
        window=window_text,
        period=period,
        cohort_grain=cohort_grain,
        day_boundary=day_boundary,
        reporting_tz=str(reporting_tz) if reporting_tz is not None else None,
        as_of=as_of.isoformat(),
        last_mature_day=mature.isoformat(),
        data_start=data_start.isoformat(),
        identity=dict(cohort_event="first_session", player_key=player_col),
        min_cohort_size=min_cohort_size,
        release_date=release_date.isoformat() if release_date else None,
        launch_window_days=launch_window_days if release_date else None,
        warnings=warnings,
    )
    return SurvivorshipMatrix(
        rows=pd.DataFrame(row_records),
        cells=cells,
        period_labels=tuple(w.label for w in windows),
        meta=meta,
    )
