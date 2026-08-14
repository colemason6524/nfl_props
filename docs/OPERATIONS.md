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

## Mac dev equivalents

```bash
.venv/bin/python run_board.py                # board
.venv/bin/python grade.py                    # grade + games.csv refresh
.venv/bin/python -m nfl_props.cli refresh-data && \
  .venv/bin/python -m nfl_props.cli build && \
  .venv/bin/python -m nfl_props.cli rebuild-state   # weekly rebuild
.venv/bin/python backtest.py                 # full historical backtest
```

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
