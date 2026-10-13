"""Tests for helpers/games/metrics.py.

Worked examples come from Ovans, "Game analytics 100: The retention curve"
(project file russell-ovans-gameanalytics-retention.pdf). Where Ovans is wrong
or ambiguous, the test says so and the correction is logged in the registry.
"""
import math
from datetime import date, datetime, timedelta, timezone

import pytest

from helpers.games.metrics import (
    PowerCurve,
    dau_series,
    day_complete_at,
    dn_retention,
    fit_power_curve,
    ltv,
    player_duration,
    pooled_retention_profile,
)

UTC = timezone.utc
OVANS_EXAMPLE = PowerCurve(a=0.4, b=-0.5)       # Ovans' illustrative curve
OVANS_FITTED = PowerCurve(a=0.372, b=-0.442)    # Ovans' fitted curve (p.12)


# --- Strict Dn retention -------------------------------------------------

def test_d7_ovans_fixture_is_16_26_percent():
    # Ovans p.11 prints 6.26%; his own table (0.1626) and the arithmetic say 16.26%.
    result = dn_retention(1283, 7891, n=7, cohort_date=date(2023, 7, 1),
                          as_of=datetime(2023, 7, 9, tzinfo=UTC))
    assert result.status == "ok"
    assert result.day_boundary == "utc"
    assert result.value == pytest.approx(0.1626, abs=5e-5)


def test_unbaked_cohort_is_none_not_zero():
    # D7 for a July 1 cohort is complete only after July 8 ends (UTC midnight July 9).
    result = dn_retention(0, 7891, n=7, cohort_date=date(2023, 7, 1),
                          as_of=datetime(2023, 7, 8, 23, 59, tzinfo=UTC))
    assert result.value is None
    assert result.status == "immature"


def test_player_local_matures_12_hours_after_utc_midnight():
    day = date(2023, 7, 8)
    assert day_complete_at(day, "player_local") == datetime(2023, 7, 9, 12, tzinfo=UTC)
    just_before = dn_retention(10, 100, n=7, cohort_date=date(2023, 7, 1),
                               as_of=datetime(2023, 7, 9, 11, 59, tzinfo=UTC),
                               day_boundary="player_local")
    at_cutoff = dn_retention(10, 100, n=7, cohort_date=date(2023, 7, 1),
                             as_of=datetime(2023, 7, 9, 12, tzinfo=UTC),
                             day_boundary="player_local")
    assert just_before.status == "immature" and just_before.day_boundary == "player_local"
    assert at_cutoff.value == pytest.approx(0.10)


def test_reporting_tz_uses_that_timezone_midnight():
    pacific_daylight = timezone(timedelta(hours=-7))
    assert day_complete_at(date(2023, 7, 8), "reporting_tz", pacific_daylight) == \
        datetime(2023, 7, 9, 7, tzinfo=UTC)
    with pytest.raises(ValueError):
        day_complete_at(date(2023, 7, 8), "reporting_tz")


@pytest.mark.parametrize("active, size, n", [(1, 10, 0), (11, 10, 1), (1, 0, 1), (-1, 10, 1)])
def test_dn_retention_rejects_invalid_inputs(active, size, n):
    with pytest.raises(ValueError):
        dn_retention(active, size, n=n, cohort_date=date(2023, 1, 1),
                     as_of=datetime(2024, 1, 1, tzinfo=UTC))


def test_naive_as_of_is_rejected():
    with pytest.raises(ValueError):
        dn_retention(1, 10, n=1, cohort_date=date(2023, 1, 1), as_of=datetime(2024, 1, 1))


# --- Pooled retention profile ---------------------------------------------

def test_pooled_profile_weights_by_cohort_size_not_mean_of_rates():
    cohorts = [
        {"cohort_size": 100, "active": {1: 50}},
        {"cohort_size": 1000, "active": {1: 100}},
    ]
    profile = pooled_retention_profile(cohorts)
    assert profile[1] == pytest.approx(150 / 1100)      # 13.6%
    assert profile[1] != pytest.approx((0.5 + 0.1) / 2)  # not 30%


def test_pooled_profile_skips_immature_cohorts_per_day():
    cohorts = [
        {"cohort_size": 100, "active": {1: 40, 7: 20}},
        {"cohort_size": 300, "active": {1: 90, 7: None}},   # too young for D7
    ]
    profile = pooled_retention_profile(cohorts)
    assert profile[1] == pytest.approx(130 / 400)
    assert profile[7] == pytest.approx(20 / 100)


# --- Power curve -----------------------------------------------------------

@pytest.mark.parametrize("n, expected", [(7, 0.1512), (53, 0.0549), (180, 0.0298)])
def test_ovans_example_curve_values(n, expected):
    assert OVANS_EXAMPLE(n) == pytest.approx(expected, abs=5e-5)


def test_curve_day_zero_is_one_and_terminal_day_zeroes_the_tail():
    curve = PowerCurve(a=0.4, b=-0.5, terminal_day=30)
    assert curve(0) == 1.0
    assert curve(30) > 0
    assert curve(31) == 0.0


