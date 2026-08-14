# Module map

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
outputs/history/         nfl_board_*.json (every run, full snapshot)
outputs/backtests/       backtest_*.txt, backtest_v2_*.txt, grade_*.txt
outputs/diagnostics/     build + bovada coverage diagnostics
scripts/                 Windows Task Scheduler wrappers
tests/                   unittest: bovada cache/parse/filter + v2 replay/leakage
```

## Layer separation (do not mix)

| Layer | Source of truth | Never contains |
|---|---|---|
| Performance model (v1) | `ratings/epa.py` | odds, Discord, display labels |
| Performance model (v2 shadow) | `ratings/v2.py` | tier gates, v1 state |
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
is for prospective grading. `grade.py` compares v1/v2 points, margin, and total
MAE on every resolved regular-season game; it does not assign v2 bets or ROI.
Its base off/def/pace ratings are numerically
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
