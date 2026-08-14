"""Screen live Bovada games against the model state -> tiered candidates.

Market is never inside the fair probability: the model prices every market
from ratings alone, then the value layer compares against de-vigged Bovada.
Game totals ride along as a bonus market (same distributions, zero extra
fetch cost; the backtest's most promising EV band).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .pricing import devig_proportional, expected_value
from .ratings.epa import (game_total_probs, moneyline_probs, project_points,
                          shrink_prob, spread_probs, team_total_probs)
from .sources.bovada import LiveGame, TwoWayPrice
from .tiers import MIN_TEAM_GAMES, Candidate, assign_tier


def _mu_for_game(state: dict, home: str, away: str) -> Optional[Tuple[float, float]]:
    teams = state["teams"]
    if home not in teams or away not in teams:
        return None
    th, ta = teams[home], teams[away]
    league_pace = state["league"]["pace"]
    pace_h = th["pace"] if th["pace"] is not None else league_pace
    pace_a = ta["pace"] if ta["pace"] is not None else league_pace
    pace_avg_c = (pace_h + pace_a) / 2.0 - league_pace
    coefs = state["coefs"]
    mu_h = project_points(coefs, th["off"], ta["def"], pace_avg_c, home=1)
    mu_a = project_points(coefs, ta["off"], th["def"], pace_avg_c, home=0)
    return mu_h, mu_a


def _candidate(game: LiveGame, market: str, side: str, line: Optional[float],
               american: int, decimal: float, p_model: float, p_cal: float,
               p_market: float, p_push: float, mu_h: float, mu_a: float,
               games_min: int, flags: List[str]) -> Candidate:
    c = Candidate(
        event_id=game.event_id, away=game.away, home=game.home,
        market=market, side=side, line=line, american=american,
        decimal=decimal,
        p_model=round(p_model, 4), p_model_cal=round(p_cal, 4),
        p_market=round(p_market, 4), p_push=round(p_push, 4),
        ev=round(expected_value(p_model, decimal, p_push), 4),
        edge=round(p_model - p_market, 4),
        mu_home=round(mu_h, 2), mu_away=round(mu_a, 2),
        games_current_min=games_min,
        start_time_utc=game.start_time_utc,
        flags=list(flags),
    )
    c.tier = assign_tier(c)
    return c


def screen_games(games: List[LiveGame], state: dict
                 ) -> Tuple[List[Candidate], dict]:
    rp = np.array(state["resid_points"])
    rm = np.array(state["resid_margin"])
    rt = np.array(state["resid_total"])
    gammas = state.get("prob_shrink", {})
    candidates: List[Candidate] = []
    projections: List[dict] = []
    unmatched: List[str] = []

    for game in games:
        mus = _mu_for_game(state, game.home, game.away)
        if mus is None:
            unmatched.append(f"{game.away} @ {game.home}")
            continue
        mu_h, mu_a = mus
        teams = state["teams"]
        games_min = min(teams[game.home]["games_current_season"],
                        teams[game.away]["games_current_season"])
        flags: List[str] = []
        if games_min < MIN_TEAM_GAMES:
            flags.append("LOW_SAMPLE")
        projections.append({
            "away": game.away, "home": game.home,
            "mu_away": round(mu_a, 1), "mu_home": round(mu_h, 1),
            "margin": round(mu_h - mu_a, 1), "total": round(mu_h + mu_a, 1),
            "start_time_utc": game.start_time_utc,
        })

        def cal(p: float, market: str) -> float:
            return shrink_prob(p, gammas.get(market, 1.0))

        if game.moneyline:
            ml = game.moneyline
            probs = moneyline_probs(mu_h - mu_a, rm)
            k_h, k_a = devig_proportional(ml.dec_a, ml.dec_b)
            for side, p, k, am, dec in (
                    (game.home, probs["p_home_win"], k_h, ml.am_a, ml.dec_a),
                    (game.away, probs["p_away_win"], k_a, ml.am_b, ml.dec_b)):
                candidates.append(_candidate(
                    game, "MONEYLINE", side, None, am, dec, p,
                    cal(p, "moneyline"), k, probs["p_tie"],
                    mu_h, mu_a, games_min, flags))

        if game.spread and game.spread.line is not None:
            sp = game.spread
            probs = spread_probs(mu_h - mu_a, rm, home_spread=sp.line)
            k_h, k_a = devig_proportional(sp.dec_a, sp.dec_b)
            for side, line, p, k, am, dec in (
                    (game.home, sp.line, probs["p_home_cover"], k_h, sp.am_a, sp.dec_a),
                    (game.away, -sp.line, probs["p_away_cover"], k_a, sp.am_b, sp.dec_b)):
                candidates.append(_candidate(
                    game, "SPREAD", side, line, am, dec, p,
                    cal(p, "spread"), k, probs["p_push"],
                    mu_h, mu_a, games_min, flags))

        if game.game_total and game.game_total.line is not None:
            gt = game.game_total
            probs = game_total_probs(mu_h + mu_a, rt, gt.line)
            k_o, k_u = devig_proportional(gt.dec_a, gt.dec_b)
            for side, p, k, am, dec in (
                    ("OVER", probs["p_over"], k_o, gt.am_a, gt.dec_a),
                    ("UNDER", probs["p_under"], k_u, gt.am_b, gt.dec_b)):
                candidates.append(_candidate(
                    game, "GAME_TOTAL", side, gt.line, am, dec, p,
                    cal(p, "total"), k, probs["p_push"],
                    mu_h, mu_a, games_min, flags))

        for team, tt in (game.team_totals or {}).items():
            if tt.line is None:
                continue
            mu_team = mu_h if team == game.home else mu_a
            probs = team_total_probs(mu_team, rp, tt.line)
            k_o, k_u = devig_proportional(tt.dec_a, tt.dec_b)
            for direction, p, k, am, dec in (
                    ("OVER", probs["p_over"], k_o, tt.am_a, tt.dec_a),
                    ("UNDER", probs["p_under"], k_u, tt.am_b, tt.dec_b)):
                candidates.append(_candidate(
                    game, "TEAM_TOTAL", f"{team} {direction}", tt.line,
                    am, dec, p, cal(p, "team_total"), k, probs["p_push"],
                    mu_h, mu_a, games_min, flags))

    candidates.sort(key=lambda c: ({"Core": 0, "Lean": 1, "Watch": 2}[c.tier],
                                   -c.ev))
    summary = {
        "games_screened": len(projections),
        "unmatched_games": unmatched,
        "candidates": len(candidates),
        "core": sum(1 for c in candidates if c.tier == "Core"),
        "lean": sum(1 for c in candidates if c.tier == "Lean"),
        "watch": sum(1 for c in candidates if c.tier == "Watch"),
        "projections": projections,
        "state_as_of": state.get("as_of"),
        "current_season": state.get("current_season"),
    }
    return candidates, summary
