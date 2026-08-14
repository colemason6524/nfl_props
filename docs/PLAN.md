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
| Discord | Core-only digest, `NFL_DISCORD_WEBHOOK_URL`, default off |

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

## Roadmap after go-live

1. Weeks 1–4: run board daily, grade Tuesday mornings, no retuning.
2. After ~50–100 graded Core/Lean plays: review EV bands, market mix
   (are team totals actually softer?), Discord promotion.
3. Later: FanDuel scrape (bet-at prices), alt team totals, injury/QB-out
   flags as shadow fields first, playoffs handling.
