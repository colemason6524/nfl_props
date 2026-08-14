"""v2 candidate model — richer features, SHADOW ONLY.

This module is a parallel, research-only path. It never changes v1
(`ratings/epa.py`) probabilities, tiers, or `live_state.json`. It replays the
same base EPA/pace ratings as v1 (identical update formulas and ordering, so
the "base" feature set is numerically equal to v1) and adds:

- situational pass/rush EPA ratings (opponent-adjusted EWMAs),
- schedule context (rest, neutral site, dome),
- a point-in-time quarterback feature built from a lag-based starter proxy
  (the highest-volume passer of the team's immediately prior game).

Starter resolution is intentionally leak-free: it uses only completed prior
games. For the first game of a season (or any team with no prior game in the
replay) the QB feature is marked `qb_uncertain` and contributes zero. Live,
this means the QB family stays neutral until a real starter source exists.

Everything is fitted and written to `live_state_v2_shadow.json` and compared
to v1 via `backtest_v2.py`; nothing here reaches Core/Lean/Watch.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..config import CURRENT_SEASON, FIRST_SEASON, PROCESSED_DIR
from .epa import (ALPHA_LEAGUE, ALPHA_PACE, ALPHA_RATING, SEASON_CARRYOVER,
                  fit_rows)

LIVE_STATE_V2_PATH = PROCESSED_DIR / "live_state_v2_shadow.json"
MODEL_VERSION_V2 = "nfl-epa-points-v2-shadow"

ALPHA_SPLIT = 0.12       # EWMA weight for pass/rush situational ratings
QB_SHRINK = 50.0         # dropbacks to halve the shrink weight on the QB delta
LEAGUE_PASS_PRIOR = 0.05
LEAGUE_RUSH_PRIOR = -0.10
REST_FILL = 7.0          # neutral rest assumption for any missing rest value

BASE_FEATURES = ["off_rating", "opp_def_rating", "pace_avg_c", "home"]
SCHED_FEATURES = ["rest_days", "opp_rest_days", "neutral_site", "dome"]
SPLIT_FEATURES = ["off_pass", "opp_def_pass", "off_rush", "opp_def_rush"]
QB_FEATURES = ["qb_epa", "qb_cpoe"]

# `qb_uncertain` is exported as a context flag, not a coefficient: it is
# identically zero across all fit rows (the lag starter proxy always resolves a
# prior-game passer once a team has MIN_GAMES_FOR_FIT games), so it would make
# the design matrix rank-deficient.
FULL_FEATURES = (BASE_FEATURES + SCHED_FEATURES + SPLIT_FEATURES
                 + QB_FEATURES)

FEATURE_SETS = {
    "base": BASE_FEATURES,
    "sched": BASE_FEATURES + SCHED_FEATURES,
    "split": BASE_FEATURES + SPLIT_FEATURES,
    "qb": BASE_FEATURES + QB_FEATURES,
    "full": FULL_FEATURES,
}


def _blank_team() -> dict:
    return {
        "off": 0.0, "def": 0.0, "pace": None,
        "off_pass": 0.0, "def_pass": 0.0,
        "off_rush": 0.0, "def_rush": 0.0,
        "games_total": 0, "season": None, "games_season": 0,
        "last_qb_id": None,
    }


def _blank_qb() -> dict:
    return {"epa_c": 0.0, "cpoe": 0.0, "sack_rate": 0.06,
            "dropbacks": 0, "games": 0, "last_team": None}


def replay_v2(games: pd.DataFrame, team_games: pd.DataFrame,
              situational: pd.DataFrame, qb_games: pd.DataFrame
              ) -> Tuple[pd.DataFrame, dict]:
    """Point-in-time v2 features + final state.

    Base off/def/pace ratings update identically to `ratings/epa.py::replay`,
    so the base columns of this output equal v1 for identical inputs.
    """
    tg = {(r.game_id, r.posteam): (r.off_epa_pp, r.off_plays)
          for r in team_games.itertuples()}
    sg = {(r.game_id, r.posteam): r for r in situational.itertuples()}

    qb_by_game: Dict[tuple, List] = {}
    for r in qb_games.itertuples():
        qb_by_game.setdefault((r.game_id, r.posteam), []).append(r)

    completed = games[games["home_score"].notna()
                      & games["away_score"].notna()
                      & games["home_team"].notna()
                      & games["away_team"].notna()].copy()
    completed = completed.sort_values(["gameday", "game_id"])

    teams: Dict[str, dict] = {}
    qbs: Dict[str, dict] = {}
    league = {"epa_pp": 0.0, "pace": 63.0,
              "pass_epa": LEAGUE_PASS_PRIOR, "rush_epa": LEAGUE_RUSH_PRIOR}
    rows: List[dict] = []

    def _is_dome(r) -> float:
        return 1.0 if str(getattr(r, "roof", "") or "").lower() in (
            "closed", "dome") else 0.0

    def _is_neutral(r) -> float:
        return 1.0 if str(getattr(r, "location", "") or "") == "Neutral" else 0.0

    def rest(v) -> float:
        try:
            x = float(v)
            return x if not np.isnan(x) else REST_FILL
        except (TypeError, ValueError):
            return REST_FILL

    for g in completed.itertuples():
        home, away = g.home_team, g.away_team
        key_h, key_a = (g.game_id, home), (g.game_id, away)
        if key_h not in tg or key_a not in tg:
            continue
        if key_h not in sg or key_a not in sg:
            continue  # pbp-derived situational missing (diagnosed at build)

        th = teams.setdefault(home, _blank_team())
        ta = teams.setdefault(away, _blank_team())

        for t in (th, ta):
            if t["season"] != g.season:
                if t["season"] is not None:
                    t["off"] *= SEASON_CARRYOVER
                    t["def"] *= SEASON_CARRYOVER
                    t["off_pass"] *= SEASON_CARRYOVER
                    t["def_pass"] *= SEASON_CARRYOVER
                    t["off_rush"] *= SEASON_CARRYOVER
                    t["def_rush"] *= SEASON_CARRYOVER
                t["season"] = g.season
                t["games_season"] = 0

        pace_h = th["pace"] if th["pace"] is not None else league["pace"]
        pace_a = ta["pace"] if ta["pace"] is not None else league["pace"]
        pace_avg_c = (pace_h + pace_a) / 2.0 - league["pace"]

        dome = _is_dome(g)
        neutral = _is_neutral(g)
        home_rest = rest(getattr(g, "home_rest", None))
        away_rest = rest(getattr(g, "away_rest", None))

        for team, opp, t_state, o_state, pts, is_home in (
                (home, away, th, ta, g.home_score, 1),
                (away, home, ta, th, g.away_score, 0)):
            rest_days = home_rest if is_home else away_rest
            opp_rest_days = away_rest if is_home else home_rest

            qb_epa, qb_cpoe, qb_uncertain = _qb_features(
                t_state, qbs, team)

            rows.append({
                "game_id": g.game_id, "season": g.season, "week": g.week,
                "gameday": g.gameday, "game_type": g.game_type,
                "team": team, "opp": opp, "home": is_home,
                "points": float(pts),
                # base (identical to v1)
                "off_rating": t_state["off"],
                "opp_def_rating": o_state["def"],
                "pace_avg_c": pace_avg_c,
                "games_total": t_state["games_total"],
                "opp_games_total": o_state["games_total"],
                "games_season": t_state["games_season"],
                # schedule
                "rest_days": rest_days,
                "opp_rest_days": opp_rest_days,
                "neutral_site": neutral,
                "dome": dome,
                # situational splits
                "off_pass": t_state["off_pass"],
                "opp_def_pass": o_state["def_pass"],
                "off_rush": t_state["off_rush"],
                "opp_def_rush": o_state["def_rush"],
                # qb
                "qb_epa": qb_epa,
                "qb_cpoe": qb_cpoe,
                "qb_uncertain": qb_uncertain,
            })

        # --- base state update (identical to v1) ---
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

        # --- situational split updates (pass/rush, opponent-adjusted) ---
        sh, sa = sg[key_h], sg[key_a]
        lpass, lrush = league["pass_epa"], league["rush_epa"]
        for t_state, o_state, s in ((th, ta, sh), (ta, th, sa)):
            pass_obs = s.pass_epa_pp
            rush_obs = s.rush_epa_pp
            if np.isnan(pass_obs):
                pass_obs = lpass
            if np.isnan(rush_obs):
                rush_obs = lrush
            adj_off_pass = (pass_obs - lpass) - o_state["def_pass"]
            adj_def_pass = (pass_obs - lpass) - o_state["off_pass"]
            adj_off_rush = (rush_obs - lrush) - o_state["def_rush"]
            adj_def_rush = (rush_obs - lrush) - o_state["off_rush"]
            t_state["off_pass"] += ALPHA_SPLIT * (adj_off_pass - t_state["off_pass"])
            t_state["def_pass"] += ALPHA_SPLIT * (adj_def_pass - t_state["def_pass"])
            t_state["off_rush"] += ALPHA_SPLIT * (adj_off_rush - t_state["off_rush"])
            t_state["def_rush"] += ALPHA_SPLIT * (adj_def_rush - t_state["def_rush"])

        # --- league split means ---
        obs_pass_h = sh.pass_epa_pp if not np.isnan(sh.pass_epa_pp) else lpass
        obs_pass_a = sa.pass_epa_pp if not np.isnan(sa.pass_epa_pp) else lpass
        obs_rush_h = sh.rush_epa_pp if not np.isnan(sh.rush_epa_pp) else lrush
        obs_rush_a = sa.rush_epa_pp if not np.isnan(sa.rush_epa_pp) else lrush
        league["pass_epa"] += ALPHA_LEAGUE * (((obs_pass_h + obs_pass_a) / 2.0) - lpass)
        league["rush_epa"] += ALPHA_LEAGUE * (((obs_rush_h + obs_rush_a) / 2.0) - lrush)

        # --- QB state update (post-snapshot) ---
        for team, t_state, key in ((home, th, key_h), (away, ta, key_a)):
            primary_id = None
            for q in qb_by_game.get(key, []):
                if q.qb_id is None:
                    continue
                qs = qbs.setdefault(q.qb_id, _blank_qb())
                epa_c_obs = float(q.epa_per_db) - league["pass_epa"]
                if qs["games"] == 0:
                    qs["epa_c"] = epa_c_obs
                else:
                    qs["epa_c"] += ALPHA_SPLIT * (epa_c_obs - qs["epa_c"])
                if not np.isnan(q.cpoe):
                    qs["cpoe"] += ALPHA_SPLIT * (float(q.cpoe) - qs["cpoe"])
                qs["sack_rate"] += ALPHA_SPLIT * (float(q.sack_rate) - qs["sack_rate"])
                qs["dropbacks"] += int(q.dropbacks)
                qs["games"] += 1
                qs["last_team"] = team
                if q.is_primary:
                    primary_id = q.qb_id
            if primary_id is not None:
                t_state["last_qb_id"] = primary_id

    features = pd.DataFrame(rows)
    state = {"teams": teams, "qbs": qbs, "league": league}
    return features, state


def _qb_features(t_state: dict, qbs: Dict[str, dict],
                 team: str) -> Tuple[float, float, float]:
    """(qb_epa, qb_cpoe, qb_uncertain) for a team's expected starter.

    Delta = starter's centered EPA/dropback minus his team's centered pass
    rating, shrunk toward zero by dropback sample. Unknown/absent starter ->
    (0.0, 0.0, 1.0), which contributes nothing to the projection.
    """
    qb_id = t_state.get("last_qb_id")
    qb = qbs.get(qb_id) if qb_id else None
    if qb is None or qb["dropbacks"] <= 0:
        return 0.0, 0.0, 1.0
    w = qb["dropbacks"] / (qb["dropbacks"] + QB_SHRINK)
    delta = qb["epa_c"] - t_state["off_pass"]
    return w * delta, qb["cpoe"], 0.0


def design(df: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    cols = [np.ones(len(df))]
    for f in features:
        cols.append(df[f].to_numpy(dtype=float))
    return np.column_stack(cols)


def fit_v2(features: pd.DataFrame, seasons: Sequence[int],
           feature_names: Sequence[str]) -> dict:
    """Named-feature OLS + empirical residuals for one feature set."""
    rows = fit_rows(features, seasons)
    if len(rows) < 500:
        raise RuntimeError(f"too few fit rows ({len(rows)}); check build/replay")
    X = design(rows, feature_names)
    y = rows["points"].to_numpy()
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    mu = np.dot(X, coefs)
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

    coef_dict = {"intercept": float(coefs[0])}
    coef_dict.update({f: float(c) for f, c in zip(feature_names, coefs[1:])})

    return {
        "feature_names": list(feature_names),
        "coefs": coef_dict,
        "fit_seasons": sorted(int(s) for s in set(seasons)),
        "n_fit_rows": int(len(rows)),
        "resid_points": [round(float(r), 2) for r in resid_points],
        "resid_margin": [round(float(r), 2) for r in resid_margin],
        "resid_total": [round(float(r), 2) for r in resid_total],
    }


def project_points_v2(coefs: Dict[str, float],
                      feat: Dict[str, float]) -> float:
    return coefs["intercept"] + sum(
        coefs[f] * feat[f] for f in coefs if f != "intercept")


def _feat_for(state_v2: dict, team: str, opp: str, is_home: int,
              rest_days: float, opp_rest_days: float, neutral: float,
              dome: float) -> Dict[str, float]:
    teams = state_v2["teams"]
    qbs = state_v2["qbs"]
    league = state_v2["league"]
    t, o = teams[team], teams[opp]
    pace_t = t["pace"] if t.get("pace") is not None else league["pace"]
    pace_o = o["pace"] if o.get("pace") is not None else league["pace"]
    pace_avg_c = (pace_t + pace_o) / 2.0 - league["pace"]
    qb_epa, qb_cpoe, qb_uncertain = _qb_features(t, qbs, team)
    return {
        "off_rating": t["off"],
        "opp_def_rating": o["def"],
        "pace_avg_c": pace_avg_c,
        "home": float(is_home),
        "rest_days": rest_days,
        "opp_rest_days": opp_rest_days,
        "neutral_site": neutral,
        "dome": dome,
        "off_pass": t["off_pass"],
        "opp_def_pass": o["def_pass"],
        "off_rush": t["off_rush"],
        "opp_def_rush": o["def_rush"],
        "qb_epa": qb_epa,
        "qb_cpoe": qb_cpoe,
        "qb_uncertain": qb_uncertain,
    }


def project_games_v2(state_v2: dict, games_live: List, schedule: pd.DataFrame
                     ) -> List[dict]:
    """v2 shadow point projections for live Bovada games.

    `games_live` entries need `.away`/`.home` (canonical abbrs). Schedule
    context (rest / neutral / dome) is joined from the canonical games table
    by (away, home) for the current season; missing schedule rows fall back to
    a rest/neutral/dome of 7/0/0 and are flagged `schedule_missing`. This is
    research-only output — it never changes v1 selection or tiers.
    """
    teams = state_v2["teams"]
    coefs = state_v2["coefs"]
    season = state_v2.get("current_season")

    sched = {}
    for r in schedule.itertuples():
        if r.season == season:
            sched[(r.away_team, r.home_team)] = r

    out = []
    for game in games_live:
        th, ta = teams.get(game.home), teams.get(game.away)
        row = sched.get((game.away, game.home))
        if th is None or ta is None:
            out.append({"away": game.away, "home": game.home,
                        "mu_home_v2": None, "mu_away_v2": None,
                        "unmatched": True})
            continue

        if row is not None:
            def rest(v):
                try:
                    x = float(v)
                    return x if not np.isnan(x) else REST_FILL
                except (TypeError, ValueError):
                    return REST_FILL
            home_rest = rest(getattr(row, "home_rest", None))
            away_rest = rest(getattr(row, "away_rest", None))
            neutral = _is_neutral_schedule(getattr(row, "location", ""))
            dome = _is_dome_schedule(getattr(row, "roof", ""))
            missing = False
        else:
            home_rest = away_rest = REST_FILL
            neutral = dome = 0.0
            missing = True

        fh = _feat_for(state_v2, game.home, game.away, 1,
                       home_rest, away_rest, neutral, dome)
        fa = _feat_for(state_v2, game.away, game.home, 0,
                       away_rest, home_rest, neutral, dome)
        out.append({
            "away": game.away, "home": game.home,
            "mu_home_v2": round(project_points_v2(coefs, fh), 2),
            "mu_away_v2": round(project_points_v2(coefs, fa), 2),
            "schedule_missing": missing,
            "qb_home": th.get("last_qb_id"),
            "qb_away": ta.get("last_qb_id"),
            "qb_uncertain_home": fh["qb_uncertain"],
            "qb_uncertain_away": fa["qb_uncertain"],
        })
    return out


def _is_neutral_schedule(location) -> float:
    return 1.0 if str(location or "") == "Neutral" else 0.0


def _is_dome_schedule(roof) -> float:
    return 1.0 if str(roof or "").lower() in ("closed", "dome") else 0.0


def rebuild_state_v2(fit_seasons: Optional[Sequence[int]] = None,
                     feature_names: Sequence[str] = FULL_FEATURES) -> dict:
    """Replay v2, fit the shadow model, write live_state_v2_shadow.json."""
    from ..sources.nflverse import load_processed_v2

    games, team_games, situational, qb_games = load_processed_v2()
    features, state = replay_v2(games, team_games, situational, qb_games)

    completed_seasons = sorted(
        int(s) for s in features["season"].unique()
        if s > FIRST_SEASON and s < CURRENT_SEASON)
    seasons = list(fit_seasons) if fit_seasons else completed_seasons
    fit = fit_v2(features, seasons, list(feature_names))

    teams_out = {}
    for team, t in state["teams"].items():
        games_season = t["games_season"] if t["season"] == CURRENT_SEASON else 0
        teams_out[team] = {
            "off": round(t["off"], 5),
            "def": round(t["def"], 5),
            "pace": round(t["pace"], 2) if t["pace"] is not None else None,
            "off_pass": round(t["off_pass"], 5),
            "def_pass": round(t["def_pass"], 5),
            "off_rush": round(t["off_rush"], 5),
            "def_rush": round(t["def_rush"], 5),
            "games_total": t["games_total"],
            "last_season": t["season"],
            "games_current_season": games_season,
            "last_qb_id": t.get("last_qb_id"),
        }

    qbs_out = {qid: {k: (round(v, 5) if isinstance(v, float) else v)
                     for k, v in q.items()}
               for qid, q in state["qbs"].items()}

    shadow = {
        "model_version": MODEL_VERSION_V2,
        "as_of": str(features["gameday"].max()),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "current_season": CURRENT_SEASON,
        "league": {k: round(v, 5) for k, v in state["league"].items()},
        "teams": teams_out,
        "qbs": qbs_out,
        **fit,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_STATE_V2_PATH.write_text(json.dumps(shadow))
    return shadow


def load_state_v2() -> dict:
    if not LIVE_STATE_V2_PATH.exists():
        raise RuntimeError("live_state_v2_shadow.json missing; "
                           "run `cli rebuild-state-v2`")
    return json.loads(LIVE_STATE_V2_PATH.read_text())
