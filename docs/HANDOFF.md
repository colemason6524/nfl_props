# Handoff — read this first in a new chat

## What this repo is

NFL team-market research engine (team totals + spread + moneyline), built
2026-08-13 as a clone of the `tennis_props` architecture using the
cross-sport playbook (`tennis_props/docs/PLAYBOOK.md`). Free data only:
nflverse historical parquet/CSV + Bovada live JSON. FanDuel is the bet-at
book; its scraper is deferred.

## Current state (2026-08-13)

- Git: pushed to https://github.com/colemason6524/nfl_props (`main`;
  initial build `b0999ce`, deployment doc `9f8bea6`). HTTPS remote, same
  convention as tennis_props.
- Full pipeline works end to end on this Mac: `refresh-data` → `build`
  (pbp match rate 1.0000 for 2013–2025) → `rebuild-state` → `run_board.py`
  (parsed all 16 Week 1 2026 games from Bovada) → `grade.py` (verified on a
  synthetic snapshot of a completed 2025 game; live Week 1 rows correctly
  pending).
- 2026 pbp is not published yet (404 → skipped automatically); ratings state
  is as of the 2025 season finale (2026-02-08).
- Bovada team totals are not posted yet for Week 1; the board logs
  `team_totals_found=0` and continues. Verify the team-total parser against
  real markets during game week — the market-description patterns in
  `sources/bovada.py::_TEAM_TOTAL_PATTERNS` were written from known naming
  variants, not observed payloads. **This is the top open risk.**
- Windows prod is deployed at `C:\Users\muski\nfl_props` (SSH host
  `windows`). Smoke tests 4.1–4.6 passed 2026-08-14; Task Scheduler jobs
  `nfl_props_daily` (daily 11:00) and `nfl_props_grade` (Tuesday 09:00) are
  enabled under passwordless S4U `colemason41` and manually returned 0.
- Discord is stubbed and OFF (`NFL_DISCORD_WEBHOOK_URL` + `NFL_SEND_DISCORD`).
- Local-only artifacts (gitignored, rebuilt from `refresh-data`): `data/`,
  `outputs/`, `.cache/`, `.venv/`.
- For a brand-new conversation, paste `docs/AGENT_INTRO_PROMPT.md`.

## Backtest verdict (2026-08-13, tune 2015–2022, holdout 2023–2025)

- The model does NOT beat closing lines. Calibrated log-loss reaches market
  parity (spread 0.6939 vs 0.6934; game total 0.6927 vs 0.6933 — slightly
  better; ML 0.6381 vs 0.6077 — worse).
- Per-market calibration shrink says spread/total raw probabilities carry
  ~no standalone signal vs closing (gamma → 0.1 floor); ML is slightly
  underconfident (gamma ≈ 1.15).
- EV bands at raw probabilities: the production window [2%, 8%) was mildly
  positive only for team totals (+3.2% in [2,5)) and game totals (+4.3%);
  spread/ML negative. EV ≥ 15% bands are toxic (ML 99-264) — hence the hard
  EV cap to Watch.
- The live thesis is therefore: model-vs-Bovada disagreement in a capped EV
  window, focused on totals-family markets, graded prospectively. Treat live
  ROI as research until `grade.py` builds a 50–100 play sample.

## Design invariants (do not silently change)

- Market never inside the live fair probability.
- `p_model` (raw) drives tiers; `p_model_cal` is exported research context.
- Tiers: Core needs EV in [2%, 8%], edge ≥ 2%, both teams ≥ 3 current-season
  games; otherwise Lean at best; oversized EV always Watch.
- Every run exports full history JSON; grading uses the latest pre-kickoff
  snapshot per (matchup, market, side).
- Versions in `nfl_props/version.py`: schema 3, `nfl-epa-points-v1`,
  `core-lean-watch-v1`. Bump the model version only when probabilities
  change.

## Next steps (in order)

1. Preseason dry-runs: run `run_board.py` a few times a week; check Bovada
   coverage diagnostics; confirm team-total parsing once markets appear.
2. Week 1 (Sept 2026): schedule both Windows tasks; grading live from the
   first week; no retuning.
3. After ~50–100 graded Core/Lean plays: review EV bands and market mix in
   `docs/PLAN.md` findings; decide Discord promotion.
4. Deferred: FanDuel scrape, alt team totals, QB-out/injury flags (shadow
   fields first), playoff handling.
