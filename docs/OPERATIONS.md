# Operations

## Weekly rhythm (in season)

| When | What | Command |
|---|---|---|
| Tue morning | Grade finished week, refresh data, rebuild state | `scripts\run_nfl_grade_task.cmd` (or `grade.py` + `cli refresh-data/build/rebuild-state`) |
| Tue–Sun (daily) | Live board (lines move all week) | `scripts\run_nfl_board_task.cmd` (or `run_board.py`) |

Team totals usually appear on Bovada during game week; earlier runs will log
`team_totals_found=0` — that is coverage, not failure.

## Windows Task Scheduler setup

Full SSH/clone/bootstrap walkthrough with ordered smoke tests:
[`DEPLOY_WINDOWS.md`](DEPLOY_WINDOWS.md). Summary:

1. Clone to `C:\Users\muski\nfl_props`; create `.venv`; `pip install -r requirements.txt`.
2. One-time data bootstrap: `python -m nfl_props.cli refresh-data`, `build`, `rebuild-state`.
3. Run the smoke tests (deps, build diagnostics, state, board, grade,
   wrapper-exact cmd.exe invocations) — all must pass first.
4. Board task (daily, e.g. 11:00): Program `C:\Windows\System32\cmd.exe`,
   arguments `/c ""C:\Users\muski\nfl_props\scripts\run_nfl_board_task.cmd""`,
   Start in `C:\Users\muski\nfl_props`.
5. Grade task (Tue 09:00): same pattern with `run_nfl_grade_task.cmd`.
6. Discord (when a channel exists): `setx NFL_DISCORD_WEBHOOK_URL "..."` and
   `setx SEND_DISCORD "true"` for the task account. Default is OFF.

Logs: `logs\nfl_board.log`, `logs\nfl_grade.log`.

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
