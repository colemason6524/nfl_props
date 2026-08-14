# Agent intro prompt (copy into the new chat)

Paste everything below the line into the new agent conversation as the first
user message (or attach this file + `docs/HANDOFF.md`).

---

You are continuing work on **nfl_props**, an NFL team-market (team totals,
spread, moneyline) prediction and betting-edge project. Do **not** restart
greenfield discovery. Read these docs in order before changing anything:

1. `docs/HANDOFF.md` — current state, backtest verdict, watch-outs
2. `../tennis_props/docs/PLAYBOOK.md` — cross-sport process this repo follows
3. `docs/PLAN.md` — locked decisions, model, findings log
4. `docs/OPERATIONS.md` + `docs/DEPLOY_WINDOWS.md` — how to run / deploy
5. `docs/ARCHITECTURE.md` — code map and layer-separation rules

## Context in one paragraph

Repo: https://github.com/colemason6524/nfl_props
Mac dev path: `/Users/colemason/nfl_props` (venv at `.venv`).
Windows prod target: `C:\Users\muski\nfl_props` (SSH host `windows` =
`colemason41@100.77.131.65`) — **not yet deployed**; follow
`docs/DEPLOY_WINDOWS.md` when it's time. Built 2026-08-13 as a clone of the
tennis_props architecture: nflverse free data (pbp EPA + games.csv closing
lines), point-in-time opponent-adjusted EPA ratings → OLS points projection →
empirical score distributions, backtest vs closing (tune 2015–2022, holdout
2023–2025), live Bovada JSON board (FanDuel is the bet-at book, FD scrape
deferred), Core/Lean/Watch tiers, export-everything history, next-day
grading, Windows wrappers, Discord stubbed OFF behind
`NFL_DISCORD_WEBHOOK_URL`. Flat 1u stakes. Regular season only; preseason
runs are ungraded pipeline dry-runs.

## Backtest verdict (do not re-litigate without new evidence)

The model does **not** beat closing lines (expected). Calibrated log-loss is
at market parity for spread/game total, worse for ML. Per-market calibration
shrink (`prob_shrink` in `live_state.json`) collapsed to the 0.1 floor for
spread/total/team_total — i.e. no standalone signal at the closing line —
and ≈1.15 for ML. EV bands at raw probabilities: only team totals (+3.2%)
and game totals (+4.3%) were positive in the production [2%,5%) band;
EV ≥ 15% is toxic (ML 99-264). The live thesis is model-vs-Bovada
disagreement in a capped EV window, totals-family focus, graded
prospectively.

## Live tier policy (production semantics)

Fair = raw model probability (`p_model`; market never inside fair).
`p_model_cal` (shrunk) is exported research context only. Core: EV ∈
[2%,8%], edge ≥ 2% vs de-vigged Bovada, both teams ≥ 3 current-season games.
Lean: same window, low sample. Watch: everything else including oversized EV
(stale-line filter). Env overrides: `NFL_EV_MIN/MAX`, `NFL_EDGE_MIN`,
`NFL_CORE_EV_MIN`, `NFL_MIN_TEAM_GAMES`.

## Current evidence state

Zero graded plays — the 2026 season starts ~Sept 10. All 16 Week 1 games
already parse from Bovada with spread/ML/game total. Do not retune anything
until ≥50–100 resolved Core/Lean grades.

## Known open risks

- **Team-total parser unverified against real payloads**: Bovada posts team
  totals only in game week; `_TEAM_TOTAL_PATTERNS` in
  `nfl_props/sources/bovada.py` was written from naming conventions. Verify
  the first game week and fix patterns if `tt=0` while the site shows them.
- 2026 pbp file 404s until the season starts (handled; skipped).
- Bovada price ≠ FanDuel price (bet-at book) — don't claim realized ROI
  across books.
- Historical team-total lines are DERIVED `(total ∓ spread)/2`; backtest
  team-total ROI is not real-market ROI.
- No injury/QB-out awareness: that is exactly why big EV is quarantined.

## What "done" looks like for the next stretch

1. Preseason: a few clean dry-run boards; team-total parsing confirmed.
2. Before Week 1: Windows deployed per `docs/DEPLOY_WINDOWS.md` (smoke tests
   4.1–4.6 pass), both Task Scheduler jobs live (daily board, Tuesday
   grade+rebuild).
3. Weeks 1–8: grading loop green, findings appended to `docs/PLAN.md`,
   no threshold changes.
4. At 50–100 grades: EV-band/market-mix review; only then consider Discord
   promotion, FanDuel scrape, alt lines, injury shadow flags.

User communication style: direct, concise, no unnecessary questions;
implement when direction is clear. Free data only; never add paid APIs
without being asked. Update docs when process changes; log findings in
`docs/PLAN.md`, version bumps per `nfl_props/version.py` rules.

Start by reading `docs/HANDOFF.md`, then run
`.venv/bin/python run_board.py` and check the coverage diagnostics.
