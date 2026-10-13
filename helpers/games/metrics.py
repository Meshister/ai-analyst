"""Games metric formulas: retention, retention curves, player duration, LTV, DAU.

Every formula here has a test in tests/games/test_metrics_formulas.py, using
Ovans' worked examples where they exist. Definitions follow the registry in
.claude/skills/games-metric-dictionary/registry/ (e.g. retention_dn_strict.yaml).

Conventions:
- A cohort that is too young for a value returns None, never 0.
- Outputs that depend on a day boundary carry the boundary they used.
- Day 0 is the cohort's entry day (first session); r(0) = 1 by definition.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Iterable, Literal, Mapping, Optional, Sequence

DayBoundary = Literal["utc", "player_local", "reporting_tz"]
DAY_BOUNDARIES = ("utc", "player_local", "reporting_tz")

# A player-local day spans ~50 UTC hours (UTC+14 to UTC-12). The last timezone
# finishes calendar day D at 12:00 UTC on D+1.
PLAYER_LOCAL_LAG = timedelta(hours=12)


@dataclass(frozen=True)
class MetricValue:
    """A metric result labeled with the day boundary it used."""
    value: Optional[float]
    day_boundary: DayBoundary
    status: Literal["ok", "immature"]


def _check_boundary(day_boundary: str, reporting_tz: Optional[tzinfo]) -> None:
    if day_boundary not in DAY_BOUNDARIES:
        raise ValueError(f"day_boundary must be one of {DAY_BOUNDARIES}, got {day_boundary!r}")
    if day_boundary == "reporting_tz" and reporting_tz is None:
        raise ValueError("reporting_tz is required when day_boundary='reporting_tz'")


def day_complete_at(day: date, day_boundary: DayBoundary,
                    reporting_tz: Optional[tzinfo] = None) -> datetime:
    """UTC instant after which calendar `day` has fully elapsed under the boundary."""
    _check_boundary(day_boundary, reporting_tz)
    next_midnight = datetime.combine(day + timedelta(days=1), time(0))
    if day_boundary == "utc":
        return next_midnight.replace(tzinfo=timezone.utc)
    if day_boundary == "player_local":
        return next_midnight.replace(tzinfo=timezone.utc) + PLAYER_LOCAL_LAG
    return next_midnight.replace(tzinfo=reporting_tz).astimezone(timezone.utc)


def is_day_mature(day: date, as_of: datetime, day_boundary: DayBoundary,
                  reporting_tz: Optional[tzinfo] = None) -> bool:
    """True once `day` has fully elapsed. `as_of` must be timezone-aware."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return as_of >= day_complete_at(day, day_boundary, reporting_tz)


def dn_retention(active_on_day_n: int, cohort_size: int, n: int, cohort_date: date,
                 as_of: datetime, day_boundary: DayBoundary = "utc",
                 reporting_tz: Optional[tzinfo] = None) -> MetricValue:
    """Strict day-n retention (registry: retention_dn_strict).

    R_n = active_on_day_n / cohort_size. Returns value None (status 'immature')
    until day cohort_date + n has fully elapsed under the day boundary.
    """
    if n < 1:
        raise ValueError("n must be >= 1; D0 is 1 by definition and is the denominator")
    if cohort_size <= 0:
        raise ValueError("cohort_size must be positive")
    if not 0 <= active_on_day_n <= cohort_size:
        raise ValueError("active_on_day_n must be between 0 and cohort_size")
    if not is_day_mature(cohort_date + timedelta(days=n), as_of, day_boundary, reporting_tz):
        return MetricValue(None, day_boundary, "immature")
    return MetricValue(active_on_day_n / cohort_size, day_boundary, "ok")


