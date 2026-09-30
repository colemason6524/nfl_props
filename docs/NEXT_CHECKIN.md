# NFL Props — Next Check-In

## Current check-in — 2026-09-30

- Week 4 grade-diagnostics commit `385f82d` has been pulled to the existing
  Azure VM. Verified `git rev-parse HEAD` is `385f82d`; remote working tree is
  clean, `nfl-forecast-grading-v2` imports, and all 70 unit tests pass remotely.
- No board/grade timer was manually triggered, no grade was forced, and no
  Discord post was sent for this grading-only update. No forecast-state rebuild
  was needed.
- At the next scheduled weekly grade, verify the weekly JSON has
  `grading_version: nfl-forecast-grading-v2` and the `market_reversals`,
  `confidence_groups`, residual, `reference_forecast_error`, and `edge_bands`
  fields; check `logs/nfl_weekly_grade.log` for successful completion and
  zero unmatched games. The new artifact check is pending until that grade.
- Continue the pre-registered Week 4–8 observation plan: preserve forecast
  coverage and latest-pre-kickoff grading; no model, value-policy, threshold,
  or production recommendation changes before the Week 8 evidence review.
- Detailed rollout and schema semantics: `docs/WEEK4_GRADE_DIAGNOSTICS.md`.

## 0. Historical Tue Sep 8 audit fix (deployed to the Azure VM)
- **Season carryover in live state** (`ratings/epa.py`, `ratings/v2.py`):
  `rebuild-state` exported raw replay state, so all 32 teams (no 2026 game
  yet) carried full 2025 strength into live boards while the backtest
  regressed them by SEASON_CARRYOVER=0.60 at the season boundary. Export
  now goes through `export_teams()`/`carryover_view()` — a regressed VIEW
  (no state mutation), applied exactly once because every rebuild
  re-replays from scratch and a team that has played in 2026 is already
  regressed inside `replay`. New `carryover_applied` flag per team and
  `season_carryover` in live_state.json. Week-1 boards are now priced off
  the same ratings the backtest used for week-1 games.
- Implied team-total sign audited: nflverse `spread_line` is the home
  margin expectation (positive = home favored), so the existing
  (total + spread)/2 home / (total − spread)/2 away is correct — no change.
- Tests 25 → 29 on the Mac (`tests/test_ratings_export.py`).

## 1. Status
Production migrated 2026-09-07/08 and **verified live**: Windows retired
(tasks disabled), Azure VM running user-level fleet timers (`sports-nfl-board`
daily 10:56 ET, `sports-nfl-grade` Tue 08:47 ET, `Persistent=false`).
System-level `nfl-props-*` timers disabled 2026-09-09. The interim tmux
scheduler experiment was removed. Single-side policy live (tier v2): one
Core/Lean side per (game, market), `OPPOSITE_SIDE` → Watch. Team totals
verified: 32/32 parsed, unmatched descriptions are player props. Model v1.1
(carryover export included).

## 2. Next action — WHEN: Tue Sep 8 09:00 ET (first `sports-nfl-grade` fleet run)
- Verify the grade service runs clean: `logs/nfl_grade.log` ends
  `grade_exit=0 refresh_exit=0`, and the first
  `outputs/forecast_backtests/grade_forecast_*.txt` appears (sparse/empty
  grades = success, not failure).
- Confirm the board fleet fired 10:56 ET: same-day
  `outputs/forecast_history/nfl_forecast_*.json`, `exit=0` in
  `logs/nfl_forecast.log`.
- Week 1 2026 = live go-live per docs/PLAN.md: flat 1u, no per-play sizing
  changes.
- After Week 1 resolves, compare forecast accuracy and reference ROI.

## 3. Check on pop-back
- Fleet timers: check `sports-nfl-board` and `sports-nfl-grade` in the
  user's scheduler. System-level `nfl-props-*` timers are disabled.
- Latest `outputs/forecast_history/nfl_forecast_*.json` is same-day (VM),
  and the Mac's canonical copy stays within a few days if you want the bulk
  mirror.
- Board summary: confirm Lean/Watch counts stay sane; team totals are posted
  and parsed (`team_totals_found` ≈ 32).
- `logs/nfl_forecast.log` / `logs/nfl_grade.log` on the VM end with
  `exit=0` / `grade_exit=0 refresh_exit=0`.

## 4. Do NOT
- No EV-window/threshold churn; go-live stays honest flat 1u on the locked
  plan (single-side policy v2 is the only gate change, adopted pre-data).
- No retune before Week-1 grades accumulate (≥50–100 resolved plays before
  any churn discussion).
- No `git add .`; no provider/model swaps (stay on current model, no
  ChatGPT/deepseek API cash providers).

## 5. Leave-off pointer
- Production: Azure VM per `docs/DEPLOY_LINUX.md` (repo `~/nfl_props`,
  fleet timers live). Windows: retired, tasks disabled, files on hard drive +
  Mac.
- Files to start from: `docs/PLAN.md` (go-live gate), `docs/HANDOFF.md`,
  `grade_forecast.py`, `run_forecast_board.py`, `docs/OPERATIONS.md`.
- Retired in this pass: tmux scheduler experiment
  (`run_nfl_tmux_task.sh`, `start_nfl_tmux_scheduler.sh`,
  `nfl-props-tmux.service`).
