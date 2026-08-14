"""Opponent-adjusted EPA team ratings with point-in-time replay.

Replays completed games chronologically, maintaining per-team EWMA ratings of
offensive / defensive EPA per play (centered on a running league mean) plus
pace (offensive plays per game). Every feature recorded for a game uses only
games strictly before it, so the same replay feeds both the backtest and the
live state without leakage.

Points projection: OLS
    points = b0 + b_off*off_rating + b_def*opp_def_rating
             + b_pace*pace_avg_centered + b_home*home

Probabilities come from empirical residual distributions (integer-rounded
outcomes), so pushes and heavy score masses carry real probability instead of
a plain normal tail.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..config import CURRENT_SEASON, FIRST_SEASON, PROCESSED_DIR
from ..version import MODEL_VERSION

LIVE_STATE_PATH = PROCESSED_DIR / "live_state.json"

ALPHA_RATING = 0.12       # EWMA weight per game for off/def ratings
ALPHA_PACE = 0.10         # EWMA weight per game for plays
ALPHA_LEAGUE = 0.005      # slow drift of the league-mean baselines
SEASON_CARRYOVER = 0.60   # regress ratings toward league mean at rollover
MIN_GAMES_FOR_FIT = 10    # all-time games before a row enters fit/residuals


def _blank_team() -> dict:
    return {"off": 0.0, "def": 0.0, "pace": None, "games_total": 0,
            "season": None, "games_season": 0}


def replay(games: pd.DataFrame, team_games: pd.DataFrame
           ) -> Tuple[pd.DataFrame, dict]:
    """Return (per-team-game point-in-time features, final replay state)."""
    tg = {(r.game_id, r.posteam): (r.off_epa_pp, r.off_plays)
          for r in team_games.itertuples()}

    completed = games[games["home_score"].notna()
                      & games["away_score"].notna()
                      & games["home_team"].notna()
                      & games["away_team"].notna()].copy()
    completed = completed.sort_values(["gameday", "game_id"])

    teams: Dict[str, dict] = {}
    league = {"epa_pp": 0.0, "pace": 63.0}
    rows: List[dict] = []

    for g in completed.itertuples():
        home, away = g.home_team, g.away_team
        key_h, key_a = (g.game_id, home), (g.game_id, away)
        if key_h not in tg or key_a not in tg:
            continue  # completed game without pbp (coverage gap; diagnosed at build)
        th = teams.setdefault(home, _blank_team())
        ta = teams.setdefault(away, _blank_team())

        for t in (th, ta):
            if t["season"] != g.season:
                if t["season"] is not None:
                    t["off"] *= SEASON_CARRYOVER
                    t["def"] *= SEASON_CARRYOVER
                t["season"] = g.season
                t["games_season"] = 0

        pace_h = th["pace"] if th["pace"] is not None else league["pace"]
        pace_a = ta["pace"] if ta["pace"] is not None else league["pace"]
        pace_avg_c = (pace_h + pace_a) / 2.0 - league["pace"]

        for team, opp, t_state, o_state, pts, is_home in (
                (home, away, th, ta, g.home_score, 1),
                (away, home, ta, th, g.away_score, 0)):
            rows.append({
                "game_id": g.game_id, "season": g.season, "week": g.week,
                "gameday": g.gameday, "game_type": g.game_type,
                "team": team, "opp": opp, "home": is_home,
                "points": float(pts),
                "off_rating": t_state["off"],
                "opp_def_rating": o_state["def"],
                "pace_avg_c": pace_avg_c,
                "games_total": t_state["games_total"],
                "opp_games_total": o_state["games_total"],
                "games_season": t_state["games_season"],
            })

        # --- update state with this game's observations (post-snapshot) ---
        epa_h, plays_h = tg[key_h]
        epa_a, plays_a = tg[key_a]
        off_h_pre, off_a_pre = th["off"], ta["off"]
        def_h_pre, def_a_pre = th["def"], ta["def"]
        lm = league["epa_pp"]

        adj_off_h = (epa_h - lm) - def_a_pre
        adj_off_a = (epa_a - lm) - def_h_pre
        adj_def_h = (epa_a - lm) - off_a_pre
        adj_def_a = (epa_h - lm) - off_h_pre

        th["off"] += ALPHA_RATING * (adj_off_h - th["off"])
        ta["off"] += ALPHA_RATING * (adj_off_a - ta["off"])
        th["def"] += ALPHA_RATING * (adj_def_h - th["def"])
        ta["def"] += ALPHA_RATING * (adj_def_a - ta["def"])

        for t, plays in ((th, plays_h), (ta, plays_a)):
            if t["pace"] is None:
                t["pace"] = float(plays)
            else:
                t["pace"] += ALPHA_PACE * (plays - t["pace"])
            t["games_total"] += 1
            t["games_season"] += 1

        league["epa_pp"] += ALPHA_LEAGUE * (((epa_h + epa_a) / 2.0) - league["epa_pp"])
        league["pace"] += ALPHA_LEAGUE * (((plays_h + plays_a) / 2.0) - league["pace"])

    features = pd.DataFrame(rows)
    state = {"teams": teams, "league": league}
    return features, state


def _design(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([
        np.ones(len(df)),
        df["off_rating"].to_numpy(),
        df["opp_def_rating"].to_numpy(),
        df["pace_avg_c"].to_numpy(),
        df["home"].to_numpy(dtype=float),
    ])


def fit_rows(features: pd.DataFrame, seasons: Sequence[int]) -> pd.DataFrame:
    """Rows eligible for fitting/residuals: warm ratings, chosen seasons."""
    mask = (features["season"].isin(list(seasons))
            & (features["games_total"] >= MIN_GAMES_FOR_FIT)
            & (features["opp_games_total"] >= MIN_GAMES_FOR_FIT))
    return features[mask]


def fit_projection(features: pd.DataFrame, seasons: Sequence[int]) -> dict:
    """OLS points projection + empirical residual distributions."""
    rows = fit_rows(features, seasons)
    if len(rows) < 500:
        raise RuntimeError(f"too few fit rows ({len(rows)}); check build/replay")
    X, y = _design(rows), rows["points"].to_numpy()
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    mu = X @ coefs
    resid_points = y - mu

    with_mu = rows[["game_id", "home", "points"]].copy()
    with_mu["mu"] = mu
    h = with_mu[with_mu["home"] == 1].set_index("game_id")
    a = with_mu[with_mu["home"] == 0].set_index("game_id")
    both = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")
    resid_margin = ((both["points_h"] - both["points_a"])
                    - (both["mu_h"] - both["mu_a"])).to_numpy()
    resid_total = ((both["points_h"] + both["points_a"])
                   - (both["mu_h"] + both["mu_a"])).to_numpy()

    return {
        "coefs": [float(c) for c in coefs],
        "fit_seasons": sorted(int(s) for s in set(seasons)),
        "n_fit_rows": int(len(rows)),
        "resid_points": [round(float(r), 2) for r in resid_points],
        "resid_margin": [round(float(r), 2) for r in resid_margin],
        "resid_total": [round(float(r), 2) for r in resid_total],
    }


def _best_gamma(p_raw: np.ndarray, y: np.ndarray) -> float:
    eps = 1e-9
    best_g, best_ll = 1.0, np.inf
    for g in np.linspace(0.1, 1.3, 49):
        p = np.clip(0.5 + g * (p_raw - 0.5), eps, 1 - eps)
        ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
        if ll < best_ll:
            best_ll, best_g = ll, float(g)
    return round(best_g, 4)


def fit_prob_shrinks(features: pd.DataFrame, games: pd.DataFrame,
                     fit: dict, seasons: Sequence[int]) -> Dict[str, float]:
    """Per-market shrink of win probabilities toward 0.5, tune seasons only.

    Playbook lesson (tennis: 'overconfident Elo'): rating noise and missing
    injury/QB information make raw disagreement probabilities too extreme,
    and the miscalibration differs by market. Each gamma is fit on
    tune-season outcomes at the real closing lines (team totals use the
    derived implied line, matching how they are backtested).
    """
    coefs = np.array(fit["coefs"])
    rp = np.array(fit["resid_points"])
    rm = np.array(fit["resid_margin"])
    rt = np.array(fit["resid_total"])

    rows = fit_rows(features, seasons).copy()
    rows["mu"] = _design(rows) @ coefs
    h = rows[rows["home"] == 1].set_index("game_id")
    a = rows[rows["home"] == 0].set_index("game_id")
    both = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")
    gm = games.set_index("game_id")
    both = both.join(gm[["spread_line", "total_line"]], how="left")

    margin = (both["points_h"] - both["points_a"]).to_numpy()
    mu_margin = (both["mu_h"] - both["mu_a"]).to_numpy()

    # moneyline
    mask = margin != 0
    p_raw = np.array([moneyline_probs(m, rm)["p_home_win"]
                      + 0.5 * moneyline_probs(m, rm)["p_tie"]
                      for m in mu_margin[mask]])
    gammas = {"moneyline": _best_gamma(p_raw, (margin[mask] > 0).astype(float))}

    # spread at real closing line
    sl = both["spread_line"].to_numpy(dtype=float)
    mask = ~np.isnan(sl) & (margin != sl)
    p_raw = np.array([spread_probs(m, rm, home_spread=-l)["p_home_cover"]
                      for m, l in zip(mu_margin[mask], sl[mask])])
    gammas["spread"] = _best_gamma(p_raw, (margin[mask] > sl[mask]).astype(float))

    # game total at real closing line
    total = (both["points_h"] + both["points_a"]).to_numpy()
    mu_total = (both["mu_h"] + both["mu_a"]).to_numpy()
    tl = both["total_line"].to_numpy(dtype=float)
    mask = ~np.isnan(tl) & (total != tl)
    p_raw = np.array([game_total_probs(m, rt, l)["p_over"]
                      for m, l in zip(mu_total[mask], tl[mask])])
    gammas["total"] = _best_gamma(p_raw, (total[mask] > tl[mask]).astype(float))

    # team totals at derived implied lines
    p_list, y_list = [], []
    for pts_col, mu_col, sign in (("points_h", "mu_h", 1.0), ("points_a", "mu_a", -1.0)):
        pts = both[pts_col].to_numpy()
        mus = both[mu_col].to_numpy()
        implied = np.round((tl + sign * sl) / 2.0 * 2.0) / 2.0
        mask = ~np.isnan(implied) & (pts != implied)
        p_list.extend(team_total_probs(m, rp, l)["p_over"]
                      for m, l in zip(mus[mask], implied[mask]))
        y_list.extend((pts[mask] > implied[mask]).astype(float).tolist())
    gammas["team_total"] = _best_gamma(np.array(p_list), np.array(y_list))
    return gammas


def shrink_prob(p: float, gamma: float) -> float:
    """Shrink a win probability toward 0.5 by the tuned factor."""
    return 0.5 + gamma * (p - 0.5)


def project_points(coefs: Sequence[float], off_rating: float,
                   opp_def_rating: float, pace_avg_c: float,
                   home: int) -> float:
    b0, b_off, b_def, b_pace, b_home = coefs
    return (b0 + b_off * off_rating + b_def * opp_def_rating
            + b_pace * pace_avg_c + b_home * home)


def _outcome_probs(mu: float, residuals: np.ndarray, line: float,
                   direction: str) -> Tuple[float, float]:
    """(p_win, p_push) for over/under-style comparisons vs an integer-ish line."""
    outcomes = np.rint(mu + residuals)
    if direction == "over":
        p_win = float(np.mean(outcomes > line))
    else:
        p_win = float(np.mean(outcomes < line))
    p_push = float(np.mean(outcomes == line))
    return p_win, p_push


def team_total_probs(mu: float, resid_points: np.ndarray,
                     line: float) -> dict:
    p_over, p_push = _outcome_probs(mu, resid_points, line, "over")
    return {"p_over": p_over, "p_under": max(0.0, 1.0 - p_over - p_push),
            "p_push": p_push}


def game_total_probs(mu_total: float, resid_total: np.ndarray,
                     line: float) -> dict:
    p_over, p_push = _outcome_probs(mu_total, resid_total, line, "over")
    return {"p_over": p_over, "p_under": max(0.0, 1.0 - p_over - p_push),
            "p_push": p_push}


def spread_probs(mu_margin: float, resid_margin: np.ndarray,
                 home_spread: float) -> dict:
    """home_spread is the Bovada handicap on the home side (e.g. -3.5).

    Home covers when margin + home_spread > 0.
    """
    outcomes = np.rint(mu_margin + resid_margin)
    p_home = float(np.mean(outcomes > -home_spread))
    p_push = float(np.mean(outcomes == -home_spread))
    return {"p_home_cover": p_home,
            "p_away_cover": max(0.0, 1.0 - p_home - p_push),
            "p_push": p_push}


def moneyline_probs(mu_margin: float, resid_margin: np.ndarray) -> dict:
    outcomes = np.rint(mu_margin + resid_margin)
    p_home = float(np.mean(outcomes > 0))
    p_tie = float(np.mean(outcomes == 0))
    return {"p_home_win": p_home, "p_away_win": max(0.0, 1.0 - p_home - p_tie),
            "p_tie": p_tie}


def rebuild_state(fit_seasons: Optional[Sequence[int]] = None) -> dict:
    """Replay everything, fit on completed seasons, write live_state.json.

    Production fit uses all completed seasons after the warm-up year (the
    backtest separately validates the methodology on a held-out split).
    """
    from ..sources.nflverse import load_processed

    games, team_games = load_processed()
    features, state = replay(games, team_games)

    completed_seasons = sorted(
        int(s) for s in features["season"].unique()
        if s > FIRST_SEASON and s < CURRENT_SEASON)
    seasons = list(fit_seasons) if fit_seasons else completed_seasons
    fit = fit_projection(features, seasons)
    fit["prob_shrink"] = fit_prob_shrinks(features, games, fit, seasons)

    teams_out = {}
    for team, t in state["teams"].items():
        games_season = t["games_season"] if t["season"] == CURRENT_SEASON else 0
        teams_out[team] = {
            "off": round(t["off"], 5),
            "def": round(t["def"], 5),
            "pace": round(t["pace"], 2) if t["pace"] is not None else None,
            "games_total": t["games_total"],
            "last_season": t["season"],
            "games_current_season": games_season,
        }

    live_state = {
        "model_version": MODEL_VERSION,
        "as_of": str(features["gameday"].max()),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "current_season": CURRENT_SEASON,
        "league": {k: round(v, 5) for k, v in state["league"].items()},
        "teams": teams_out,
        **fit,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_STATE_PATH.write_text(json.dumps(live_state))
    return live_state


def load_state() -> dict:
    if not LIVE_STATE_PATH.exists():
        raise RuntimeError("live_state.json missing; run `cli rebuild-state`")
    return json.loads(LIVE_STATE_PATH.read_text())