def pooled_retention_profile(cohorts: Iterable[Mapping]) -> dict[int, float]:
    """Retention profile pooled across cohorts: sum of actives / sum of cohort sizes.

    Each cohort is {"cohort_size": int, "active": {n: int or None}}. None means the
    cohort is not mature for that n and is left out of both numerator and
    denominator. This weights by cohort size; it is not the mean of daily rates.
    """
    num: dict[int, int] = {}
    den: dict[int, int] = {}
    for c in cohorts:
        size = c["cohort_size"]
        if size <= 0:
            raise ValueError("cohort_size must be positive")
        for n, active in c["active"].items():
            if active is None:
                continue
            if not 0 <= active <= size:
                raise ValueError(f"active on day {n} outside 0..cohort_size")
            num[n] = num.get(n, 0) + active
            den[n] = den.get(n, 0) + size
    return {n: num[n] / den[n] for n in sorted(num)}


@dataclass(frozen=True)
class PowerCurve:
    """Retention curve r(n) = a * n^b, with r(0) = 1 and r(n) = 0 after terminal_day."""
    a: float
    b: float
    terminal_day: Optional[int] = None

    def __call__(self, n: int) -> float:
        if n < 0:
            raise ValueError("n must be >= 0")
        if n == 0:
            return 1.0
        if self.terminal_day is not None and n > self.terminal_day:
            return 0.0
        return self.a * n ** self.b


def fit_power_curve(n_values: Sequence[int], rates: Sequence[float],
                    terminal_day: Optional[int] = None) -> PowerCurve:
    """Least-squares fit of ln r = ln a + b ln n (the Excel/Tableau power trend line).

    D0 is excluded (ln 0 is undefined and r(0)=1 by definition). Zero or negative
    rates raise instead of being dropped silently, since dropping them biases the fit.
    """
    if len(n_values) != len(rates):
        raise ValueError("n_values and rates must be the same length")
    pts = [(n, r) for n, r in zip(n_values, rates) if n >= 1]
    if len(pts) < 2:
        raise ValueError("need at least two points with n >= 1")
    if any(r <= 0 for _, r in pts):
        raise ValueError("rates must be > 0 for a log-log fit; aggregate cohorts or cap n")
    xs = [math.log(n) for n, _ in pts]
    ys = [math.log(r) for _, r in pts]
    x_bar = sum(xs) / len(xs)
    y_bar = sum(ys) / len(ys)
    sxx = sum((x - x_bar) ** 2 for x in xs)
    if sxx == 0:
        raise ValueError("need at least two distinct n values")
    b = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / sxx
    a = math.exp(y_bar - b * x_bar)
    return PowerCurve(a=a, b=b, terminal_day=terminal_day)


def player_duration(curve: PowerCurve, n: int) -> float:
    """Expected distinct days played by day n: PD_n = sum_{i=0..n} r(i). Discrete sum."""
    if n < 0:
        raise ValueError("n must be >= 0")
    return sum(curve(i) for i in range(n + 1))


def ltv(retention: Sequence[float], arpdau: Sequence[float], margin: float = 1.0) -> float:
    """LTV_H = margin * sum_{d=0..H} R_d * ARPDAU_d.

    retention[d] is R_d (retention[0] = 1). arpdau[d] is revenue per retained player
    on day d, in the revenue scope the caller states. margin converts revenue to
    profit (e.g. 0.7 after a 30% platform fee); 1.0 returns revenue LTV.
    """
    if len(retention) != len(arpdau):
        raise ValueError("retention and arpdau must cover the same days")
    if not retention:
        raise ValueError("need at least day 0")
    if not 0 <= margin <= 1:
        raise ValueError("margin must be between 0 and 1")
    return margin * sum(r * v for r, v in zip(retention, arpdau))


def dau_series(installs_per_day: float, curve: PowerCurve, last_day_index: int) -> list[float]:
    """Expected DAU for days 0..last_day_index after launch, at a constant install rate.

    Ovans' recurrence DAU_n = r(n) * installs + DAU_{n-1}, so DAU_n = installs * PD_n.
    Day index 30 is the 31st day of installs. Values are unrounded.
    """
    if last_day_index < 0:
        raise ValueError("last_day_index must be >= 0")
    series: list[float] = []
    total = 0.0
    for n in range(last_day_index + 1):
        total += curve(n) * installs_per_day
        series.append(total)
    return series
