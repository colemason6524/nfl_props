# Operations

## Weekly rhythm (in season)

| When (America/Detroit) | What | Runs on |
|---|---|---|
| Daily 11:00 | Live board (lines move all week) | Azure VM systemd timer `nfl-props-board` |
| Tue 09:00 | Grade finished week, refresh data, rebuild v1+v2 state | Azure VM systemd timer `nfl-props-grade` |

On the VM these call `scripts/run_nfl_linux_task.sh {board|grade}`; the grade
task chains `grade.py` → `refresh-data` → `build` → `rebuild-state` →
`rebuild-state-v2`. Manual equivalents on any host: `run_board.py` /
`grade.py` + the cli chain. Team totals usually appear on Bovada during game
week; earlier runs log `team_totals_found=0` — that is coverage, not failure.

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
.venv/bin/python run_board.py                # board
.venv/bin/python grade.py                    # grade + games.csv refresh
.venv/bin/python -m nfl_props.cli refresh-data && \
  .venv/bin/python -m nfl_props.cli build && \
  .venv/bin/python -m nfl_props.cli rebuild-state   # weekly rebuild
.venv/bin/python backtest.py                 # full historical backtest
.venv/bin/python -m nfl_props.cli rebuild-state-v2  # v2 shadow state (research)
.venv/bin/python backtest_v2.py              # v2 feature-set ablation
.venv/bin/python -m unittest discover -s tests   # unit tests
```

The v2 shadow model (`ratings/v2.py`, `backtest_v2.py`,
`rebuild-state-v2`) is research-only: it never changes v1 probabilities or
tiers. Its ablation backtest is flat vs the market (see `docs/PLAN.md`), so it
is kept as a graded-shadow pipeline, not promoted. `run_board.py` attaches v2
shadow projections to history when `live_state_v2_shadow.json` exists.
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
