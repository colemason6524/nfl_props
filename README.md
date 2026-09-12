# nfl_props

NFL **forecast-first** board: the model publishes an independent opinion for
every scheduled game on the current day — **moneyline, spread, total** — before
looking at a sportsbook. Bovada (primary) and Polymarket (fallback) lines and
prices are attached afterwards only to describe value (`PLAYABLE` / `NO_VALUE` /
`UNPRICED`). The model decides the pick; the price determines the action.

The authoritative product rules live in [`docs/PRODUCT_CONTRACT.md`](docs/PRODUCT_CONTRACT.md).

## What it does

- Replays opponent-adjusted, play-based EPA (overall + pass/rush) and pace from
  free nflverse data, point-in-time.
- Fits **three separate price-free models** each owning its own target:
  - `winner` — logistic P(home win) → moneyline pick
  - `margin` — ridge OLS on (home − away) → spread side
  - `total` — ridge OLS on (home + away) → over/under side
- Adds schedule context (rest, dome, neutral, division), lagged quarterback
  quality, and point-in-time head-to-head history. Weather is plumbed through
  the total head (neutral until the weather store is populated).
- Creates a weekly NFL-universe forecast even when a game is unpriced; internal
  disagreement between the three heads is shown, not hidden.
- Grades forecast accuracy (winner accuracy, Brier, log loss, margin/total MAE)
  separately from flat-1u reference ROI at captured prices.
- Continues into the postseason.

## Status (2026-09-12)

| Phase | Status |
|---|---|
| Legacy price-screened board (`run_board.py`, Core/Lean/Watch) | **retired operationally** (historical grading only) |
| Forecast-first models (`nfl-forecast-v1.1`) | **done** |
| Current-day schedule board + Bovada/Polymarket references | **done** |
| Discord three-section board (no truncation) | **done** |
| Forecast grading + ROI (`grade_forecast.py`) | **done** |
| Forecast backtest (`backtest_forecast.py`) | **done** |
| Weather store populated + total head (`cli backfill-weather`) | **done** (Open-Meteo; improves total MAE) |
| Personnel / injury feed | **deferred** (fail-open scaffold) |
| Player props | **deferred** |

## Pipeline (forecast-first)

```
refresh-data           ->  data/raw/play_by_play_*.parquet + games.csv
build                  ->  data/processed/{games,team_games,team_situational_games,qb_games}.parquet
rebuild-forecast-state ->  data/processed/forecast_state.json  (winner/margin/total)
run_forecast_board     ->  today's schedule -> price-free forecasts
                       ->  Bovada + Polymarket references -> consensus edge/EV
                       ->  terminal board + outputs/forecast_history/nfl_forecast_*.json
grade_forecast         ->  latest pre-kickoff forecast vs final scores + reference ROI
backtest_forecast      ->  leak-free tune/holdout forecast validation
```

Legacy artifacts remain in `outputs/history/` and are graded by `grade.py`;
they are never pooled with the forecast cohort.

## Quick start

```bash
cd /path/to/nfl_props
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# One-time / weekly data + model rebuild
.venv/bin/python -m nfl_props.cli refresh-data
.venv/bin/python -m nfl_props.cli build
.venv/bin/python -m nfl_props.cli rebuild-forecast-state

# Forecast board for the current day (add --discord to post)
.venv/bin/python run_forecast_board.py
.venv/bin/python run_forecast_board.py --date 2026-09-13   # override ET day

# Grade + historical validation
.venv/bin/python grade_forecast.py
.venv/bin/python backtest_forecast.py
```

Run schedule (America/New_York): Thursday evening (TNF), Sunday morning (early
slate), Sunday afternoon (remaining evening slate), Monday evening (MNF). Each
run publishes only the current day's not-yet-started games.

## Action labels (current defaults)

The model pick is fixed before pricing. The reference price only describes
whether the model's side has positive expected value.

| Label | Rule |
|---|---|
| `PLAYABLE` | model EV at the reference price ≥ `NFL_VALUE_PLAYABLE` (default 0%) |
| `NO_VALUE` | a price exists but EV is below the threshold |
| `UNPRICED` | no usable reference price captured |

Numeric model probability, consensus market probability, edge, and EV are
always shown. Env override: `NFL_VALUE_PLAYABLE`.

## Important honesty notes

- **NFL closing spreads/totals are the most efficient lines in sports.** The
  forecast-first shift improves decision integrity and coverage; it is not a
  claim that the model beats the market. Backtest holdout (2023–2025) is
  logged in `outputs/forecast_backtests/`.
- The model's pick is never flipped by a price, and the opposite side is never
  promoted because it looks like "value."
- NFL ties settle as pushes (stake returned), never losses.
- Early-season forecasts lean on last season's play-based ratings (60%
  carryover). Week 1's snapshot is an evaluation sample, not a tuning trigger.
- Do not retune thresholds from a handful of graded games; log findings first.
