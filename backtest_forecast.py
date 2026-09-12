#!/usr/bin/env python3
"""Leak-free historical validation of the forecast-first family models.

Fits winner/margin/total on tune seasons and evaluates on an untouched
holdout. No sportsbook price enters fitting or evaluation — forecast quality
only. Feature sets (base / full) are compared on the identical paired frame.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List

import numpy as np

from nfl_props import config
from nfl_props.config import FORECAST_BACKTESTS_DIR, HOLDOUT_SEASONS, TUNE_SEASONS
from nfl_props.forecasting import (_design, fit_forecast_models,
                                   paired_features)
from nfl_props.ratings.v2 import replay_v2
from nfl_props.rivalry import compute_rivalry_frame
from nfl_props.sources.nflverse import load_processed_v2
from nfl_props.sources.weather import load_weather


def _predict(fit: dict, rows) -> dict:
    coef_w = np.asarray(fit["winner"]["coefs"], dtype=float)
    coef_m = np.asarray(fit["margin"]["coefs"], dtype=float)
    coef_t = np.asarray(fit["total"]["coefs"], dtype=float)
    eta = _design(rows, fit["winner"]["features"]) @ coef_w
    p_home = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
    mu_margin = _design(rows, fit["margin"]["features"]) @ coef_m
    mu_total = _design(rows, fit["total"]["features"]) @ coef_t
    return {"p_home": p_home, "mu_margin": mu_margin, "mu_total": mu_total}


def evaluate(fit: dict, rows, label: str,
             train_margin_mean: float, train_total_mean: float) -> dict:
    pred = _predict(fit, rows)
    margin = rows["margin"].to_numpy(dtype=float)
    total = rows["total"].to_numpy(dtype=float)
    tie = margin == 0
    p = pred["p_home"]
    p_nt, margin_nt = p[~tie], margin[~tie]
    y_nt = (margin_nt > 0).astype(float)
    pick_home = p_nt >= 0.5
    accuracy = float(np.mean(pick_home == y_nt.astype(bool)))
    brier = float(np.mean((p_nt - y_nt) ** 2))
    eps = 1e-9
    logloss = float(-np.mean(
        y_nt * np.log(np.clip(p_nt, eps, 1 - eps))
        + (1 - y_nt) * np.log(np.clip(1 - p_nt, eps, 1 - eps))))
    margin_mae = float(np.mean(np.abs(margin - pred["mu_margin"])))
    total_mae = float(np.mean(np.abs(total - pred["mu_total"])))
    return {
        "label": label,
        "feature_set": fit.get("feature_set"),
        "n_games": int(len(rows)),
        "winner_accuracy": round(accuracy, 4),
        "brier": round(brier, 4),
        "log_loss": round(logloss, 4),
        "margin_mae": round(margin_mae, 3),
        "total_mae": round(total_mae, 3),
        "baseline_home_rate": round(float(np.mean(y_nt.astype(bool))), 4),
        "baseline_margin_mae": round(float(np.mean(
            np.abs(margin - train_margin_mean))), 3),
        "baseline_total_mae": round(float(np.mean(
            np.abs(total - train_total_mean))), 3),
    }


def render(results: List[dict]) -> str:
    lines = ["NFL forecast-first backtest (price-free)", ""]
    current = None
    for r in results:
        if r["feature_set"] != current:
            current = r["feature_set"]
            lines.append(f"=== feature_set={current} ===")
        lines.append(f"[{r['label']}] n={r['n_games']}")
        lines.append(
            f"  winner  acc={r['winner_accuracy']:.4f} "
            f"(home baseline {r['baseline_home_rate']:.4f}) | "
            f"brier={r['brier']:.4f} | log_loss={r['log_loss']:.4f}")
        lines.append(
            f"  margin MAE={r['margin_mae']:.3f} "
            f"(mean baseline {r['baseline_margin_mae']:.3f}) | "
            f"total MAE={r['total_mae']:.3f} "
            f"(mean baseline {r['baseline_total_mae']:.3f})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="forecast-first backtest")
    ap.add_argument("--tune", type=int, nargs="*", default=list(TUNE_SEASONS))
    ap.add_argument("--holdout", type=int, nargs="*",
                    default=list(HOLDOUT_SEASONS))
    ap.add_argument("--feature-set", default="all",
                    choices=("base", "full", "all"))
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    games, team_games, situational, qb_games = load_processed_v2()
    features, _ = replay_v2(games, team_games, situational, qb_games)
    rivalry = compute_rivalry_frame(games)
    weather = load_weather()
    paired = paired_features(features, games, rivalry=rivalry,
                             weather=weather)

    tune = [s for s in args.tune if s in set(paired["season"])]
    holdout = [s for s in args.holdout if s in set(paired["season"])]
    if not tune or not holdout:
        print(f"insufficient seasons: tune={tune} holdout={holdout}")
        return 0

    sets = (["base", "full"] if args.feature_set == "all"
            else [args.feature_set])
    train_rows = paired[paired["season"].isin(tune)]
    train_margin_mean = float(train_rows["margin"].mean())
    train_total_mean = float(train_rows["total"].mean())
    holdout_rows = paired[paired["season"].isin(holdout)]

    results = []
    for feature_set in sets:
        fit = fit_forecast_models(paired, tune, feature_set=feature_set)
        results.append(evaluate(fit, holdout_rows, "holdout",
                                train_margin_mean, train_total_mean))
        results.append(evaluate(fit, train_rows, "tune",
                                train_margin_mean, train_total_mean))

    text = render(results)
    print(text)
    if not args.no_export:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        FORECAST_BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
        (FORECAST_BACKTESTS_DIR / f"backtest_forecast_{stamp}.txt").write_text(
            text, encoding="utf-8")
        (FORECAST_BACKTESTS_DIR / f"backtest_forecast_{stamp}.json").write_text(
            json.dumps({"results": results, "tune": tune,
                        "holdout": holdout}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
