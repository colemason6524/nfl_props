# Weekly forecast-grade diagnostics (Week 4 rollout)

## Scope

`grade_forecast.py --weekly` continues to select the latest available
pre-kickoff forecast per game and grade model accuracy separately from the
flat-1u captured-price references. The forecast model remains
`nfl-forecast-v1.1`; no model features, probabilities, value thresholds,
action labels, or production policy changed in this rollout.

The weekly JSON artifact now carries additional observational diagnostics
under grading version `nfl-forecast-grading-v2`:

- Winner `n`, accuracy, Brier score, and log loss (ties excluded).
- Margin and total MAE, plus signed mean/median residuals with the sign
  convention **actual minus predicted**; the five largest absolute residuals
  are identified by game.
- Forecast-vs-reference line residual summaries for spread and total,
  separately from model residuals.
- Margin-direction reversals, separately from winner-vs-market favorite
  reversals. A winner reversal is counted when the captured de-vigged
  `p_market` for the model-selected moneyline side is below 0.5. Its weekly
  summary reports sample size, correct/incorrect, and win rate.
- Winner confidence groups with sample size, correct count, accuracy, mean
  selected-side confidence, and observed calibration gap.
- Captured reference outcomes grouped by stored edge band.

These are descriptive diagnostics. A weekly subgroup is small, and the three
references in a game are correlated. Do not interpret an observed edge,
confidence bucket, reversal record, or source split as a validated action rule.
The public Discord recap format is unchanged; the additional fields are in the
weekly JSON report. Cumulative grading remains behaviorally unchanged apart
from the grading-version identifier.

## Local verification

```sh
python3 -m unittest tests.test_weekly_grade
python3 -m unittest discover -s tests
```

No model-state rebuild is required: `FORECAST_MODEL_VERSION` and model inputs
are unchanged. No dependency update is required.

## Deployment verification

After the approved commit is pushed and pulled on the Linux VM, check the next
scheduled weekly grade for `grading_version: nfl-forecast-grading-v2` and the
new diagnostic blocks. Check `logs/nfl_weekly_grade.log` for a successful run;
verify the produced weekly JSON artifact and test the grading command locally
before deployment. Do not force a live grade or send another Discord message
for this rollout. If the next naturally scheduled grade is not yet available,
record the deployment as verified but the new production artifact as pending.
