# nfl_props — plan, locked decisions, findings log

## Locked product decisions (Phase 0 — do not re-ask)

| Question | Answer |
|---|---|
| Competitions | NFL regular season (+ playoffs later); preseason = ungraded dry-run only |
| Markets v1 | Team total points O/U, game spread, game moneyline |
| Book read vs bet | Bovada free JSON is read/modeled; FanDuel is the bet-at book (scrape deferred) |
| Production | Windows Task Scheduler via git pull; Mac dev |
| Go-live gate | Backtest vs closing + preseason pipeline dry-run; Week 1 2026 live |
| Stakes | Flat 1 unit |
| Voids / cancelled | Pushes wash; grade completed games only |
| Discord | Core-only digest, `NFL_DISCORD_WEBHOOK_URL` + `NFL_SEND_DISCORD`, default off |

## Architecture

```
FREE DATA -> CANONICAL STORE -> POINT-IN-TIME RATINGS -> ODDS COMPARE
   -> TIERS (Core/Lean/Watch) -> HISTORY JSON -> NEXT-DAY GRADE
```

Layer separation per the cross-sport playbook: the performance model produces
fair probabilities from stats only; odds enter only in the value layer; tiers
are absolute gates; presentation and history never change thresholds.

## Data sources

| Need | Source | Fallback |
|---|---|---|
| Play-by-play EPA (1999+) | nflverse-data GitHub release parquet, per season | nflreadr mirrors of the same assets |
| Schedules, final scores, closing spread/total/ML | nflverse/nfldata `games.csv` (Lee Sharpe) | `http://www.habitatring.com/games.csv` |
| Live odds | Bovada coupon JSON `.../football/nfl` | cached last-good payload (fail-open) |
| Historical team-total lines | none free — derived implied `(total ∓ spread)/2` | — |

## Model

1. Replay games chronologically; maintain per-team EWMA ratings of
   opponent-adjusted offensive/defensive EPA per play plus pace
   (offensive plays/game). Season rollover regresses ratings toward league
   mean; early weeks lean on the prior-season prior.
2. Points projection: OLS on tune seasons,
   `points = b0 + b_off*off_rating + b_def*opp_def_rating + b_pace*pace + b_home*home`.
3. Probabilities from **empirical residual distributions** (tune seasons):
   team-points residuals for team totals, margin residuals for spread/ML,
   integer-rounded so key numbers (3, 7) and pushes carry real mass.
4. Value layer: de-vig Bovada two-way prices, edge = p_model − p_market,
   EV at offered price with push handling. Market never inside fair.

## Backtest discipline

- Tune 2015–2022 (coefficients, residual distributions, EV-band inspection).
- Holdout 2023–2025, benchmark vs de-vigged closing lines: log-loss, Brier,
  ROI by EV band per market.
- Toxic band rule: oversized EV (> `NFL_EV_MAX`, default 8%) is forced to
  Watch in production.

## Findings log

### 2026-08-14 — v2 shadow candidate (data foundation + ablation: flat)

Built a parallel v2 candidate model, research-only, without touching v1
probabilities or tiers (`MODEL_VERSION`/`TIER_POLICY_VERSION` unchanged;
`HISTORY_SCHEMA_VERSION` 2→3 for the `v2_shadow` export block).

- **Data foundation** (`sources/nflverse.py`): canonical store now also writes
  `team_situational_games.parquet` (pass/rush EPA + success/sack/explosive/
  early-down splits per team-game, 7,124 rows) and `qb_games.parquet`
  (passer-game EPA/dropback, CPOE, sacks, scrambles; 13,813 rows, 463 QBs,
  `is_primary` per team-game). `games.parquet` now retains rest, location,
  stadium, roof, surface, temp, wind, and starting-QB ids.
- **v2 model** (`ratings/v2.py`): named feature sets — base (=v1, numerically
  identical, asserted by test), +sched (rest/neutral/dome), +split
  (pass/rush opponent-adjusted EWMAs), +qb (lag-starter QB delta + CPOE).
  Writes `live_state_v2_shadow.json` and attaches shadow projections to each
  board history run. Starter = highest-volume passer of the prior completed
  game (leak-free; unknown -> `qb_uncertain`, contributes zero).
- **Ablation backtest** (`backtest_v2.py`, tune 2015–2022, holdout 2023–2025,
  raw probs vs de-vigged closing): **no feature family beats the market.**

  | set | pts MAE | sp LL | tot LL | ml LL |
  |---|---|---|---|---|
  | base | 7.44 | 0.7204 | 0.7045 | 0.6383 |
  | full | 7.42 | 0.7199 | 0.7075 | 0.6374 |
  | market | — | 0.6934 | 0.6933 | 0.6077 |

  Schedule/split/QB context shave ~0.02 points MAE but leave log-loss/Brier at
  market parity (spread/total raw are still overconfident; ML still loses).
  Consistent with the tennis verdict: the closing line already prices
  schedule, pass/rush splits, and QB quality.
