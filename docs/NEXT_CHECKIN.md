# NFL Props — Next Check-In

## 1. Status
Dry-run daily board green through Sep 4 (11:00 ET task: 16 games screened, Core=0, Lean 9, Watch 151, v2_shadow on 16 games); zero grades yet — first grade task fires Tue Sep 8; Week 1 kicks off ~Sep 9–10.

## 2. Next action — WHEN: Tue Sep 8 09:00 ET (first nfl_props_grade run), then Week-1 kickoff Sep 9
- Verify Tue Sep 8 09:00 grade task produces the first `outputs/backtests/grade_*.txt` cleanly.
- Week 1 2026 = live go-live per docs/PLAN.md: flat 1u, no per-play sizing changes.
- After Week 1 resolves, compare graded rows vs v2_shadow rows before any further thought.

## 3. Check on pop-back
- Daily `nfl_props` 11:00 ET task "Ready" and latest `outputs/history/nfl_board_*.json` is same-day (latest today: 20260904T150038Z).
- Board summary: Core=0 is expected pre-week-1; confirm Lean/Watch counts stay sane (today 9/151).
- No `live_state.json` in outputs — board state lives in `outputs/history/` snapshots + `data/processed/live_state*.json`.
- v2 shadow columns (`mu_*_v2`) present in projections — shadow is collecting, not driving.
- First grade run may be sparse (nothing graded until games resolve) — that is success, not failure.

## 4. Do NOT
- No threshold/tier-policy flips in this pass; go-live is flat 1u only.
- No Discord changes or polish.
- No retune before Week-1 grades accumulate (≥50–100 resolved plays before any churn discussion).
- No `git add .`; no provider/model swaps (stay on current model, no ChatGPT/deepseek API cash providers).

## 5. Leave-off pointer
- HEAD `5105040` (Drop Bovada preMatchOnly so morning coupons are not empty {}); Windows clone in sync.
- Files to start from: `docs/PLAN.md` (go-live gate: backtest vs closing + preseason dry-run), `docs/HANDOFF.md`, `grade.py`, `run_board.py`.
- Windows: `C:\Users\muski\nfl_props` (tasks: nfl_props_daily 11:00, nfl_props_grade Tue 09:00).
