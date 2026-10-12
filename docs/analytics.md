# Analytics and scoring

All formulas below are implemented as pure functions in
[`app/modules/analytics/calculations.py`](../app/modules/analytics/calculations.py) and covered by unit
tests. This page is the reference for what every number in the API means.

## Conventions

- **Night of.** A session belongs to the local calendar date on which the night began. A session
  starting between 00:00 and 11:59 local time belongs to the previous date. `night_of` is computed in
  the timezone the session was recorded in and stored with the session.
- **Clock times as minutes.** Bedtimes and wake times are minutes after local midnight
  (`0 <= m < 1440`), computed in the user's *preference* timezone for analytics.
- **Nights vs sessions.** A night can have several sessions (a main sleep plus naps). Per-night values
  use the night's total duration; the bedtime and wake time of a night come from its longest session.
  Quality, latency, awakenings and mood are averaged over sessions.
- **Ranges.** `from`/`to` are inclusive local dates on `night_of`. The default is the last 30 days
  ending today; the maximum span is 400 days.
- **Goal.** Defaults until the user saves one: 480 minutes, bedtime 23:00, wake 07:00, window ±30 min.

## Circular statistics

Averaging clock times linearly breaks around midnight (the mean of 23:30 and 00:30 would be noon).
Each time `m` is mapped to an angle `θ = 2π · m / 1440` and averaged on the unit circle:

```
S = Σ sin θᵢ        C = Σ cos θᵢ        R = √(S² + C²) / n
mean = atan2(S, C) mapped back to minutes (undefined when R ≈ 0)
std  = √(−2 ln R) · 1440 / 2π            (circular standard deviation, in minutes)
diff(a, b) = shortest signed distance on the clock, in (−720, 720]
```

## Sleep score (0-100)

Each completed session gets a weighted score. Every component is in `[0, 1]`:

| Component | Weight | Formula |
|---|---|---|
| Duration | 40 % | `d ≥ T`: 1 if `d − T ≤ 60`, else `1 − (d − T − 60) / 180`. `d < T`: `1 − (T − d) / (T / 2)` |
| Quality | 25 % | `(q − 1) / 4` |
| Latency | 15 % | 1 if `l ≤ 15`, else `1 − (l − 15) / 45` |
| Awakenings | 10 % | 1 if `a ≤ 1`, else `1 − (a − 1) / 5` |
| Bedtime adherence | 10 % | `Δ = |diff(bedtime, target)|`; 1 if `Δ ≤ window`, else `1 − (Δ − window) / 90` |

All components are clamped to `[0, 1]`. `T` is the goal duration. Quality, latency and awakenings are
optional; when one is missing its weight is removed and the remaining weights are renormalised:

```
score = round(100 · Σ wᵢ·cᵢ / Σ wᵢ)     over the components that are present
```

Examples with the default goal:

| Night | Score |
|---|---|
| 8 h, quality 5, latency 10, 1 awakening, bed 23:10 | 100 |
| 8 h, quality 4, latency 10, 1 awakening, bed 22:30 | 94 (quality earns 0.75 of 25 points) |
| 6 h, no optional inputs, bed 23:00 | 60 ((0.5 · 0.4 + 1 · 0.1) / 0.5) |

Scores are stored for sorting and filtering and are recomputed in bulk when the user changes goals.

## Overview metrics

