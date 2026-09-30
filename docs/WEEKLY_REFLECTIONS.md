# Weekly Reflections — nfl_props

## Template (copy for each week)

### Week N — [Date Range]

#### Record
- **Picks**: X-Y-Z (W-L-P)
- **Units**: +/- X.XXu
- **ROI**: +/- XX.X%

#### By Market Family
| Family | Record | Notes |
|--------|--------|-------|
| Moneylines | X-Y-Z | |
| Spreads | X-Y-Z | |
| Totals | X-Y-Z | |

#### Model Behavior
- **Winner accuracy**: XX% (baseline: XX%)
- **Margin MAE**: X.XX
- **Total MAE**: X.XX
- **Average |edge|**: X.X%

#### What Worked
- [observations]

#### What Didn't Work
- [observations]

#### Anomalies / Red Flags
- [anything unusual]

#### Learnings
- [key takeaways]

#### Action Items (if any)
- [changes to consider]

---

## Week 1 — Sep 9-15, 2026 (Season Opener)

### Record
- **Reference record**: 21-21-0 across 42 priced references (not 42 independent games)
- **Units**: -4.30u
- **ROI**: -10.2% (flat 1u per priced reference)
- **Forecast accuracy**: winner 8/14 (57.1%); margin MAE 11.721; total MAE 11.579

| Family | Record | Units | ROI |
|---|---:|---:|---:|
| Moneylines | 8-6-0 | -1.48u | -10.6% |
| Spreads | 6-8-0 | -2.55u | -18.2% |
| Totals | 7-7-0 | -0.28u | -2.0% |

### Model Context
- **Model version**: nfl-forecast-v1.1
- **All teams**: 0 current-season games (carryover-regressed)
- **Forecast state**: built 2026-09-12, fit on 3,295 paired games (2014-2025)
- **Feature set**: full (21 features for margin/total, 13 for winner)

### Pre-Week 1 Observations

#### 1. QB EPA Coefficient Anomaly
The winner model's `qb_epa_h` coefficient is **-0.152** (negative), meaning better home QB play *reduces* home win probability. This is counterintuitive. The margin model has the correct sign (+0.487). Possible causes:
- Multicollinearity with `off_h`/`def_a` features
- The logistic regression's IRLS optimization found a local minimum
- The feature is capturing a different dynamic (e.g., good QBs on bad teams)

**Impact**: The winner model's moneyline picks may be less reliable than the margin/total models.

#### 2. Low-Confidence Edge Profile
- All 18 Legacy Lean picks had `games_current_min: 0`
- All flagged `LOW_SAMPLE`
- Edges: 2-7% (small)
- EV: 2-8% (marginal)
- After calibration shrink: p_model_cal ≈ 0.50 (honest "I don't know")

#### 3. Market Mix
Legacy board Lean picks by market:
- Game totals: ~6 picks (UNDER-heavy)
- Spreads: ~6 picks (mixed sides)
- Team totals: ~6 picks (mixed OVER/UNDER)

Forecast board PLAYABLE references:
- 6 moneylines, 11 spreads, 9 totals

#### 4. Key Numbers
- Model total intercept: 45.4 (reasonable NFL baseline)
- Model margin home advantage: +0.58 pts (low vs historical ~2.5-3)
- Margin residual SD: 13.2 (high uncertainty)
- Total residual SD: 13.3 (high uncertainty)

#### 5. Structural Safeguards Working
- LOW_SAMPLE flag correctly capped all plays at Lean
- Single-side rule prevented double-counting
- EV cap (>8%) moved oversized disagreements to Watch
- Pipeline ran end-to-end (data → board → Discord → grade)

### What to Watch in Week 2
1. Does the QB EPA anomaly persist? Check if Week 1 data changes the sign.
2. Do edges grow as current-season data accumulates?
3. How does the margin model's home advantage compare to actual Week 1 results?
4. Are totals still the "soft" family? (Backtest showed +3.2% in [2%,5%) band)

---

## Week 2 — Sep 17-21, 2026

### Scope and method

