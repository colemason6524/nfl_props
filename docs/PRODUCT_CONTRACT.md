# Product Contract — nfl_props (forecast-first)

**Authoritative.** If any other document, comment, or code path contradicts
this file, this file wins. Update it deliberately; do not let behavior drift
away from it.

Effective: Week 1, 2026 season. Supersedes the `core-lean-watch-v2`
price-screened product for all new output. Legacy snapshots remain in
`outputs/history/` for historical grading only and are never pooled with the
forecast cohort.

## 1. What the product is

An **independent-forecast NFL board**:

1. The **model** forecasts every scheduled game on the current day. It never
   reads a sportsbook price, spread, total, or moneyline when producing a
   projection, a probability, or a winner.
2. **Every** scheduled game on the current day is published, including games
   with no market price. Coverage never silently disappears.
3. Sportsbook lines and prices (Bovada primary, Polymarket fallback) are
   attached **after** the forecast, as descriptive references.
4. The viewer decides what, if anything, is worth acting on. The program does
   **not** recommend wagers.

The forecast is the product. The price is a downstream descriptive quantity;
the model decides the prediction, the price determines the action.

## 2. Scope

Supported markets, one independent opinion each:

- **Moneyline** — the winner model's pick.
- **Spread** — the margin model's projected margin compared with the posted
  line; the posted line defines the proposition, never the projection.
- **Game total** — the total model's projected total compared with the posted
  number.

Universe and timing:

- The official nflverse schedule, filtered to the **current calendar day in
  Eastern Time**.
- Games already started are excluded from new forecasts.
- Runs: Thursday (Thursday games), Monday (Monday game), Sunday morning
  (early slate), Sunday afternoon (remaining evening slate).
- Continues into the postseason; no separate playoff policy.

Not yet supported (documented future scope):

- Player props.
- Alternate lines, first halves, quarters, and other specials.
- Live/in-play markets.
- Multi-book comparison beyond Bovada/Polymarket.

## 3. The forecast

- Exactly one `GameForecast` per scheduled current-day game per run.
- Three separate family models, each owning its own target:
  - `winner` — L2 logistic P(home win); owns the moneyline probability. Ties
    are excluded from the fit.
  - `margin` — ridge OLS on (home − away) points; owns the spread
    distribution.
  - `total` — ridge OLS on (home + away) points; owns the over/under
    distribution.
- Family outputs are **not reconciled**; a family's probability is that
  family's truth and internal disagreement is displayed, not hidden.
- No final score, projected score, or tie probability is published. Only the
  moneyline pick, projected margin, and projected total are shown.
- No price, spread, total, or moneyline is an input to any family. Historical
  prices are used only for evaluation (backtest, ROI).

## 4. Price handling

- Bovada is the primary reference; Polymarket is a fallback reference used
  when Bovada is unavailable, stale, or empty.
- Each source is de-vigged independently. The **consensus** market
  probability is the average of the available sources' de-vigged probability
  for the model's chosen side. Per-source references are retained.
- The model's pick is fixed before pricing. A price **never** flips the
  moneyline pick, and a line change never flips the model's projection. The
  opposite side is never promoted because it has "more value."
- A missing or unfavorable price never removes or demotes a forecast.
- Expected value is flat-1-unit, push-aware, and **descriptive**.

## 5. Action labels

Published defaults (env-tunable):

| Label | Rule |
|---|---|
| `PLAYABLE` | model EV at the reference price >= `NFL_VALUE_PLAYABLE` (default 0%) |
| `NO_VALUE` | a price exists but EV < the playable threshold |
| `UNPRICED` | no usable reference price captured |

Labels are descriptive, not advice, and change only with a versioned policy
note. Numeric model probability, consensus market probability, edge, and EV
are always shown alongside the label.

## 6. Ties

NFL regular-season games can tie. A tied game settles the moneyline reference
as a **push** (stake returned); it is never counted as a loss. Spread and
total references settle as pushes when they land exactly on the number.

## 7. Presentation

- Terminal output and Discord are organized into exactly three sections:
  **Moneylines**, **Spreads**, **Totals**.
- Discord contains **every** current-day game in each applicable section.
  Messages are chunked only to respect Discord's size limit; no section is
  truncated and no game is dropped.
- There is no conversational digest. Unpriced games are shown and labelled.
- The words "lock", "play", and "wager" are prohibited in output.

## 8. Records and grading

Separate, non-poolable records:

- Forecast accuracy (winner accuracy excluding ties, Brier, log loss,
  margin MAE, total MAE).
- Reference value (flat-1-unit ROI at captured prices, by family, source, and
  action label).

Rules:

- The **latest pre-kickoff forecast** is the headline record, per stable
  forecast id.
- The forecast cohort (`nfl-forecast-v1`) is never pooled with the legacy
  price-screened cohort (`nfl-epa-points-v1.1`).
- ROI weights wins by price: a short-priced win is not equivalent to a loss.

## 9. Versioning

- `FORECAST_MODEL_VERSION` (`nfl-forecast-v1`) changes when projections or
  probabilities change.
- `PRODUCT_POLICY_VERSION` changes when product semantics change.
- `VALUE_POLICY_VERSION` changes when action labels change.
- Legacy identifiers stay frozen for historical grading.

## 10. Feature policy

The model uses measurable, point-in-time team variables: opponent-adjusted
play-based EPA (overall and pass/rush), pace/play volume, schedule context
(rest, dome/outdoor, neutral site, divisional familiarity), team-quality and
rivalry history, and lagged quarterback quality. Weather (temperature,
precipitation, wind) is plumbed through the feature layer and contributes
neutrally until a forecast-weather source is populated. Personnel context
(injuries, inactives, confirmed starters) is scaffolded as a fail-open
adapter: a forecast is never blocked by its absence.

Richer inputs are collected and evaluated separately. A feature enters a
production model only after it demonstrates out-of-sample improvement. The
user has asked to move aggressively: candidate feature sets are tested on the
paired historical frame, and per-target promotion is allowed.
