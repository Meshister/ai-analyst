---
name: games-metric-dictionary
description: >-
  Look up and apply the games metric dictionary before computing or citing any
  game metric: retention (strict, rolling, bracket, period), churn, DAU/MAU,
  ARPDAU, ARPU, ARPPU, payer conversion, LTV, player duration, units, net
  revenue, ASP, discount lift, DLC attach, refunds, wishlists, catalog share,
  forecast error. Use when an analysis names a game KPI, when two sources
  disagree on a definition, or when a metric must be added. Not for a
  dataset's own metric files (use metrics) or generic metric templates (use
  metric-spec).
---

# Skill: Games Metric Dictionary

## Purpose
One definition per game metric, shared by every games agent. Agents never
define metrics on their own: they look the metric up here, compute it the way
the file says, and label the output with the choices the file requires.

## Files
| Path | What it holds |
|---|---|
| `registry/<metric_id>.yaml` | One metric per file: definition, formula, parameters, day boundary, maturity rule, retention type, revenue scope, payer window, identity basis, tags, caveats, sources, corrections |
| `registry/_schema.yaml` | The allowed values for every field. Read it before writing or editing a metric |
| `registry/_backlog.yaml` | Every KPI named in the reference docs, with its planned ID, owning agent and phase |
| `helpers/games/metrics.py` | Tested code for retention, maturity, pooled profiles, power curves, player duration, LTV and DAU |

Paths under `registry/` are relative to this skill's folder,
`.claude/skills/games-metric-dictionary/`.

## Procedure

### 1. Find the metric
1. Match the name the user or analysis used against file names, then against
   each file's `aliases`.
2. If the name is ambiguous, stop and resolve it. Common traps:
   - "conversion": `payer_conversion_period` (share of actives who paid this
     period) vs `payer_conversion_cohort_day_n` (share of a cohort who have
     paid by day N).
   - "retention": `retention_dn_strict`, `_rolling`, `_bracket`, or
     `retention_period` (existing base). The files' `distinct_from` lists name
     the neighbours.
   - "MAU": `mau_rolling` vs calendar-month MAU.
   - "stickiness" used to mean Dn retention: it is `stickiness_dau_mau`.
3. If the user's meaning is clear, use that ID and say which one. If not, ask.

### 2. Read the file and state the definition
Before any number, write a definition block in the output:

```
Metric: <metric_id> (<display_name>)  status: <active|draft>
Formula: <formula>
Parameters: <each parameter and the value used>
Day boundary: <utc | player_local | reporting_tz (name the timezone)>
Retention type: <value>   Revenue scope: <streams, basis>   Payer window: <value>
Identity: cohort entry = <cohort_event>, player key = <player_key>
```

Fields that say `not_applicable` can be left out of the block. A `draft`
metric is labeled "draft definition" wherever it appears.

### 3. Compute it
- Use the helper when one exists (`dn_retention`, `pooled_retention_profile`,
  `fit_power_curve`, `player_duration`, `ltv`, `dau_series`). Otherwise
  follow the file's formula exactly.
- Apply the `maturity_rule`: a value that is not yet complete is `null`
  (reported as "immature" or "not yet available"), never 0.
- Ratios across cohorts or days are ratios of sums, not averages of ratios,
  unless the file says otherwise.

### 4. Check the caveats
Read every caveat in the file. Put in the output each one that applies to this
analysis, and say how it was handled.

### 5. Metric not in the registry
1. Search `registry/_backlog.yaml` for the name.
2. If it is there, say it is not yet defined, and give its owner and phase.
3. Do not improvise a definition silently. Either:
   - use the closest defined metric, name it, and say how it differs; or
   - add the metric (below) and use it.

## Adding or changing a metric
1. Copy the closest existing file and rename it to the new `metric_id`.
2. Fill every field. Use values from `registry/_schema.yaml`; write
   `not_applicable` for a field that does not apply, never leave it blank.
3. Same name, different meaning: give it a new ID and list each in the
   other's `distinct_from`.
4. New metrics without a published source get `status: draft` and a `sources`
   entry saying so.
5. If a reference doc is wrong, fix it in the metric and log it under
   `corrections`. Never edit the reference doc.
6. If the KPI is in `_backlog.yaml`, set its `status: built` and `planned_id`
   to the new ID.
7. Run `python -m pytest tests/games -q`. The schema and backlog tests must pass.
8. A formula added to `helpers/games/metrics.py` needs a test in
   `tests/games/test_metrics_formulas.py`, using a published worked example
   where one exists.

## Rules
- Never swap in a different metric without naming it.
- Every output that depends on days names its day boundary.
- Every revenue output names its revenue scope and basis.
- Player-local days are immature until about 12 hours after the next UTC
  midnight.