- **Model**: `nfl-forecast-v1.1`; the Tuesday weekly report graded the latest
  available pre-kickoff forecast for each scheduled Week 2 game.
- **Games**: 16 forecasted and resolved; no pending games.
- **Comparison**: descriptive forecast accuracy and flat-1u reference results
  at the prices/lines captured with each forecast. References are not
  recommendations or a record of wagers placed.
- **Interpretation**: the 48 references (one moneyline, one spread and one
  total per game) are correlated within games. ROI and W-L counts are useful
  diagnostics, not 48 independent observations. This is one small weekly
  sample; no model or policy changes follow from it.

### Results

| Measure | Week 1 | Week 2 | Week 2 detail |
|---|---:|---:|---|
| Forecasted games | 14 | 16 | All 16 resolved |
| Winner accuracy | 8/14 (57.1%) | 9/16 (56.3%) | Brier 0.2402 for Week 2 |
| Margin MAE | 11.721 | 12.269 | Market-line MAE 11.84 |
| Total MAE | 11.579 | 11.037 | Market-total MAE 10.69 |
| Priced reference record | 21-21-0 | 24-24-0 | 48 references |
| Flat-1u reference units | -4.30u | -6.08u | -12.7% ROI |

| Family | Week 1 record | Week 2 record | Week 2 units | Week 2 ROI |
|---|---:|---:|---:|---:|
| Moneylines | 8-6-0 | 9-7-0 | -3.53u | -22.1% |
| Spreads | 6-8-0 | 8-8-0 | -0.18u | -1.1% |
| Totals | 7-7-0 | 7-9-0 | -2.37u | -14.8% |

By reference source, Bovada returned 18-16-0, -4.30u (-12.7%); Polymarket
returned 6-8-0, -1.78u (-12.7%). Source counts differ because the captured
reference set can contain more than one source and availability varies.

### Detailed findings: what the model overstated or underestimated

#### 1. Home-win confidence exceeded the results, but this was not simply a home-pick bias

The model picked the home winner in 12 of 16 games; home teams won 7 of 16.
Across all games, its mean home-win probability was 58.1%, while the home
win rate was 43.8%—a five-win difference between the number of home picks and
the number of home teams that actually won (12 versus 7).

However, the market also favored the home team in 12 games. The overall
home-favorite tendency was therefore shared with the market, not unique to
the model. The more diagnostic subset is where the model's winner side
disagreed with the market's home/away favorite: **both disagreements lost**.

- **CAR @ ATL**: model picked ATL (home win probability 57.2%; projected
  margin ATL +3.1) while the market made CAR the favorite. CAR won 34-3;
  actual home margin was -31, a 34.1-point margin projection error.
- **JAX @ DEN**: model picked JAX (away win probability 57.9%; projected
  margin JAX +2.8) while the market favored DEN. DEN won 20-13.

For comparison, the Week 1 model/market winner disagreements went 1-4 for
the model. Across the two weeks that is 1-6, a notable warning signal, but
still a tiny sample and not proof that the market should override the model.

Confidence was not uniformly bad: the three Week 2 forecasts whose selected
winner confidence was at least 70% all won. In the 10 games with selected-side
confidence from 60% to under 70%, the model went 5-5. This points more toward
possible overconfidence in the middle-confidence band and market-reversal
cases than toward every confident forecast being unreliable. Two weeks do
not establish calibration; continue recording probability-based metrics.

#### 2. Margin errors were asymmetric in the tails, not a clean constant bias

Actual home margin averaged -0.44 points, against a model-projected average
of +3.16: on average, outcomes were 3.59 points more favorable to away teams
than projected. The median game-level margin error (actual minus projected)
was only -0.55 points, though, and errors split evenly in sign (8 above and
8 below projection). Large misses pull the mean; a uniform 3.6-point
correction would not describe the week.

Examples of underestimating away-team performance included CAR winning at
ATL by 31 despite an ATL +3.1 projection, NO winning at BAL by 7 despite a
BAL +7 projection, and LV winning at LAC by 12 despite a LAC +5.2 projection.
The model also underestimated some home-team blowouts: LA beat NYG by 22
after a projected LA +3.9, and DAL beat WAS by 17 after DAL +1.9. This pattern
is consistent with a model that did not capture the realized size of several
team-performance extremes, in both directions, rather than a general
home/away adjustment error.