- **Implication:** do not promote v2. Keep grading v1 Core/Lean. The v2 shadow
  export stays as prospective research; a real pregame QB/injury source is the
  only remaining lever with plausible upside, and even that is unproven.
- `grade.py` now closes the shadow loop: for every regular-season game it uses
  the latest pre-kickoff snapshot and reports v1 vs v2 points/margin/total MAE.
  Resolved comparison rows export to `grade_*_v2_rows.json`; v2 still has no
  bet selection, tier, or ROI semantics.

### 2026-08-14 — source-safety hardening + diagnostics

Third preseason dry-run triggered a full-source hardening pass (no model or
threshold change; `MODEL_VERSION`/`TIER_POLICY_VERSION` unchanged,
`HISTORY_SCHEMA_VERSION` 1→2 for the richer source diagnostics):

- The Bovada coupon returns HTTP 200 with `{}` when the redundant `lang=en`
  query parameter is present. Removed; `fetch_live_games` now rejects any
  non-list response shape before it can overwrite the last-good cache.
- `_fetch_json` now returns `FetchResult` metadata (mode, attempts,
  http_status, response_bytes, fetched_at, cache_age, error) and retries once
  on transient failures (network / 429 / 5xx / malformed shape), falling back
  to a valid cache, and raising only when no valid cache exists.
- `fetch_live_games` filters events whose kickoff is in the past or missing —
  a stale cached board can no longer resurface an already-started game as
  bettable. `grade.py`'s pre-kickoff snapshot guard is unchanged.
- Team-total diagnostics now record observed `total`-bearing descriptions
  that don't match `_TEAM_TOTAL_PATTERNS` (currently only benign "Odd/Even
  Total Points" / "Total Points Range") plus any team-matched markets missing
  Over/Under outcomes. This is the fixture we'll use to verify the parser when
  real team totals post in game week.
- Terminal output caps Watch rows at 30 (all still exported to history);
  `run_board.py --all-watch` prints everything.
- Added `tests/` (`unittest`, no new deps): cache safety, kickoff filtering,
  main-market + team-total parsing. `python -m unittest discover -s tests`.
- Windows smoke testing found the task account already had another project's
  global `SEND_DISCORD=true`. NFL now uses sport-specific
  `NFL_SEND_DISCORD`, preventing cross-project flags from breaking or sending
  the NFL board. The Tuesday wrapper also rebuilds both v1 and v2 state.

### 2026-08-13 — initial build + first backtest

Tune 2015–2022 (4,346 team-game rows), holdout 2023–2025 (855 games).
Points MAE 7.44, margin MAE 10.27. Full report:
`outputs/backtests/backtest_20260813_*.txt`.

- **Model does not beat closing lines** (expected; same verdict as tennis
  Elo vs Pinnacle). Calibrated probabilities reach parity: spread log-loss
  0.6939 vs market 0.6934; game total 0.6927 vs 0.6933 (marginally better);
  ML 0.6381 vs 0.6077 (worse — the market's injury/QB information shows up
  most in win probability).
- **Per-market calibration shrink** (fit on tune closing lines): spread 0.1,
  total 0.1, team_total 0.1 (grid floor — i.e. ~no standalone signal vs
  closing at the line), moneyline ≈ 1.15 (raw margin-based win probs
  slightly underconfident). Stored in `live_state.json::prob_shrink`;
  exported per candidate as `p_model_cal`.
- **EV bands (raw probabilities, production semantics):** the [2%, 5%) band
  was positive only for team totals (+3.2%, n=241) and game totals (+4.3%,
  n=99); spread and ML negative everywhere in the production window.
  Consistent with the thesis that totals are the softer family.
- **EV ≥ 15% is toxic** (ML band went 99-264, -12.8% ROI): confirms the hard
  `NFL_EV_MAX` cap to Watch.
- First live pull (2026-08-13): Bovada already posts all 16 Week 1 games
  with spread/ML/total; team totals not yet posted; board produced 4 Leans
  in-window and parked 30–69% EV disagreements in Watch, as designed.
- Second dry-run found Bovada returning HTTP 200 with `{}` for the NFL coupon
  when the redundant `lang=en` query parameter was present. Removing it
  restored 16/16 spread, ML, and game-total coverage; the fetcher now rejects
  malformed response shapes before replacing its last-good cache. Team totals
  remain unposted (`tt=0`).

## Roadmap after go-live

1. Weeks 1–4: run board daily, grade Tuesday mornings, no retuning.
2. After ~50–100 graded Core/Lean plays: review EV bands, market mix
   (are team totals actually softer?), Discord promotion.
3. Later: FanDuel scrape (bet-at prices), alt team totals, injury/QB-out
   flags as shadow fields first, playoffs handling.
