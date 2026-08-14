# nfl_props

NFL **team-market** research engine: team total points O/U, game spread, and
moneyline.

Builds opponent-adjusted EPA team ratings from free nflverse data, projects
team points, compares fair probabilities to Bovada prices, and surfaces
positive-EV plays as Core / Lean / Watch. Flat 1-unit stakes. No paid API keys.

Lessons carried from `tennis_props`, `wnba_props`, `mlb_props`, and
`golf_props` (see `tennis_props/docs/PLAYBOOK.md`): scrape-first free data,
point-in-time features, export-everything history, grade snapshots instead of
auto-retraining, and keep the performance model separate from the odds/value
layer.

## Status (2026-08-13)

| Phase | Status |
|---|---|
| 0 Locked decisions | **done** |
| 1 Data foundation (nflverse ingest, canonical store) | **done** |
| 2 Baseline model + backtest vs closing | **done** |
| 3 Live Bovada board + tiers + history export | **done** |
| 4 Grading (`grade.py`) | **done** |
| 5 Windows Task Scheduler ops | **scripts ready; schedule at Week 1** |
| Preseason dry-run window (Aug 2026) | **in progress — ungraded pipeline checks only** |
| Discord delivery | **stubbed, default off** |
| FanDuel scraper | **deferred** |

Roadmap and findings log: [`docs/PLAN.md`](docs/PLAN.md).
Day-to-day ops: [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
Module map: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
**Handoff / current state (read first in a new chat):** [`docs/HANDOFF.md`](docs/HANDOFF.md).

## Design decisions (locked)

| Decision | Choice |
|---|---|
| Markets v1 | Team total points O/U + game spread + moneyline |
| Games | Regular season 2026+; preseason = ungraded pipeline dry-run only |
| Live odds | Bovada free JSON coupon API (primary). FanDuel is the book actually bet; FD scrape deferred |
| Historical stats | nflverse play-by-play parquet releases (EPA per play) |
| Historical odds | nflverse/nfldata `games.csv` closing spread/total/moneylines (Lee Sharpe) |
| Historical team-total lines | **Not published free** — derived implied team totals `(total ∓ spread)/2`; see honesty note |
| Staking | Flat 1 unit, no Kelly |
| Scheduling | Windows Task Scheduler production; macOS for dev/review |
| Pushes / cancelled games | Pushes are a wash; grade only completed games |

## Pipeline (as built)

```
refresh-data  ->  data/raw/play_by_play_*.parquet + games.csv
build         ->  data/processed/team_games.parquet + games.parquet
rebuild-state ->  data/processed/live_state.json  (ratings + coefficients + residuals)
run_board     ->  Bovada slate/odds -> fair p -> EV -> Core/Lean/Watch
              ->  terminal board + outputs/history/nfl_board_*.json
              ->  optional Discord Core digest (default off)
grade         ->  latest pre-kickoff Core/Lean vs final scores -> outputs/backtests/
```

## Quick start

```bash
cd /path/to/nfl_props
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Weekly (Tuesday, after MNF): refresh raw data and rebuild
.venv/bin/python -m nfl_props.cli refresh-data
.venv/bin/python -m nfl_props.cli build
.venv/bin/python -m nfl_props.cli rebuild-state

# Historical backtest (tune 2015-2022, holdout 2023-2025)
.venv/bin/python backtest.py

# Live board (in season: run daily Tue-Sun; lines move all week)
.venv/bin/python run_board.py

# After games finish (and refresh-data): grade Core/Lean
.venv/bin/python grade.py
```

### Windows production

1. Clone the repo to the desktop (e.g. `C:\Users\muski\nfl_props`).
2. Create `.venv`, install `requirements.txt`.
3. Optional: set user env `NFL_DISCORD_WEBHOOK_URL` and `SEND_DISCORD=true`.
4. Task Scheduler → daily → `scripts\run_nfl_board_task.cmd`.
5. Tuesday morning task → `scripts\run_nfl_grade_task.cmd` (grades + refreshes data + rebuilds state).

Logs: `logs\nfl_board.log`, `logs\nfl_grade.log`. See `docs/OPERATIONS.md`.

## Live tiers (current defaults)

Fair probability = EPA points projection + empirical score distributions
(**market not inside fair**). EV = flat 1u at the offered Bovada price.
Edge = `p_model − p_market` (de-vigged book).

| Tier | Rule |
|---|---|
| **Core** | EV in [2%, 8%], edge ≥ 2%, both teams ≥ 3 current-season games |
| **Lean** | EV in [2%, 8%], edge ≥ 2% (early-season / lower-confidence) |
| **Watch** | Everything else with interest; EV > 8% stays Watch (stale/outlier filter) |

Env overrides: `NFL_EV_MIN`, `NFL_EV_MAX`, `NFL_EDGE_MIN`, `NFL_CORE_EV_MIN`,
`NFL_MIN_TEAM_GAMES`.

Discord posts **Core only** and stays off unless `SEND_DISCORD=true` and
`NFL_DISCORD_WEBHOOK_URL` are set (sport-specific webhook, never shared).

## Important honesty notes

- **NFL closing spreads/totals are the most efficient lines in sports.**
  Expect parity at best against closing in backtests. The live thesis is
  model-vs-Bovada disagreement in a capped EV window (team totals are softer
  than spreads), graded prospectively — treat early live ROI as research.
- **Historical team-total lines are derived, not real.** Free archives carry
  closing spread + total only, so the backtest prices team totals at the
  implied line `(total ∓ spread)/2` with assumed -110 juice. That validates
  the points model and calibration, not realized team-total ROI.
- Backtest results and EV-band findings are logged in `docs/PLAN.md` as they
  accumulate. Do not retune from fewer than ~50-100 graded Core/Lean plays.
