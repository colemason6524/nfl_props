# NFL Props — Next Check-In

## 1. Status
Production migrated 2026-09-07: Windows retired, Azure VM live with systemd
timers (`nfl-props-board` daily 11:00, `nfl-props-grade` Tue 09:00, both
America/Detroit). Board snapshots green through Sep 7 (34 snapshots on the
Mac, including 29 migrated from Windows). Zero grades yet — first grade task
fires Tue Sep 8 on the VM; Week 1 kicks off ~Sep 9–10.

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
  sane; `tt=0` before team totals post is coverage, not failure.
- `logs/nfl_board.log` / `logs/nfl_grade.log` on the VM end with `exit=0` /
  `grade_exit=0 refresh_exit=0`.
- v2 shadow columns (`mu_*_v2`) present in history projections — shadow is
  collecting, not driving.

## 4. Do NOT
- No threshold/tier-policy flips in this pass; go-live is flat 1u only.
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
