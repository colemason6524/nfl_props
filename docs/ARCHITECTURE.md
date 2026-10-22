# Module map

## Live system (forecast-first) — the production product

The board publishes a price-free forecast for every scheduled game first, then
attaches Bovada/Polymarket references to label value (`PLAYABLE` / `NO_VALUE` /
`UNPRICED`). Product rules: [`PRODUCT_CONTRACT.md`](PRODUCT_CONTRACT.md).

```
nfl_props/
  config.py              paths (incl. FORECAST_HISTORY_DIR, SNAPSHOTS_DIR,
                         LEDGER_DIR, FORECAST_BACKTESTS_DIR), seasons, URLs
  teams.py               canonical abbrs + alias normalization (never force-match)
  pricing.py             American/decimal, proportional devig, EV with pushes
  version.py             history schema / model / grading / policy versions
  cli.py                 refresh-data | build | rebuild-state | rebuild-state-v2
                         | rebuild-forecast-state | backfill-weather
  sources/
    nflverse.py          pbp parquet + games.csv ingest -> canonical store
                         (team_games + games + team_situational_games +
                          qb_games), merge-coverage diagnostics
    bovada.py            live lines (spread/ML/total + team totals), validated
                         fail-open cache, per-event fetch
    polymarket.py        fallback prices when Bovada has no event
    weather.py           Open-Meteo game-day weather store (total head input)
    personnel.py         injury/personnel feed store (fail-open scaffold)
    personnel_feed.py    ESPN personnel pull -> personnel.json
    depth.py             depth-chart pull -> starter/backup attribution
  forecasting.py         point-in-time feature build + three price-free heads
                         (winner logistic, margin OLS, total OLS) ->
                         data/processed/forecast_state.json
  forecast.py            one game -> GameForecast (heads + internal
                         disagreement + pick per family)
  references.py          market reference attachment: consensus price, edge,
                         EV, PLAYABLE / NO_VALUE / UNPRICED labelling
  forecast_board.py      schedule -> one GameForecast per game; Discord
                         three-section render
  forecast_output.py     export_history() (outputs/forecast_history/),
                         latest_history(), append_ledger()
  snapshots.py           per-game pregame snapshots (outputs/snapshots/),
                         latest_snapshot_before(), cleanup_snapshots()
  delta.py               pre-kickoff injury delta check + tiered alerts
  injury_debit.py        injury/positional debits applied to the model input
  schedule.py            schedule/kickoff resolution, ET day-of-board rule
  rivalry.py             division/rivalry context features
  notifiers/
    forecast_discord.py  board + recap webhook delivery, chunking, gating
    discord.py           ops alerts (send_ops_alert)

run_forecast_board.py    live board entry point (--wave, --date, --discord)
grade_forecast.py        official performance record: season report
                         (grade_all/render_report) + weekly public recap
                         (grade_week/render_weekly_recap, --weekly)
backtest_forecast.py     leak-free tune/holdout validation of the forecast heads

outputs/forecast_history/  nfl_forecast_*.json — THE record (every board run)
outputs/snapshots/         <game-date>/<forecast_id>.json — last bettable
                           pregame pick state; grading prefers these
outputs/forecast_backtests/ grade_forecast_*.{txt,json} (season) and
                           grade_weekly_<season>w<NN>_*.{txt,json} (weekly)
outputs/ledger/            forecast_ledger.jsonl — write-only print log, NOT
                           a performance source (no reader)
outputs/diagnostics/       bovada coverage + build diagnostics
```

## Legacy lane (v1 / v2 shadow) — retired operationally

Kept for historical cohort grading only; nothing scheduled runs it, and
`outputs/history/` has been frozen since 2026-09-12. Its artifacts are never
pooled with the forecast cohort.