Margin MAE was 12.27, slightly worse than Week 1's 11.72 and the recorded
market spread's 11.84. The spread references nevertheless finished 8-8,
near-even in unit terms (-0.18u). Forecasted margin accuracy and spread
reference outcomes measure related but different things: the former compares
point projections, the latter grades the selected side against a line at a
price.

#### 3. Total projections leaned high, but a few very low totals drove most of the mean miss

Actual totals were below the model projection in 12 of 16 games. Mean model
total was 45.96, versus 40.44 actual: a +5.52-point average overestimate.
The median error (actual minus projected) was -6.85 points. Week 1 went the
opposite way: actual totals averaged 6.15 points above the model. The
two-week direction has therefore reversed, not repeated.

Four games were particularly influential:

| Game | Projected total | Actual total | Error (actual - model) |
|---|---:|---:|---:|
| MIN @ CHI | 35.8 | 12 | -23.8 |
| CIN @ HOU | 49.6 | 26 | -23.6 |
| PIT @ NE | 43.8 | 23 | -20.8 |
| NYG @ LA | 51.9 | 34 | -17.9 |

Removing those four leaves a mean model-total error of -0.19 points across
the other 12 games. That sensitivity cautions against interpreting Week 2
alone as evidence of a stable high-total bias. The market's recorded totals
also averaged 4.69 points above actual results; model total MAE was 11.04
versus 10.69 for the market line.

MIN @ CHI is a useful distinction between detecting direction and estimating
magnitude. The forecast incorporated substantial precipitation and projected
35.8, well below the market total (47.5); the game finished at 12. The model
recognized a lower-scoring setup relative to market, but still overestimated
the realized total by 23.8 points. Do not attribute this miss solely to the
weather feature: one game cannot tell us whether the feature, other inputs,
or game-specific execution explains the remaining error.

Totals reference results were 7-9, -2.37u (-14.8%). Of 13 PLAYABLE total
references, 6 won and 7 lost; Week 2's total losses are not explained only
by the four most extreme forecast misses.

#### 4. Large positive model-market edges again had poor outcomes

Among the 12 Week 2 references with a positive edge of at least 10 percentage
points, 3 won and 9 lost, returning -6.28u. This group included the ATL home
spread against CAR, JAX spread and moneyline at DEN, MIA spread at SF, SEA @
ARI over, CIN @ HOU over, and NYG @ LA over. The mean edge across the 12 was
large, but realized performance was poor.

Week 1's corresponding 10%+ edge band was also negative: 6 wins from 15
references, -2.94u. By contrast, Week 2's 6-10% band went 9-4 for +4.24u.
These are small, noisy, retrospectively grouped samples; they support close
monitoring of whether extreme disagreements are systematically overstated,
not a change to the playable policy now. The 10%+ group includes correlated
references from the same games and families.

#### 5. The model was not uniformly wrong across all games or labels

- Three 70%+ winner forecasts were correct.
- Margin spread references improved from 6-8 in Week 1 to 8-8 in Week 2,
  despite a small deterioration in margin MAE.
- Total forecasts were close in some games, including PHI @ TEN (45.1
  projected, 44 actual) and LV @ LAC (40.8 projected, 40 actual).
- The spread-reference result was almost break-even in units, while
  moneyline references returned -3.53u and totals -2.37u. The distinction
  argues against treating the whole system as having one undifferentiated
  failure mode.

### Cross-week interpretation

1. **Winner accuracy is stable but modest so far**: 57.1% in Week 1 and
   56.3% in Week 2. The Week 2 mean home-probability overprediction and the
   1-6 record on model/market winner disagreements deserve continued tracking.
2. **Margin point error has edged upward**: 11.72 to 12.27 MAE. Directional
   mean bias changed from approximately -0.25 points in Week 1 to -3.59 in
   Week 2, while the Week 2 median was only -0.55. The weekly mean is
   sensitive to extreme games.
