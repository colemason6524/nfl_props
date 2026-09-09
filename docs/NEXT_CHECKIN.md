# NFL Props — Next Check-In

## 0. Tue Sep 8 audit fix (deployed to the Azure VM)
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
(tasks disabled), Azure VM running systemd timers (`nfl-props-board` daily
11:00, `nfl-props-grade` Tue 09:00, both America/Detroit). Both services
were triggered manually under systemd and returned exit 0; grading over the
migrated 35-snapshot history reports `graded 0 | pending 18 | unmatched 0`.
The interim tmux scheduler experiment (bash while-loops in tmux) was
removed. Tue Sep 8 grade fired on schedule; Week 1 kicks off ~Sep 9–10.
Single-side policy live (tier v2): one Core/Lean side per (game, market),
`OPPOSITE_SIDE` → Watch. Team totals verified: 32/32 parsed, unmatched
descriptions are player props. Model v1.1 (carryover export included).

## 2. Next action — WHEN: Tue Sep 8 09:00 ET (first `nfl-props-grade` timer run)
- Verify the grade service runs clean under systemd: `journalctl -u
  nfl-props-grade.service`, `logs/nfl_grade.log` ends
  `grade_exit=0 refresh_exit=0`, and the first `outputs/backtests/grade_*.txt`
  appears (sparse/empty grades = success, not failure).
- Confirm the board timer fired 11:00 ET: same-day
  `outputs/history/nfl_board_*.json`, `exit=0` in `logs/nfl_board.log`.
- Week 1 2026 = live go-live per docs/PLAN.md: flat 1u, no per-play sizing
  changes.
- After Week 1 resolves, compare graded rows vs v2_shadow rows before any
  further thought.

## 3. Check on pop-back
- `systemctl list-timers --no-pager | grep nfl-props` — both pending.
- Latest `outputs/history/nfl_board_*.json` is same-day (VM), and the Mac's
  canonical copy stays within a few days if you want the bulk mirror.
- Board summary: Core=0 is expected pre-week-1; confirm Lean/Watch counts stay
  sane; team totals are posted and parsed (`team_totals_found` ≈ 32).
- `logs/nfl_board.log` / `logs/nfl_grade.log` on the VM end with `exit=0` /
  `grade_exit=0 refresh_exit=0`.
- v2 shadow columns (`mu_*_v2`) present in history projections — shadow is
  collecting, not driving.

## 4. Do NOT
- No EV-window/threshold churn; go-live stays honest flat 1u on the locked
  plan (single-side policy v2 is the only gate change, adopted pre-data).
- No retune before Week-1 grades accumulate (≥50–100 resolved plays before
  any churn discussion).
- No `git add .`; no provider/model swaps (stay on current model, no
  ChatGPT/deepseek API cash providers).

## 5. Leave-off pointer
- Production: Azure VM per `docs/DEPLOY_LINUX.md` (repo `~/nfl_props`,
  timers live). Windows: retired, tasks disabled, files on hard drive +
  Mac.
- Files to start from: `docs/PLAN.md` (go-live gate), `docs/HANDOFF.md`,
  `grade.py`, `run_board.py`, `journalctl -u nfl-props-board`.
- Retired in this pass: tmux scheduler experiment
  (`run_nfl_tmux_task.sh`, `start_nfl_tmux_scheduler.sh`,
  `nfl-props-tmux.service`).
