# Operations

## Weekly rhythm (in season) — forecast-first

| When (America/New_York) | What | Runs on |
|---|---|---|
| Thu 17:00 | Forecast Thursday games | `nfl-props-board` timer |
| Sun 11:00 | Forecast Sunday early slate | `nfl-props-board` timer |
| Sun 16:00 | Forecast remaining Sunday evening slate | `nfl-props-board` timer |
| Mon 18:00 | Forecast Monday game | `nfl-props-board` timer |
| Tue 09:00 | Grade forecasts, refresh data, rebuild v1+v2+forecast state | `nfl-props-grade` timer |

Each run publishes only the current ET day's not-yet-started games, in three
Discord sections (Moneylines / Spreads / Totals), with no truncation. On the VM
these call `scripts/run_nfl_linux_task.sh {board|grade}`; the board task runs
`run_forecast_board.py --rebuild-state --discord`, and the grade task chains
`grade_forecast.py` → `refresh-data` → `build` → `rebuild-state` →
`rebuild-state-v2` → `rebuild-forecast-state`. Manual equivalents on any host:
`run_forecast_board.py` / `grade_forecast.py`. Unpriced games still publish —
that is coverage, not failure.

The legacy price-screened board (`run_board.py`, `grade.py`) is retired
operationally and kept only for historical cohort grading.

## Linux production (Azure VM) — current

Deploy/verify steps: [`DEPLOY_LINUX.md`](DEPLOY_LINUX.md). Summary:

- Repo `~/nfl_props` on `azureuser@130.131.0.6`; venv; one-time data
  bootstrap (`refresh-data` → `build` → `rebuild-state[-v2]`).
- systemd: `scripts/systemd/nfl-props-{board,grade}.{service,timer}`,
  installed to `/etc/systemd/system`, `enable --now`, `Persistent=true`.
- Env/config in `~/.config/nfl_props/env`; logs in
  `logs/nfl_board.log` / `logs/nfl_grade.log` plus `journalctl`.
- Verify: `systemctl list-timers | grep nfl-props`, trigger a service once,
  check the log endings (`exit=0`, `grade_exit=0 refresh_exit=0`).

## Windows Task Scheduler setup (RETIRED)

Full SSH/clone/bootstrap walkthrough with ordered smoke tests:
[`DEPLOY_WINDOWS.md`](DEPLOY_WINDOWS.md) — retired 2026-09-07 when the box
was decommissioned; `nfl_props_daily` (daily 11:00) and `nfl_props_grade`
(Tuesday 09:00) ran green from 2026-08-14 until then and were disabled at
retirement. History/logs were migrated to the Mac before shutdown.

## Source safety + diagnostics

- The Bovada fetcher validates response shape before caching: an HTTP 200
  `{}` or other non-list body is treated as a failed fetch, never a silent
  empty board. Transient failures retry once, then fall back to the last-good
  cache; with no valid cache it raises rather than export nothing.
- Events whose kickoff is in the past (or missing) are filtered out of the
  board, so a stale cached board can't resurface an already-started game.
- Each run writes fetch metadata + team-total parse diagnostics to
  `outputs/diagnostics/bovada_coverage_*.json` (`coupon_fetch`,
  `event_fetches`, `stale_games_filtered`, `team_total_diagnostics`, ...).
  When team totals first post in game week, check `team_totals_found` and
  `team_total_diagnostics.unmatched_total_desc` to verify the parser against
  real market descriptions.
- Terminal output caps Watch rows at 30 for readability; every candidate is
  still exported to history. `run_board.py --all-watch` prints all Watch rows.
- Tests: `.venv\Scripts\python -m unittest discover -s tests` (Mac:
  `.venv/bin/python -m unittest discover -s tests`).

## Mac dev equivalents

```bash
.venv/bin/python run_forecast_board.py          # current-day forecast board
.venv/bin/python run_forecast_board.py --date 2026-09-13 --discord
.venv/bin/python grade_forecast.py              # accuracy + reference ROI
.venv/bin/python -m nfl_props.cli refresh-data && \
  .venv/bin/python -m nfl_props.cli build && \
  .venv/bin/python -m nfl_props.cli rebuild-forecast-state   # weekly rebuild
.venv/bin/python backtest_forecast.py           # leak-free tune/holdout
.venv/bin/python -m nfl_props.cli rebuild-state-v2  # v2 shadow state (research)
.venv/bin/python -m nfl_props.cli backfill-weather  # optional weather store
.venv/bin/python backtest_v2.py                 # v2 feature-set ablation
.venv/bin/python -m unittest discover -s tests  # unit tests
```

The forecast-first lane (`nfl_props/forecast.py`, `forecasting.py`,
`forecast_board.py`, `run_forecast_board.py`, `grade_forecast.py`,
`backtest_forecast.py`) is the production product; see
[`PRODUCT_CONTRACT.md`](PRODUCT_CONTRACT.md). The legacy v1 board
(`run_board.py`, Core/Lean/Watch) is retired operationally; v1/v2 state rebuilds
remain for historical grading. Weather columns enter the total head only when
`data/processed/game_weather.parquet` exists (`cli backfill-weather`); until
then they are neutral and dropped from the fit.

`grade.py` selects the latest pre-kickoff v1/v2 projection for every regular-
season game and reports points, margin, and total MAE side by side. Negative
`v2-v1` deltas mean v2 was better. Resolved shadow rows are exported separately
as `outputs/backtests/grade_*_v2_rows.json`; candidate ROI grading remains v1.

Note: `backtest.py`/`backtest_v2.py` may print spurious numpy `RuntimeWarning:
divide by zero / overflow / invalid value encountered in matmul` on numpy 2.x.
The numbers are correct (`np.dot` vs `@` is bit-identical for these inputs);
ignore the warnings.

## Failure triage

| Symptom | Check |
|---|---|
| Empty board | `outputs/diagnostics/bovada_coverage_*.json` — fetch failed vs no games posted |
| `state as of` stale during season | weekly rebuild task didn't run; check `logs\nfl_grade.log` |
| Grade "unmatched games" | team alias missing in `teams.py`, or kickoff/date drift beyond ±1 day |
| pbp 404 for current season | normal before the season's first game is published |
| Grade "pending" rows | normal until nflverse posts final scores (usually same night / next morning) |

## Rules of engagement

- Do not retune thresholds from fewer than ~50–100 graded Core/Lean plays.
- Oversized EV (> `NFL_EV_MAX`) stays in Watch. Do not promote by hand.
- Preseason (Aug 2026): pipeline dry-runs only, nothing graded as a pick.
- Record findings in `docs/PLAN.md` (findings log), not in chat memory.