3. **Total bias changed sign**: Week 1 actual totals averaged +6.15 above
   model projections; Week 2 averaged -5.52 below. Excluding four Week 2
   low-total outliers reduces its average miss to -0.19. Look for persistence
   across more weeks before proposing an intercept or weather adjustment.
4. **ROI is negative in both weeks**, but the path differs: Week 1 was
   21-21 and -4.30u; Week 2 was 24-24 and -6.08u. Week 2's spread units
   were near flat, with the largest family losses in moneyline and totals.
5. **Large disagreement remains the clearest repeated concern**: the 10%+
   edge band was negative in both weeks, but the band sizes are small and
   outcomes remain noisy. Preserve per-play details and compare again after
   additional weeks rather than inferring a threshold from two samples.

### What this says about the model (hypotheses to test, not conclusions)

- It may be **overstating confidence when its independent forecast sharply
  contradicts the market**, especially for outright winner reversals. Week 2
  had two such games and both lost; Week 1 had five and only one won.
- It may be **underrepresenting game-to-game tail risk** in margins and
  totals: several blowouts and low-scoring games produced large projection
  errors, while median margin error was comparatively small. This is a
  diagnostic hypothesis, not yet evidence for widening residuals or changing
  the model.
- It may be **overrating large estimated edges**. Large positive edges were
  poor in both weeks, while mid-sized Week 2 edges did better. Check whether
  those large edges cluster in specific families, sources, teams, or market
  line disagreements before identifying a mechanism.
- The opposing total misses in Weeks 1 and 2 warn against overreacting to one
  week's scoring environment. Separate persistent calibration error from
  normal NFL variance and a few high-leverage outliers.
- The Week 1 inverted winner `qb_epa_h` coefficient remains an unresolved
  model diagnostic. These aggregate weekly outcomes do not establish that it
  caused Week 2's misses; retain it as a separate item to monitor.

### Follow-up ledger for subsequent weeks

For comparability, each weekly entry should preserve: model/policy versions;
forecast and pending game counts; winner accuracy, Brier/log loss when
available; margin and total MAE; actual-minus-projected mean **and median**;
market-line MAE where available; family W-L-P, units and ROI; reference source
counts; edge-band counts and returns; winner confidence bands; model/market
winner disagreements; and the largest absolute margin/total misses. Record
outlier sensitivity only as a diagnostic, with the excluded games listed.
Keep forecast accuracy separate from price-reference ROI, and avoid
retroactive labels or thresholds that make weeks incomparable.

### Week 3 watch list (retrospective; questions carried into Week 3)

1. Does the winner model continue to overpredict home wins or miss when it
   reverses the market favorite?
2. Do 60-70% winner probabilities remain less reliable than 70%+ picks, and
   do probability-based metrics support that split over a larger sample?
3. Do large positive edges again underperform, and are misses concentrated in
   a family, source, or type of model/market disagreement?
4. Does the total-projection direction persist, or reverse again? Track mean,
   median, error distribution, and outlier sensitivity together.
5. Does margin MAE remain above the market's spread-line MAE, and are large
   margin misses still balanced between home- and away-favored outcomes?

**Decision status at end of Week 2:** observation only. No model, feature,
coefficient, threshold, action-label, or production-policy changes were proposed.



---

## Week 3 — Sep 24-28, 2026

### Scope and method

- **Model**: `nfl-forecast-v1.1`; latest available pre-kickoff forecast selected
  for each of the 16 scheduled, resolved regular-season games.
- **Comparison**: price-free winner/margin/total forecasts are evaluated
  separately from 48 flat-1u references at captured prices (one per market per
  game). The references are correlated within games and are not a record of
  wagers placed.
- **Evidence**: VM weekly grade artifact
  `outputs/forecast_backtests/grade_weekly_2026w03_20260929_073002.json`,
  forecast history snapshots, `data/raw/games.csv`, and
  `logs/nfl_weekly_grade.log`. Weekly grade completed on Sep 29 with no pending
  games. Margin/total mean and median errors, market-line MAEs, Brier/log loss,
  confidence splits, and edge bands below were independently calculated from
  the selected snapshots and final scores using the same latest-pre-kickoff
  rule. Market-line MAEs are approximate diagnostics calculated from the
  captured reference lines and are not the official weekly grade fields.