def test_fit_recovers_known_curve_exactly():
    truth = PowerCurve(a=0.35, b=-0.45)
    ns = list(range(1, 31))
    fitted = fit_power_curve(ns, [truth(n) for n in ns])
    assert fitted.a == pytest.approx(0.35, rel=1e-9)
    assert fitted.b == pytest.approx(-0.45, rel=1e-9)


def test_fit_excludes_day_zero():
    truth = PowerCurve(a=0.35, b=-0.45)
    ns = list(range(1, 31))
    with_d0 = fit_power_curve([0] + ns, [1.0] + [truth(n) for n in ns])
    assert with_d0.a == pytest.approx(0.35, rel=1e-9)


def test_fit_on_ovans_published_rows_is_close_to_his_curve():
    # Ovans fits 0.372 * n^-0.442 on D1-D30 but prints only D0-D7 and D28-D30
    # (p.10). The published rows alone give about 0.371 * n^-0.448, so the
    # tolerance on b is wider than on a. The exact-recovery test above is the
    # real check on the fitting code.
    ns = [1, 2, 3, 4, 5, 6, 7, 28, 29, 30]
    actives = [2929, 2120, 1769, 1559, 1409, 1326, 1283, 658, 651, 621]
    fitted = fit_power_curve(ns, [a / 7891 for a in actives])
    assert fitted.a == pytest.approx(0.372, abs=0.002)
    assert fitted.b == pytest.approx(-0.442, abs=0.01)


def test_fit_rejects_zero_rates():
    with pytest.raises(ValueError):
        fit_power_curve([1, 2, 3], [0.3, 0.0, 0.1])


# --- Player duration ---------------------------------------------------------

def test_player_duration_is_discrete_sum_matching_ovans():
    # Ovans p.17: sum_ret_curve(a=0.372, b=-0.442, n=30) ends 4.855 4.939 5.021.
    assert player_duration(OVANS_FITTED, 28) == pytest.approx(4.855, abs=5e-4)
    assert player_duration(OVANS_FITTED, 29) == pytest.approx(4.939, abs=5e-4)
    assert player_duration(OVANS_FITTED, 30) == pytest.approx(5.021, abs=5e-4)


def test_player_duration_is_not_the_example_curve():
    # Guard against mixing the two Ovans curves: a=0.4, b=-0.5 gives 4.834, not 5.021.
    assert player_duration(OVANS_EXAMPLE, 30) == pytest.approx(4.834, abs=5e-4)


def test_player_duration_stops_growing_after_terminal_day():
    curve = PowerCurve(a=0.4, b=-0.5, terminal_day=10)
    assert player_duration(curve, 50) == pytest.approx(player_duration(curve, 10))


# --- LTV ------------------------------------------------------------------------

def test_ltv_hand_example():
    assert ltv([1.0, 0.4, 0.3], [1.0, 1.0, 1.0], margin=0.7) == pytest.approx(0.7 * 1.7)


def test_ltv_equals_player_duration_times_flat_arpdau():
    # Ovans p.18: LTV30 = 5.021 * $1.00 = $5.02 when ARPDAU is flat at $1.
    retention = [OVANS_FITTED(d) for d in range(31)]
    assert ltv(retention, [1.0] * 31) == pytest.approx(player_duration(OVANS_FITTED, 30))


@pytest.mark.parametrize("margin", [-0.1, 1.5])
def test_ltv_rejects_bad_margin(margin):
    with pytest.raises(ValueError):
        ltv([1.0], [1.0], margin=margin)


def test_ltv_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        ltv([1.0, 0.4], [1.0], margin=1.0)


# --- DAU from constant installs --------------------------------------------------

def test_dau_matches_ovans_small_table():
    # Ovans p.13: 100 installs/day on r(n) = 0.4 n^-0.5 gives 100, 140, 168.
    series = dau_series(100, OVANS_EXAMPLE, 2)
    assert [round(x) for x in series] == [100, 140, 168]


def test_dau_matches_ovans_r_output_day_by_day():
    # Ovans p.16: dau(installs=500, a=0.372, b=-0.442, n=30) prints 31 values
    # (day indexes 0..30), truncated to integers. 2,510 is day index 30, i.e.
    # after 31 days of installs, not 30.
    ovans = [500, 686, 822, 937, 1038, 1129, 1213, 1292, 1366, 1437, 1504,
             1568, 1630, 1690, 1748, 1804, 1859, 1912, 1964, 2014, 2064,
             2112, 2160, 2206, 2252, 2297, 2341, 2384, 2427, 2469, 2510]
    series = dau_series(500, OVANS_FITTED, 30)
    assert len(series) == 31
    assert [math.floor(x) for x in series] == ovans


def test_dau_equals_installs_times_player_duration():
    series = dau_series(500, OVANS_FITTED, 30)
    assert series[30] == pytest.approx(500 * player_duration(OVANS_FITTED, 30))
