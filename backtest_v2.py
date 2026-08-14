#!/usr/bin/env python3
"""v2 shadow-model ablation backtest vs closing lines.

Fits each feature set on TUNE_SEASONS, evaluates HOLDOUT_SEASONS against
closing spread/total/moneyline, and reports points/margin MAE plus log-loss
and Brier (raw model probability vs de-vigged closing market). This is the
research harness for the v2 candidate — it changes nothing about the v1
production board.
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from nfl_props import config
from nfl_props.config import BACKTESTS_DIR, HOLDOUT_SEASONS, TUNE_SEASONS
from nfl_props.pricing import american_to_decimal, devig_proportional
from nfl_props.ratings.epa import (fit_rows, moneyline_probs, spread_probs,
                                   game_total_probs)
from nfl_props.ratings.v2 import FEATURE_SETS, design, fit_v2, replay_v2
from nfl_props.sources.nflverse import load_processed_v2
from nfl_props.utils import log

EPS = 1e-9


def _dec(american, fallback=-110) -> float:
    try:
        a = float(american)
        if math.isnan(a) or a == 0:
            a = fallback
    except (TypeError, ValueError):
        a = fallback
    return american_to_decimal(a)


def _ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _brier(p, y):
    return float(np.mean((p - y) ** 2))


def _score(features, games, tune, holdout, feats) -> dict:
    fit = fit_v2(features, tune, feats)
    coefs = np.array([fit["coefs"]["intercept"]]
                     + [fit["coefs"][f] for f in feats])
    rp = np.array(fit["resid_points"])
    rm = np.array(fit["resid_margin"])
    rt = np.array(fit["resid_total"])

    rows = fit_rows(features, holdout).copy()
    rows["mu"] = np.dot(design(rows, feats), coefs)
    h = rows[rows["home"] == 1].set_index("game_id")
    a = rows[rows["home"] == 0].set_index("game_id")
    both = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")
    gm = games.set_index("game_id")
    both = both.join(gm[["spread_line", "total_line", "home_moneyline",
                         "away_moneyline", "home_spread_odds",
                         "away_spread_odds", "over_odds", "under_odds"]],
                     how="left")

    mae_pts = float(np.mean(np.abs(both["points_h"] - both["mu_h"]).tolist()
                            + np.abs(both["points_a"] - both["mu_a"]).tolist()))
    margin_err = ((both["points_h"] - both["points_a"])
                  - (both["mu_h"] - both["mu_a"]))
    mae_margin = float(np.abs(margin_err).mean())

    def gather(market):
        p_list, y_list, pk_list = [], [], []
        for g in both.itertuples():
            mu_margin = g.mu_h - g.mu_a
            actual_margin = g.points_h - g.points_a
            actual_total = g.points_h + g.points_a
            if market == "spread":
                if pd.isna(g.spread_line) or actual_margin == g.spread_line:
                    continue
                line = float(g.spread_line)
                pr = spread_probs(mu_margin, rm, home_spread=-line)
                p = pr["p_home_cover"]
                y = 1.0 if actual_margin > line else 0.0
                k_h, _ = devig_proportional(_dec(g.home_spread_odds),
                                            _dec(g.away_spread_odds))
                pk = k_h
            elif market == "total":
                if pd.isna(g.total_line) or actual_total == g.total_line:
                    continue
                line = float(g.total_line)
                pr = game_total_probs(g.mu_h + g.mu_a, rt, line)
                p = pr["p_over"]
                y = 1.0 if actual_total > line else 0.0
                k_o, _ = devig_proportional(_dec(g.over_odds),
                                            _dec(g.under_odds))
                pk = k_o
            elif market == "ml":
                if (pd.isna(g.home_moneyline) or pd.isna(g.away_moneyline)
                        or actual_margin == 0):
                    continue
                pr = moneyline_probs(mu_margin, rm)
                p = pr["p_home_win"] + 0.5 * pr["p_tie"]
                y = 1.0 if actual_margin > 0 else 0.0
                k_h, _ = devig_proportional(_dec(g.home_moneyline, None),
                                            _dec(g.away_moneyline, None))
                pk = k_h
            else:
                continue
            p_list.append(p)
            y_list.append(y)
            pk_list.append(pk)
        p = np.array(p_list)
        y = np.array(y_list)
        pk = np.array(pk_list)
        return {
            "n": len(y),
            "ll_model": _ll(p, y),
            "ll_market": _ll(pk, y),
            "brier_model": _brier(p, y),
            "brier_market": _brier(pk, y),
        }

    return {"mae_pts": mae_pts, "mae_margin": mae_margin,
            "spread": gather("spread"), "total": gather("total"),
            "ml": gather("ml")}


def main() -> int:
    ap = argparse.ArgumentParser(description="nfl_props v2 ablation backtest")
    ap.add_argument("--tune", type=int, nargs="*", default=list(TUNE_SEASONS))
    ap.add_argument("--holdout", type=int, nargs="*", default=list(HOLDOUT_SEASONS))
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    games, team_games, situational, qb_games = load_processed_v2()
    features, _ = replay_v2(games, team_games, situational, qb_games)

    lines = [
        "nfl_props v2 shadow ablation backtest",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        f"tune:    {sorted(args.tune)}",
        f"holdout: {sorted(args.holdout)}",
        "",
        f"{'set':<6} {'ptsMAE':>7} {'mrgMAE':>7} | "
        f"{'sp LL':>6} {'sp mkt':>6} {'tot LL':>6} {'tot mkt':>6} "
        f"{'ml LL':>6} {'ml mkt':>6}",
    ]

    market_ref = None
    for name in ("base", "sched", "split", "qb", "full"):
        feats = FEATURE_SETS[name]
        r = _score(features, games, args.tune, args.holdout, feats)
        if market_ref is None:
            market_ref = r
        lines.append(
            f"{name:<6} {r['mae_pts']:>7.2f} {r['mae_margin']:>7.2f} | "
            f"{r['spread']['ll_model']:>6.4f} {r['spread']['ll_market']:>6.4f} "
            f"{r['total']['ll_model']:>6.4f} {r['total']['ll_market']:>6.4f} "
            f"{r['ml']['ll_model']:>6.4f} {r['ml']['ll_market']:>6.4f}")

    lines += [
        "",
        "Brier (model / market) per set:",
        f"{'set':<6} {'spread':>16} {'total':>16} {'ML':>16}",
    ]
    for name in ("base", "sched", "split", "qb", "full"):
        r = _score(features, games, args.tune, args.holdout,
                   FEATURE_SETS[name])
        lines.append(
            f"{name:<6} "
            f"{r['spread']['brier_model']:>7.4f}/{r['spread']['brier_market']:<7.4f} "
            f"{r['total']['brier_model']:>7.4f}/{r['total']['brier_market']:<7.4f} "
            f"{r['ml']['brier_model']:>7.4f}/{r['ml']['brier_market']:<7.4f}")

    lines += [
        "",
        "Semantics: raw model probabilities (no shrink) vs de-vigged closing",
        "market. A model that merely matches the market is the expected outcome;",
        "lower log-loss/Brier than the market would be the (rare) signal. 2023-",
        "2025 has been inspected before, so treat it as reused retrospective",
        "validation, not a pristine holdout.",
    ]
    report = "\n".join(lines)
    print(report)
    if not args.no_export:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = BACKTESTS_DIR / f"backtest_v2_{stamp}.txt"
        path.write_text(report)
        log(f"[backtest_v2] report -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