### Results

| Measure | Week 1 | Week 2 | Week 3 | Week 3 detail |
|---|---:|---:|---:|---|
| Forecasted games | 14 | 16 | 16 | All resolved |
| Winner accuracy | 8/14 (57.1%) | 9/16 (56.3%) | 10/16 (62.5%) | Brier 0.2257; log loss 0.6405 |
| Margin MAE | 11.721 | 12.269 | 8.100 | Captured spread-line MAE ≈8.50 |
| Margin error, actual − model | — | mean −3.59; median −0.55 | mean −0.02; median −0.25 | 8 errors each above/below zero |
| Total MAE | 11.579 | 11.037 | 11.775 | Captured total-line MAE ≈11.03 |
| Total error, actual − model | — | mean −5.52; median −6.85 | mean +1.75; median +1.90 | 8 above/8 below model |
| Priced reference record | 21-21-0 | 24-24-0 | 27-21-0 | 48 references |
| Flat-1u reference units | −4.30u | −6.08u | +1.52u | +3.2% ROI |

| Family | Week 3 record | Units | ROI |
|---|---:|---:|---:|
| Moneylines | 10-6-0 | +0.62u | +3.9% |
| Spreads | 11-5-0 | +5.16u | +32.2% |
| Totals | 6-10-0 | −4.25u | −26.6% |

By reference source, Bovada returned 18-15-0, −1.26u (−3.8%) from 33
references; Polymarket returned 9-6-0, +2.78u (+18.5%) from 15. Source mix and
availability vary; this is descriptive, not evidence of source superiority.

### Detailed findings: what the model got right and wrong

#### 1. Winner performance improved; both market reversals won, but n=2

The model selected 10 correct winners (62.5%), its best of the three weeks so
far. Brier (0.2257) and log loss (0.6405) also improved from Week 2's Brier
0.2402. Its 60%-to-under-70% selected-confidence group went 4-7, while the two
picks at 70%+ both won. The broad 60–70% versus 70%+ difference remains too
small to infer calibration from: each bin has very few games.

The model picked the home winner in 9 games and home teams won 11; mean
predicted home wins were 8.97. This contrasts with Week 2, when the model
picked home 12 times and home teams won 7. Both Week 3 disagreements with the
captured moneyline favorite went the model's way:

- **HOU @ IND**: model home-win probability 56.9%; market implied home
  probability about 46.6%. Indianapolis won 19-17.
- **PHI @ CHI**: model home-win probability 64.7%; market implied home
  probability about 36.4%. Chicago won 27-7.

These improve the cumulative model/market reversal record from 1-6 after two
weeks to **3-6 across three weeks** (9 games total). That remains a small,
selected group; it is not evidence to make the market override the model or to
accept every large reversal.

#### 2. Margin fit improved substantially, while blowout errors still cancel

Margin MAE fell to 8.10, lower than the approximately 8.50 MAE of the captured
spread lines on these games. Mean and median actual-minus-projected margin
errors were near zero (−0.02 and −0.25), split evenly in direction. That
aggregate improvement hides opposing tail misses:

- **ATL @ GB**: projected home margin +5.9, actual −21; error −26.9.
- **NE @ JAX**: projected home margin +2.9, actual +29; error +26.1.
- **PHI @ CHI**: projected home margin +4.3, actual +20; error +15.7.
- **LV @ NO**: projected home margin +6.3, actual −8; error −14.3.

The two largest absolute errors (ATL–GB and NE–JAX) nearly cancel; excluding
them leaves margin MAE about 5.47 across the other 14, but this is a
sensitivity diagnostic, not an alternative score. The model was materially
better on average than Week 1–2 and still did not capture several game tails.

#### 3. Totals remained the weakest projection family, with misses in both directions

Totals reference outcomes were 6-10 (−4.25u). Of 13 `PLAYABLE` totals, 4 won
and 9 lost (−5.10u). Over references went 4-6; unders 2-4. Total MAE rose to
11.775, above the captured total-line MAE of approximately 11.03.

