# Module map

```
nfl_props/
  config.py              paths, seasons (tune/holdout/current), nflverse URLs
  teams.py               canonical abbrs + alias normalization (never force-match)
  utils.py               fetch/download helpers, logging
  pricing.py             American/decimal, proportional devig, EV with pushes
  version.py             history schema / model / tier policy versions
  cli.py                 refresh-data | build | rebuild-state
  sources/
    nflverse.py          pbp parquet + games.csv ingest, canonical store,
                         merge-coverage diagnostics
    bovada.py            live NFL lines (spread/ML/total + team totals),
                         fail-open cache, per-event fetch
  ratings/
    epa.py               point-in-time replay, EWMA opponent-adjusted EPA
                         ratings, OLS points projection, empirical residual
                         distributions, per-market calibration shrink,
                         live_state.json build/load
  board.py               screen live games -> tiered candidates + projections
  tiers.py               Core/Lean/Watch gates (EV window, edge, sample)
  output.py              terminal board + history JSON export
  notifiers/discord.py   Core digest, NFL_DISCORD_WEBHOOK_URL, default off

run_board.py             live board entry point
backtest.py              tune/holdout backtest vs closing lines
grade.py                 grade history snapshots vs final scores

data/raw/                nflverse downloads (gitignored)
data/processed/          team_games.parquet, games.parquet, live_state.json
outputs/history/         nfl_board_*.json (every run, full snapshot)
outputs/backtests/       backtest_*.txt, grade_*.txt
outputs/diagnostics/     build + bovada coverage diagnostics
scripts/                 Windows Task Scheduler wrappers
```

## Layer separation (do not mix)

| Layer | Source of truth | Never contains |
|---|---|---|
| Performance model | `ratings/epa.py` | odds, Discord, display labels |
| Value layer | `pricing.py` + `board.py` | model changes |
| Tiers | `tiers.py` | sorting/presentation |
| Presentation | `output.py`, `notifiers/` | thresholds |
| History | `output.py::export_history` | — |
| Grading | `grade.py` | auto-retraining |

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
