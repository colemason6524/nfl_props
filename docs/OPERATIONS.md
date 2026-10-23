# Operations

## Weekly rhythm (in season) — forecast-first

| When (America/New_York) | What | Timer |
|---|---|---|
| Thu 19:15 | Forecast Thursday game | `sports-nfl-board.timer` |
| Thu 19:45 | Injury delta check — **silent, diary only** (no Discord) | `sports-nfl-delta-check.timer` |
| Sun 09:30 | Late pregame injury pull + board regen (Sunday early slate) | `sports-nfl-pregame-refresh.timer` → `sports-nfl-pregame-refresh.service` |
| Sun 11:00 | Forecast Sunday early slate | `sports-nfl-board.timer` |
| Sun 14:47 | Wave board: full refresh for the remaining slate | `sports-nfl-wave2.timer` |
| Sun 15:37 | Forecast remaining Sunday evening slate | `sports-nfl-board.timer` |
| Sun 15:47 | Injury delta check — **silent, diary only** (no Discord) | `sports-nfl-delta-check.timer` |
| Mon 18:00 | Forecast Monday game | `sports-nfl-board.timer` |
| Mon 19:15 | Late pregame injury pull + board regen (`Persistent=true`) | `sports-nfl-mnf-refresh.timer` → `sports-nfl-pregame-refresh.service` |
| Mon 23:45 | Injury delta check — **silent, diary only** (no Discord) | `sports-nfl-delta-check.timer` |
| Tue 03:30 | Public weekly recap (refresh finals → `grade_forecast.py --weekly --discord`) | `sports-nfl-weekly-grade.timer` |
| Tue 08:47 | Grade forecasts, refresh data, rebuild v1+v2+forecast state | `sports-nfl-grade.timer` |

Each run publishes only the current ET day's not-yet-started games, in three
Discord sections (Moneylines / Spreads / Totals), with no truncation. On the VM
these call `scripts/run_nfl_linux_task.sh {board|grade}`; the board task runs
`run_forecast_board.py --rebuild-state --discord`, and the grade task chains
`grade_forecast.py` → `refresh-data` → `build` → `rebuild-state` →
`rebuild-state-v2` → `rebuild-forecast-state`. Manual equivalents on any host:
`run_forecast_board.py` / `grade_forecast.py`. Unpriced games still publish —
that is coverage, not failure.

The legacy price-screened board (`run_board.py`, `grade.py`) is retired
operationally and kept only for historical cohort grading. Weekly forecast
JSON grading diagnostics (grading version `nfl-forecast-grading-v2`) are
specified in [`WEEK4_GRADE_DIAGNOSTICS.md`](WEEK4_GRADE_DIAGNOSTICS.md).

Performance-record scope (since 2026-10-22): the advertised record — weekly
recap **and** the season report — counts `PLAYABLE` references only. `NO_VALUE`
references are graded and reported on their own line; the all-references
figure survives only as a `pooled` diagnostic. Grading prices every reference
at the latest pre-kickoff snapshot when one exists, falling back to the board
artifact.

Delta checks (installed 2026-10-23): `sports-nfl-delta-check.timer` runs
`scripts/run_nfl_linux_task.sh delta_check` at Thu 19:45, Sun 15:47 and Mon
23:45 ET — ten minutes after each board — against a 30m timeout. It is
**silent/diary-only by design** (Cole ruling 2026-10-22): the forecast board is
the only Discord surface, so the wrapper deliberately calls
`run_delta_check()` without `--discord` and posts no webhook. A `source: delta`
snapshot is written only when a check would have fired. Log:
`logs/nfl_delta_check.log`.

Sunday pregame refresh (enabled 2026-10-23): `sports-nfl-pregame-refresh.timer`
fires the same `sports-nfl-pregame-refresh.service` at Sun 09:30 ET, alongside
the Monday 19:15 `sports-nfl-mnf-refresh.timer`. A real run passes `--discord`
and therefore **does** publish a board. The timer is deliberately
`Persistent=false`: with `Persistent=true`, enabling a timer whose last elapse
was missed fires that run immediately and posts a spurious board, which is
exactly the failure this timer was fixed for. History: it fired once on
2026-10-11 (Sun 09:30 ET), was stopped on 2026-10-14 and therefore did not run
on 2026-10-18; the 2026-10-22 audit note that it "has never fired" is
corrected here — `LastTriggerUSec` reads empty only because disabling the
timer resets it. See [`INTEGRITY_AUDIT_2026-10-22.md`](INTEGRITY_AUDIT_2026-10-22.md)
for the original finding.

## Linux production (Azure VM) — current

Deploy/verify steps: [`DEPLOY_LINUX.md`](DEPLOY_LINUX.md). Summary:

- Repo `~/nfl_props` on `azureuser@130.131.0.6`; venv; one-time data
  bootstrap (`refresh-data` → `build` → `rebuild-state[-v2]`).
- User-level `sports-*` fleet timers (production since 2026-09-09), all
  `Persistent=false` except `sports-nfl-mnf-refresh` (true): board (Thu 19:15,
  Sun 11:00, Sun 15:37, Mon 18:00 ET), wave2 (Sun 14:47 Detroit), pregame
  refresh (Sun 09:30 ET via `sports-nfl-pregame-refresh.timer`, Mon 19:15 ET
  via mnf-refresh), delta check (Thu 19:45, Sun 15:47,
  Mon 23:45 ET — silent/diary only), weekly recap (Tue 03:30 ET), grade
  (Tue 08:47 Detroit). Repo copies of the two pregame-refresh units are
  `scripts/systemd/sports-nfl-pregame-refresh.{timer,service}`, byte-identical
  to the installed user units. Disabled system-level `nfl-props-*` timers
  remain in `scripts/systemd/` as a documented fallback.
- Env/config in `~/.config/nfl_props/env`; logs in
  `logs/nfl_forecast.log` / `logs/nfl_grade.log` plus `journalctl`.
- Verify: `systemctl list-timers --all | grep sports-nfl`, trigger a timer
  once, check the log endings (`exit=0`, `grade_exit=0 refresh_exit=0`).

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
  still exported to history. `run_forecast_board.py --all-watch` prints all Watch rows.
- Tests: `.venv/bin/python -m pytest -q` (the suite is unittest-style and also
  runs under `python -m unittest discover -s tests`).

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
| `state as of` stale during season | weekly rebuild task didn't run; check `logs/nfl_grade.log` |
| Grade "unmatched games" | team alias missing in `teams.py`, or kickoff/date drift beyond ±1 day |
| pbp 404 for current season | normal before the season's first game is published |
| Grade "pending" rows | normal until nflverse posts final scores (usually same night / next morning) |
| Season/weekly record looks wrong | `grade_forecast.py` (season) or `grade_forecast.py --weekly --week N --no-discord --no-export` (recompute from source, writes nothing) |

## Rules of engagement

- Do not retune thresholds from fewer than ~50–100 graded plays.
- Oversized EV (> `NFL_EV_MAX`) stays in Watch. Do not promote by hand.
- Preseason (Aug 2026): pipeline dry-runs only, nothing graded as a pick.
- Record findings in `docs/PLAN.md` (findings log), not in chat memory.
