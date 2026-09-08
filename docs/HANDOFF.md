# Handoff — read this first in a new chat

## What this repo is

NFL team-market research engine (team totals + spread + moneyline), built
2026-08-13 as a clone of the `tennis_props` architecture using the
cross-sport playbook (`tennis_props/docs/PLAYBOOK.md`). Free data only:
nflverse historical parquet/CSV + Bovada live JSON. FanDuel is the bet-at
book; its scraper is deferred.

## Current state (2026-09-07)

- Git: pushed to https://github.com/colemason6524/nfl_props (`main`).
- Full pipeline works end to end on this Mac: `refresh-data` → `build`
  (pbp match rate 1.0000 for 2013–2025) → `rebuild-state[-v2]` →
  `run_board.py` → `grade.py`.
- 2026 pbp 404s until the season's first games are published; ratings state
  is as of the 2025 season finale (2026-02-08) until the first weekly
  rebuild.
- Verify the team-total parser against real markets during game week — the
  market-description patterns in `sources/bovada.py::_TEAM_TOTAL_PATTERNS`
  were written from known naming variants, not observed payloads, and
  `team_total_diagnostics` in the coverage JSON is the fixture for that.
  **This is the top open risk.**
- **Windows prod RETIRED 2026-09-07** (machine being disposed of; both Task
  Scheduler jobs disabled after running green since 2026-08-14; `outputs/`
  history + logs migrated to the Mac).
- **Linux prod live: Azure VM** (`ssh -i ~/Downloads/RunThemScripts_key.pem
  azureuser@130.131.0.6`), repo `~/nfl_props`, systemd timers
  `nfl-props-board` (daily 11:00) + `nfl-props-grade` (Tue 09:00), both
  America/Detroit via `OnCalendar` TZ suffix. Deploy/verify:
  `docs/DEPLOY_LINUX.md`. Mac keeps the bulk of files (canonical history
  copy); an interim tmux scheduler experiment was replaced by systemd.
- Discord: env file `~/.config/nfl_props/env` on the VM carries
  `NFL_SEND_DISCORD` + `NFL_DISCORD_WEBHOOK_URL` (Core-only digest; empty
  Core sends nothing).
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
