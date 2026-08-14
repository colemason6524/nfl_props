#!/usr/bin/env python3
"""Historical backtest vs nflverse closing lines.

Fits the points projection on TUNE_SEASONS, evaluates HOLDOUT_SEASONS against
closing spread / total / moneyline prices from games.csv, and reports
log-loss / Brier / ROI by EV band per market.

Honesty notes baked in:
- Team-total lines are DERIVED implied lines (total +/- spread)/2 at assumed
  -110 juice, because free archives carry no real team-total prices. That
  section validates the points model, not realized team-total ROI.
- Spread/total/ML use real closing prices (with -110 fallback when a juice
  column is missing).
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
from nfl_props.pricing import american_to_decimal, devig_proportional, expected_value
from nfl_props.ratings.epa import (fit_projection, fit_prob_shrinks, fit_rows,
                                   moneyline_probs, replay, shrink_prob,
                                   spread_probs, team_total_probs,
                                   game_total_probs, _design)
from nfl_props.sources.nflverse import load_processed
from nfl_props.utils import log

EV_BANDS = [(-1.00, 0.00), (0.00, 0.02), (0.02, 0.05), (0.05, 0.08),
            (0.08, 0.15), (0.15, 9.99)]
DEFAULT_JUICE = -110


def _dec(american, fallback=DEFAULT_JUICE) -> float:
    try:
        a = float(american)
        if math.isnan(a) or a == 0:
            a = fallback
    except (TypeError, ValueError):
        a = fallback
    return american_to_decimal(a)


def _round_half(x: float) -> float:
    return round(x * 2.0) / 2.0


class Book:
    """Accumulates graded bet records for one market."""

    def __init__(self, name: str):
        self.name = name
        self.bets: list[dict] = []
        self.probs: list[tuple[float, float, int]] = []  # (p_model, p_market, won)

    def record_prob(self, p_model: float, p_market: float, won: int) -> None:
        self.probs.append((p_model, p_market, won))

    def bet(self, ev: float, edge: float, dec: float, result: str) -> None:
        self.bets.append({"ev": ev, "edge": edge, "dec": dec, "result": result})

    def report(self) -> str:
        lines = [f"\n== {self.name} =="]
        if self.probs:
            pm = np.array([p[0] for p in self.probs])
            pk = np.array([p[1] for p in self.probs])
            y = np.array([p[2] for p in self.probs], dtype=float)
            eps = 1e-9
            ll_m = float(-np.mean(y * np.log(pm + eps) + (1 - y) * np.log(1 - pm + eps)))
            ll_k = float(-np.mean(y * np.log(pk + eps) + (1 - y) * np.log(1 - pk + eps)))
            br_m = float(np.mean((pm - y) ** 2))
            br_k = float(np.mean((pk - y) ** 2))
            lines.append(f"n={len(y)}  log-loss model={ll_m:.4f} market={ll_k:.4f}  "
                         f"Brier model={br_m:.4f} market={br_k:.4f}")
        lines.append(f"{'EV band':>14} {'bets':>6} {'W-L-P':>12} {'ROI':>8}")
        for lo, hi in EV_BANDS:
            rows = [b for b in self.bets if lo <= b["ev"] < hi]
            if not rows:
                continue
            w = sum(1 for b in rows if b["result"] == "win")
            l = sum(1 for b in rows if b["result"] == "loss")
            p = sum(1 for b in rows if b["result"] == "push")
            pnl = sum((b["dec"] - 1.0) if b["result"] == "win"
                      else (-1.0 if b["result"] == "loss" else 0.0) for b in rows)
            risked = w + l
            roi = pnl / risked if risked else 0.0
            lines.append(f"[{lo:+.2f},{hi:+.2f}) {len(rows):>6} "
                         f"{w:>4}-{l}-{p:<4} {roi:>+7.1%}")
        return "\n".join(lines)


def run_backtest(tune, holdout) -> str:
    games, team_games = load_processed()
    features, _ = replay(games, team_games)
    fit = fit_projection(features, tune)
    coefs = np.array(fit["coefs"])
    rp = np.array(fit["resid_points"])
    rm = np.array(fit["resid_margin"])
    rt = np.array(fit["resid_total"])
    gammas = fit_prob_shrinks(features, games, fit, tune)

    def make_sh(market: str):
        g = gammas[market]
        return lambda p: min(max(shrink_prob(p, g), 1e-6), 1.0 - 1e-6)

    sh_ml, sh_sp = make_sh("moneyline"), make_sh("spread")
    sh_gt, sh_tt = make_sh("total"), make_sh("team_total")

    log(f"[backtest] fit on {fit['fit_seasons']} ({fit['n_fit_rows']} rows); "
        f"resid sd points={rp.std():.2f} margin={rm.std():.2f} "
        f"prob_shrink={gammas}")

    rows = fit_rows(features, holdout).copy()
    rows["mu"] = _design(rows) @ coefs
    h = rows[rows["home"] == 1].set_index("game_id")
    a = rows[rows["home"] == 0].set_index("game_id")
    both = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")
    gm = games.set_index("game_id")
    both = both.join(gm[["spread_line", "total_line", "home_moneyline",
                         "away_moneyline", "home_spread_odds",
                         "away_spread_odds", "over_odds", "under_odds"]],
                     how="left")

    mae_pts = float(np.mean(np.abs(both["points_h"] - both["mu_h"])
                            .tolist() + np.abs(both["points_a"] - both["mu_a"]).tolist()))
    margin_err = (both["points_h"] - both["points_a"]) - (both["mu_h"] - both["mu_a"])
    mae_margin = float(np.abs(margin_err).mean())

    ml_book = Book("Moneyline (real closing prices)")
    sp_book = Book("Spread (real closing prices)")
    gt_book = Book("Game total (real closing prices, diagnostic)")
    tt_book = Book("Team totals (DERIVED implied lines @ -110 — see honesty note)")

    for g in both.itertuples():
        mu_h, mu_a = g.mu_h, g.mu_a
        mu_margin = mu_h - mu_a
        actual_margin = g.points_h - g.points_a

        # --- moneyline ---
        if not (pd.isna(g.home_moneyline) or pd.isna(g.away_moneyline)):
            dec_h, dec_a = _dec(g.home_moneyline, None), _dec(g.away_moneyline, None)
            probs = moneyline_probs(mu_margin, rm)
            k_h, k_a = devig_proportional(dec_h, dec_a)
            if actual_margin != 0:
                ml_book.record_prob(sh_ml(probs["p_home_win"]), k_h,
                                    int(actual_margin > 0))
            # selection uses RAW model probabilities (production semantics):
            # the EV cap, not shrinkage, filters toxic disagreement live
            for side, p, k, dec in (("home", probs["p_home_win"], k_h, dec_h),
                                    ("away", probs["p_away_win"], k_a, dec_a)):
                ev = expected_value(p, dec, probs["p_tie"])
                won = (actual_margin > 0) if side == "home" else (actual_margin < 0)
                result = "push" if actual_margin == 0 else ("win" if won else "loss")
                if p - k >= 0.0:
                    ml_book.bet(ev, p - k, dec, result)

        # --- spread: spread_line is home margin, home covers if margin > line ---
        if not pd.isna(g.spread_line):
            line = float(g.spread_line)
            probs = spread_probs(mu_margin, rm, home_spread=-line)
            dec_h, dec_a = _dec(g.home_spread_odds), _dec(g.away_spread_odds)
            k_h, k_a = devig_proportional(dec_h, dec_a)
            covered = actual_margin > line
            push = actual_margin == line
            if not push:
                sp_book.record_prob(sh_sp(probs["p_home_cover"]), k_h, int(covered))
            for side, p, k, dec, won in (
                    ("home", probs["p_home_cover"], k_h, dec_h, covered),
                    ("away", probs["p_away_cover"], k_a, dec_a, not covered)):
                ev = expected_value(p, dec, probs["p_push"])
                result = "push" if push else ("win" if won else "loss")
                if p - k >= 0.0:
                    sp_book.bet(ev, p - k, dec, result)

        # --- game total (diagnostic) ---
        if not pd.isna(g.total_line):
            line = float(g.total_line)
            total_actual = g.points_h + g.points_a
            probs = game_total_probs(mu_h + mu_a, rt, line)
            dec_o, dec_u = _dec(g.over_odds), _dec(g.under_odds)
            k_o, k_u = devig_proportional(dec_o, dec_u)
            push = total_actual == line
            if not push:
                gt_book.record_prob(sh_gt(probs["p_over"]), k_o,
                                    int(total_actual > line))
            for side, p, k, dec, won in (
                    ("over", probs["p_over"], k_o, dec_o, total_actual > line),
                    ("under", probs["p_under"], k_u, dec_u, total_actual < line)):
                ev = expected_value(p, dec, probs["p_push"])
                result = "push" if push else ("win" if won else "loss")
                if p - k >= 0.0:
                    gt_book.bet(ev, p - k, dec, result)

        # --- team totals: DERIVED implied lines ---
        if not (pd.isna(g.spread_line) or pd.isna(g.total_line)):
            imp_h = _round_half((float(g.total_line) + float(g.spread_line)) / 2.0)
            imp_a = _round_half((float(g.total_line) - float(g.spread_line)) / 2.0)
            dec = american_to_decimal(DEFAULT_JUICE)
            for mu, line, actual in ((mu_h, imp_h, g.points_h),
                                     (mu_a, imp_a, g.points_a)):
                probs = team_total_probs(mu, rp, line)
                k_o, k_u = 0.5, 0.5  # derived line: no real market prices
                push = actual == line
                if not push:
                    tt_book.record_prob(sh_tt(probs["p_over"]), k_o, int(actual > line))
                for side, p, won in (("over", probs["p_over"], actual > line),
                                     ("under", probs["p_under"], actual < line)):
                    ev = expected_value(p, dec, probs["p_push"])
                    result = "push" if push else ("win" if won else "loss")
                    if p - 0.5 >= 0.0:
                        tt_book.bet(ev, p - 0.5, dec, result)

    report_lines = [
        "nfl_props backtest",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        f"tune seasons:    {sorted(tune)}",
        f"holdout seasons: {sorted(holdout)}",
        f"holdout games:   {len(both)}",
        f"points MAE:      {mae_pts:.2f}",
        f"margin MAE:      {mae_margin:.2f}",
        f"calibration shrink (tune-fit, per market): {gammas}",
        ml_book.report(), sp_book.report(), gt_book.report(), tt_book.report(),
        "\nSemantics: log-loss/Brier rows use tune-calibrated probabilities",
        "(the honest benchmark); EV-band rows use RAW model probabilities,",
        "matching live-board selection where the EV cap filters toxic",
        "disagreement. Near-zero spread/total shrink factors mean the model",
        "shows no standalone edge vs closing on those markets.",
        "\nHonesty: team-total section uses derived implied lines at assumed",
        "-110 with p_market=0.5; it validates the points distribution, not",
        "realized team-total ROI. NFL closing lines are highly efficient —",
        "parity on log-loss/Brier is the expected good outcome here.",
    ]
    return "\n".join(report_lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="nfl_props historical backtest")
    ap.add_argument("--tune", type=int, nargs="*", default=list(TUNE_SEASONS))
    ap.add_argument("--holdout", type=int, nargs="*", default=list(HOLDOUT_SEASONS))
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    report = run_backtest(args.tune, args.holdout)
    print(report)
    if not args.no_export:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = BACKTESTS_DIR / f"backtest_{stamp}.txt"
        path.write_text(report)
        log(f"[backtest] report -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