```
nfl_props/
  config.py              paths, seasons (tune/holdout/current), nflverse URLs
  teams.py               canonical abbrs + alias normalization (never force-match)
  utils.py               fetch/download helpers, logging
  pricing.py             American/decimal, proportional devig, EV with pushes
  version.py             history schema / model / tier policy versions
  cli.py                 refresh-data | build | rebuild-state | rebuild-state-v2
  sources/
    nflverse.py          pbp parquet + games.csv ingest, canonical store
                         (team_games + games + team_situational_games +
                          qb_games), merge-coverage diagnostics
    bovada.py            live NFL lines (spread/ML/total + team totals),
                         fail-open cache, per-event fetch
  ratings/
    epa.py               v1 model: point-in-time replay, EWMA opponent-adjusted
                         EPA ratings, OLS points projection, empirical residual
                         distributions, per-market calibration shrink,
                         live_state.json build/load
    v2.py                v2 shadow candidate: named features, situational
                         pass/rush + schedule + lag-starter QB replay, v2 fit,
                         live_state_v2_shadow.json, live shadow projections
  board.py               screen live games -> tiered candidates + projections
  tiers.py               Core/Lean/Watch gates (EV window, edge, sample)
  output.py              terminal board + history JSON export (incl. v2 shadow)
  notifiers/discord.py   Core digest, NFL_DISCORD_WEBHOOK_URL, default off

run_board.py             live board entry point (v1 tiers + v2 shadow attach)
backtest.py              v1 tune/holdout backtest vs closing lines
backtest_v2.py           v2 feature-set ablation backtest vs closing lines
grade.py                 grade v1 candidates + compare latest pre-kickoff
                         v1/v2 score projections vs final scores

data/raw/                nflverse downloads (gitignored)
data/processed/          team_games.parquet, games.parquet,
                         team_situational_games.parquet, qb_games.parquet,
                         live_state.json, live_state_v2_shadow.json
outputs/history/         nfl_board_*.json (legacy v1 board, frozen 2026-09-12)
outputs/backtests/       backtest_*.txt, backtest_v2_*.txt, grade_*.txt (legacy)
outputs/diagnostics/     build + bovada coverage diagnostics
scripts/                 Linux task wrappers (run_nfl_linux_task.sh)
tests/                   unittest suite, run with `.venv/bin/python -m pytest -q`
```

## Layer separation (do not mix)

| Layer | Source of truth | Never contains |
|---|---|---|
| Performance model (v1) | `ratings/epa.py` | odds, Discord, display labels |
| Performance model (v2 shadow) | `ratings/v2.py` | tier gates, v1 state |
| Value layer | `pricing.py` + `board.py` | model changes |
| Tiers | `tiers.py` | sorting/presentation |
| Presentation | `output.py`, `notifiers/` | thresholds |
| History (record) | `forecast_output.py::export_history` | derived views |
| Grading | `grade_forecast.py` | auto-retraining |
| Legacy v1/v2 lane | `board.py` + `output.py` history | forecast cohort numbers |

## Key conventions

- Canonical team IDs are current nflverse abbrs (`LA` = Rams). All joins go
  through `teams.normalize_team`; a failed lookup is surfaced, never guessed.
- `games.csv` `spread_line` is the home-team margin expectation (positive =
  home favored). Bovada spread lines are handicaps (home -3.5). Conversion
  happens exactly once at each boundary.
- `p_model` is the raw model probability and drives selection/tiers.
  `p_model_cal` (tune-fit shrink toward 0.5, per market) is the honest
  calibration estimate and is exported for research.
- Every probability function returns explicit push mass; EV treats pushes as
  stake returned.
- The Bovada fetcher (`sources/bovada.py`) is fail-open but validated: it
  returns `FetchResult` metadata (mode/attempts/status/bytes/age/error),
  retries once on transient failures, never lets a malformed response
  overwrite the last-good cache, and raises when no valid cache exists.
  `fetch_live_games` also drops past-kickoff or missing-kickoff events so a
  stale board can't offer an already-started game. Team-total parse coverage
  is captured as structured diagnostics for game-week verification.

## v2 shadow model — boundaries

`ratings/v2.py` is a research-only parallel path. It never writes
`live_state.json`, never changes `p_model`, tiers, or `MODEL_VERSION`, and its
output (`live_state_v2_shadow.json`, history `v2_shadow`, `backtest_v2.py`)
is for prospective grading. The legacy `grade.py` compares v1/v2 points,
margin, and total MAE on every resolved regular-season game; it does not assign
v2 bets or ROI. Its base off/def/pace ratings are numerically
identical to v1 (asserted by `tests/test_v2.py::BaseParityTests`).

Data definitions for the added metrics:

- dropback = `qb_dropback == 1` (sacks and scrambles included); `passer_id`
  preferred over `passer_player_id` for scramble coverage.
- rush excludes kneels/spikes; every rate keeps its raw denominator so ratings
  can shrink low samples toward a league prior.
- explosive pass = gain >= 15 yds, explosive rush = gain >= 10 yds.
- QB starter = lag-based proxy: highest-volume passer of the team's
  immediately prior completed game (leak-free; unknown/absent -> `qb_uncertain`,
  feature contributes zero). Live, the QB family stays neutral until a real
  pregame starter source exists.
- schedule features: `rest_days`, `opp_rest_days` (fill 7), `neutral_site`,
  `dome` (roof closed/dome).
