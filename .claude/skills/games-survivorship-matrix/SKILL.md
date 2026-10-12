---
name: games-survivorship-matrix
description: >-
  Build and read a game retention triangle: weekly or daily first-session
  cohorts as rows, W0..W12 or D0/D1/D7/D30/D90 as columns, with the launch
  (golden) cohort, partial and immature cells marked, small cohorts greyed
  and a weighted-average row. Use when asked for cohort retention, a
  survivorship matrix, a retention heatmap, or whether newer cohorts retain
  worse than launch. Not for defining retention metrics
  (games-metric-dictionary), time to churn or survival curves
  (survival-analysis), or acquisition-source cohorts (cohort-builder).
---

# Skill: Games Survivorship Matrix

## Purpose
Build and read the retention triangle: cohorts as rows, periods since first
session as columns, each cell the share of the cohort that came back. The code
does the counting; this skill says how to set it up, what to check, and how to
report it.

## Defaults
| Setting | Default | Options |
|---|---|---|
| Columns (`period`) | `week`: W0..W12 | `day`: D0, D1, D7, D30, D90; or any list via `periods` |
| Cell definition | Week: active at least once on days 7k..7k+6 after the player's own first session (`retention_dn_bracket`). Day: active on exactly day n (`retention_dn_strict`) | Rolling retention is a different metric; don't put it in this table |
| Cohort rows (`cohort_grain`) | `week` (ISO weeks, Monday start) | `day`, `month` |
| Golden cohort | A highlighted row when `release_date` is given: first session within `launch_window_days` (7) of release | Comparison only; never in the averages |
| Small cohorts | Rows under `min_cohort_size` (100) are greyed out | Raise it for noisy data |
| Day boundary | `utc` | `reporting_tz` (name the timezone), `player_local` |
| Identity | Cohort entry = first session, player key = `account_id` | Pass `first_sessions` from an authoritative source |

D0 and W0 are 100% by definition.

## Procedure

### 1. Frame
Write down before computing:
- the question (e.g., "are newer cohorts retaining worse than launch?") and
  the hypothesis;
- the title, the player population and any exclusions (test accounts, bots,
  regions);
- the columns, cohort grain and day boundary. Use the defaults unless the
  question needs otherwise, and say why if you change them.

### 2. Check the input
- One row per player per active day, with a player key and a calendar date in
  the chosen day boundary. Convert timestamps to dates in that boundary
  first; don't mix boundaries.
- Activity must reach back to each player's real first session. If the data
  starts after the game launched, pass `first_sessions` or say that the early
  cohorts include returning players. The helper warns when it falls back to
  "first activity in the data".
- Identity: reinstalls and second accounts enter as new players unless
  deduplicated. Say which key you used.

### 3. Build it
```python
from helpers.games.survivorship import build_survivorship_matrix
m = build_survivorship_matrix(activity, as_of=as_of, period="week",
                              release_date=release_date)
m.to_display()   # stepped table for the report
m.plot()         # heatmap (partial cells hatched, small cohorts grey)
m.meta           # definition labels and warnings for the output
```
`as_of` is the timezone-aware instant the data runs to. It decides which cells
are mature; never use "today" when the extract is older.

### 4. Read it
- Read down a column (same age, different cohorts) to see whether retention
  is improving or getting worse. Read across a row for one cohort's decay.
- Compare each cohort with the golden row at the same age, not with the
  average.
- A partial cell (`*`, hatched) uses only the members old enough for it. Its
  members joined earlier in the cohort week; say so before reading a trend
  off the newest cell in a row.
- A blank cell is immature, not zero. Never fill it.
- The weighted-average row pools each column over the cohorts that have data
  there, and prints the count. Later columns average fewer, older cohorts.
  If cohort quality changed (a UA push, a sale, a patch), the row can show a
  trend that no single cohort has. Use `balanced=True` to check: it pools only
  cohorts complete in every column.
- Greyed rows are below the minimum size. Don't draw conclusions from them.

### 5. Check before reporting
- Day boundary, as_of and identity are printed with the table.
- Any warning in `m.meta["warnings"]` is in the output with how it was handled.
- The cohorts you compare have the same mix (platform, region, acquisition
  source) or the difference is called out.
- Events in the window (patches, sales, outages, featuring) are listed next to
  the cohorts they touch.

## Output
1. Definition block: metric ID, cell definition, columns, cohort grain, day
   boundary, as_of, identity, minimum cohort size.
2. The stepped table, golden row first and the average row last.
3. The heatmap.
4. Two or three findings, each naming the cells it rests on and whether any
   are partial.
5. Warnings and caveats.

## Not this skill
- Defining or changing a retention metric: games-metric-dictionary.
- Building cohorts by acquisition source or reinstall rules, or deduplicating
  players across platforms: cohort-builder (planned).
- Time to churn, Kaplan-Meier curves, hazard rates: survival-analysis (planned).