Actual totals were above and below model projections in eight games each; mean
error was +1.75 and median +1.90. The largest misses included:

| Game | Projected total | Actual total | Error (actual − model) |
|---|---:|---:|---:|
| TEN @ NYG | 41.5 | 19 | −22.5 |
| SEA @ WAS | 43.5 | 64 | +20.5 |
| ARI @ SF | 46.5 | 66 | +19.5 |
| LV @ NO | 43.4 | 62 | +18.6 |
| CIN @ PIT | 40.8 | 57 | +16.2 |

Removing the four largest absolute errors (TEN–NYG, SEA–WAS, ARI–SF,
LV–NO) leaves a mean error of −0.67 across 12 games, which illustrates
outlier sensitivity rather than proving a calibrated center. This is not the
same pattern as Week 2's low-scoring skew: the sign is now balanced and
opposite-direction high-leverage games dominate.

#### 4. Spreads drove the positive reference return; totals offset most of it

Spread references went 11-5 for +5.16u; moneylines added +0.62u; totals gave
back −4.25u. Nine of 12 `PLAYABLE` spreads won (+5.49u). The return was
concentrated: the three PHI–CHI references netted approximately +1.55u, slightly
more than the full week's +1.52u. Excluding those three leaves the other 45
references roughly flat (−0.03u). The spread performance is encouraging, not
proof of a durable edge independent of this game.

Eight references had positive edge ≥10 percentage points: five won and three
lost (+2.66u). This reverses the negative result in each of Weeks 1 and 2,
but includes multiple correlated references in HOU–IND and PHI–CHI. The first two weeks together were 9-18; the cumulative three-week 10%+ band
is 14-21, approximately −6.56u by summing the rounded weekly units. The most
useful interpretation is “keep measuring”; neither promote nor reject the band
from this one week.

### Cross-week interpretation and hypotheses to test

1. **No single market family explains the weekly story.** Overall reference
   ROI was negative in Weeks 1–2, then slightly positive in Week 3. Spreads
   improved sharply in Week 3; totals were negative in Weeks 2–3; moneyline
   units were especially poor in Week 2. Keep point-forecast accuracy,
   reference side results, prices, and units distinct.
2. **Winner forecasts are modestly accurate, and disagreement results are
   volatile.** Accuracy has been 57.1%, 56.3%, 62.5%. Market reversals went
   1-4, 0-2, then 2-0 by week; 3-6 overall (9 games). Test reversal
   calibration across seasons and predeclared disagreement-size bins, with
   game-clustered uncertainty, rather than inferring a market override from
   nine games.
3. **Large positive edges are unresolved, not consistently bad or good.** The
   ≥10% edge bin lost in Weeks 1–2 (cumulative 9-18) and won in Week 3
   (5-3, +2.66u), leaving 14-21 cumulatively across all three weeks. Test
   calibration and realized return by
   continuous edge, market family, edge source/coverage, and game, on
   time-separated holdouts. Cluster references within games; report counts and
   uncertainty; avoid choosing a threshold from these outcomes.
4. **Totals show large errors, but no stable one-way bias yet.** Week 1's
   actual totals averaged above model; Week 2 below, driven by a few low
   outliers; Week 3 was near balanced by direction but had large misses both
   ways. Persistent signal is error magnitude and tail sensitivity, not a
   defensible intercept adjustment. Examine residual calibration, dispersion,
   conditional error by predicted total/weather/pace, and whether predictive
   intervals achieve their nominal coverage on rolling out-of-time folds.
5. **Margin central tendency looks better than tail magnitude.** MAE worsened
   W1→W2 then improved below the market line in W3. Week 2 and 3 both had
   near-balanced signed errors but sizeable, opposing blowouts. Test whether
   residual variance/tail coverage is miscalibrated; do not simply widen
   uncertainty unless out-of-time interval coverage supports it.