| Field | Definition |
|---|---|
| `nights_tracked` | Distinct `night_of` values with at least one completed session |
| `total_sleep_minutes` | Sum of nightly durations |
| `averages.duration_minutes` | Mean nightly duration |
| `averages.quality`, `mood_on_wake`, `awakenings` | Mean over sessions with a value (1 decimal) |
| `averages.score`, `sleep_latency_minutes` | Mean over sessions with a value (rounded) |
| `averages.bedtime`, `wake_time` | Circular mean of nightly bedtimes / wake times, `HH:MM` |
| `consistency_score` | `round(100 · clamp(1 − s / 120))` where `s` is the mean of the circular std-dev of bedtime and wake time in minutes; `null` with fewer than 2 nights |
| `goal_attainment_rate` | Share of nights with `duration ≥ T − 15` (2 decimals) |
| `sleep_debt_minutes` | `max(0, Σ (T − duration))` over tracked nights in the last 14 days of the range |
| `current_streak` | Consecutive goal-meeting nights ending at `to`; an unlogged night at `to` is skipped when `to` is today or yesterday (tonight can't be logged yet) |
| `longest_streak` | Longest run of consecutive goal-meeting nights in the range |
| `previous_period` | Duration, quality, score and consistency for the same number of days immediately before `from`; `null` when that period has no data |

Consistency reaches 0 at an average spread of two hours; a spread of 30 minutes scores 75.

Sleep debt nets surplus nights against deficits, reflecting that recovery sleep repays debt, and it
ignores untracked nights rather than counting them as zero sleep.

## Daily, weekday and distribution

- `/analytics/daily` returns one entry per date in the range, with `null` values and `sessions: 0`
  for untracked days.
- `/analytics/weekday` groups nights by the weekday of `night_of` (0 = Monday) and returns all seven
  weekdays, with circular means for bedtime and wake time.
- `/analytics/distribution` counts completed sessions per duration bucket (`<5h`, `5-6h`, `6-7h`,
  `7-8h`, `8-9h`, `9h+`; lower bound inclusive), per quality rating and per local bedtime hour. All
  buckets are always present.

## Tag impact

For each tag used in the range, sessions are split into those with and without the tag:

```
quality_impact          = mean quality with tag − mean quality without (2 decimals)
duration_impact_minutes = mean duration with tag − mean duration without (rounded)
```

Results are sorted by `|quality_impact|`, then by how often the tag was used. These are associations,
not causal effects; the insight rules only speak up when both groups have at least three sessions.

## Chronotype and social jetlag

Based on the Munich ChronoType Questionnaire (MCTQ) idea of mid-sleep on free days:

- Mid-sleep of a session = local bedtime + duration / 2.
- Free nights are Friday and Saturday nights; all others are work nights.
- `MSF` = circular mean mid-sleep on free nights, `MSW` on work nights (each needs 2+ nights).
- When free nights are longer than work nights, `MSF` is corrected for oversleep:
  `MSFsc = MSF − (SDf − SDweek) / 2` with `SDweek = (5·SDw + 2·SDf) / 7`.
- `social_jetlag_minutes = |diff(MSF, MSW)|`.
- Classification uses `MSFsc` (or the overall mid-sleep if there are no free nights):
  before 03:00 is `early_bird`, after 05:00 is `night_owl`, otherwise `intermediate`.
  At least 7 nights are required, otherwise `unknown`.

`mid_sleep` in the response is the uncorrected `MSF`.

## Insight rules

The default provider evaluates these rules in order, removes duplicates and sorts by priority:

| Id | Fires when | Kind |
|---|---|---|
| `not-enough-data` | fewer than 3 nights | info |
| `sleep-debt-high` | sleep debt ≥ 5 h | warning |
| `duration-below-goal` / `duration-meets-goal` | average ≥ 30 min short / within 15 min or above | warning / positive |
| `consistency-low` / `consistency-high` | consistency < 60 / ≥ 85 (3+ nights) | warning / positive |
| `goal-attainment-low` | attainment < 40 % (7+ nights) | warning |
| `score-improving` / `score-declining` | average score moved by ≥ 5 vs previous period | positive / warning |
| `quality-high` / `quality-low` | average quality ≥ 4.0 / < 2.8 | positive / warning |
| `tag-impact-<tag>` | `|quality_impact| ≥ 0.4` with 3+ sessions on each side (top 3 tags) | warning / positive |
| `tag-duration-<tag>` | tag shortens sleep by ≥ 30 min (when no quality insight fired) | warning |
| `bedtime-later-than-target` / `-earlier-` | average bedtime outside the window by more than 15 min | warning / info |
| `social-jetlag` | social jetlag ≥ 60 min | tip |
| `latency-high` | average latency > 30 min | tip |
| `goal-streak` | current streak ≥ 5 | positive |
| `weekend-catch-up` | Fri/Sat nights ≥ 60 min longer than other nights | info |
| `tip-consistent-wake-time` | always (lowest priority) | tip |

## Age-based recommendations

`GET /goals` includes the National Sleep Foundation range for the user's age (from
`date_of_birth`):

| Age | Group | Hours |
|---|---|---|
| 0-3 months | newborn | 14-17 |
| 4-11 months | infant | 12-15 |
| 1-2 years | toddler | 11-14 |
| 3-5 years | preschool | 10-13 |
| 6-13 years | school_age | 9-11 |
| 14-17 years | teen | 8-10 |
| 18-25 years | young_adult | 7-9 |
| 26-64 years | adult | 7-9 |
| 65+ years | older_adult | 7-8 |

Without a date of birth the adult range (7-9 h) is returned with `age_group: null`.