6. **The negative winner `qb_epa_h` coefficient remains a model diagnostic.**
   It persists in the current VM fit, but weekly aggregate results cannot
   identify it as causal. Check coefficient stability and feature ablation
   across rolling season splits, especially incremental log loss/Brier and
   calibration; account for correlated EPA/QB features.

### Proposed evaluation (no production changes)

- Pre-register hypotheses and metrics before examining another week's results.
- Use rolling-origin season/week splits; fit all transforms and any probability
  calibration on training/validation periods only, never on the evaluation
  week. Compare each proposed feature/model variant against the unchanged
  `nfl-forecast-v1.1` baseline on identical games.
- For probabilities, report log loss, Brier, reliability/calibration and
  discrimination, with uncertainty clustered/bootstrap-resampled by game (and
  season where practical); show confidence-bin sample counts.
- For projections, report MAE, signed mean/median errors, residual quantiles,
  and interval coverage beside captured market-line MAE. Show outlier
  sensitivity with named games, but retain all games in headline metrics.
- For reference value, evaluate at genuinely pre-kickoff captured prices and
  preserve source/family/label/edge distributions; use game-level clusters
  because references from a matchup are dependent. Do not treat retrospective
  price snapshots as closing line value.

### Consultant review

An independent consultant approved an observation-first plan through Week 4,
pre-registered offline analyses by Week 6, and no production change before a
Week 8 evidence review. Prior advice included a holdout ablation of the
inverted `qb_epa_h` coefficient. A subsequent base/QB/full ablation
(`outputs/backtests/backtest_v2_20260814_040308.txt`) shows trivial differences
in winner log loss (base 0.6383, QB-only 0.6381, full 0.6374), all worse than
the market at 0.6077. That makes this coefficient a monitoring diagnostic,
not an established fix or reason to change the winner model. The consultant
also recommended prospective reversal tracking, predeclared winner
calibration, and continued total signed-error/interval-coverage monitoring.
The separate historical closing-price holdout comparison in
`outputs/backtests/backtest_20260813_232254.txt` the winner model's log loss
(0.6381) and Brier (0.2237) were worse than market (0.6077 and 0.2102), while
spread and total log-loss/Brier were near market parity. This makes the
winner-market comparison a meaningful prior, but does not prove live Week 3
reversals should be treated differently.

I reconciled several arithmetic/definition points against the Week 1–2
reflection, grading code, and Week 3 VM records:

- The winner-reversal result is **1-6 through Weeks 1–2**, then 2-0 in Week 3,
  hence **3-6 across 9 games**, not 3-9. The consultant's 2-2 characterization
  of Week 3 was incorrect; the two Week 3 reversals in the recorded result were
  both wins.
- Using the documented positive-edge `edge >= 10%` band, Week 1 was 6-9
  (−2.94u), Week 2 was 3-9 (−6.28u), and Week 3 was 5-3 (+2.66u). Thus the
  first two weeks sum to 9-18; all three sum to **14-21 over 35 references**
  (−6.56u from the displayed rounded weekly units; individual artifact
  aggregation may differ by rounding). The 14-21 figure belongs to all three
  weeks, not Weeks 1–2. References remain correlated within games.
- Push arithmetic is consistent: 42 + 48 + 48 = 138 references; 21+24+27 =
  72 wins and 21+24+21 = 66 losses, with zero pushes reported in each weekly
  record. The consultant's claimed three missing pushes came from an addition
  error.

The historical holdout output's `EV >= +0.15` moneyline band reports 275
references, 81-193-1, −16.1% ROI; that is the verified file value and differs
from the consultant's quoted −12.8%. These are historical closing-price
backtest results, a different cohort and definition from live captured-price
references. They do not establish future profitability or justify changing
the production model.

**Decision status:** observation and offline test proposals only. No feature,
coefficient, calibration, threshold, action-label, or production-policy change
is supported by three small weekly samples.

**Correction to the preceding narrative:** the quick first-pass prose misstated
the direction and scale of the LV @ NO margin result. Actual home margin was
NO −8 versus a projected NO +6.3 (error −14.3). The largest absolute Week 3
margin misses are ATL @ GB, NE @ JAX, PHI @ CHI, and LV @ NO.
