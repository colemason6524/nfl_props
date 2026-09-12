"""Forecast-first family models: winner / margin / total (price-free).

The forecast is the product. Nothing in this module reads a sportsbook line,
spread, total, or moneyline when fitting, projecting, or choosing a pick. The
three heads are separate, each owning its target, and internal disagreement is
preserved (see docs/PRODUCT_CONTRACT.md):

- winner : L2 logistic P(home win); ties excluded from the fit.
- margin : ridge OLS on (home - away) points.
- total  : ridge OLS on (home + away) points.

Features are replayed point-in-time from the nflverse play-by-play store:
opponent-adjusted overall/pass/rush EPA, pace, schedule context (rest, dome,
neutral, division), lagged quarterback quality, point-in-time head-to-head
record, and game-time weather (neutral until the weather store is populated).

Spread/total probabilities come from empirical residuals of the family models
(integer-rounded, so pushes carry real mass), fit on outcomes only.
"""
from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# macOS Accelerate BLAS emits spurious "divide by zero / overflow / invalid
# value encountered in matmul" RuntimeWarnings on well-conditioned finite
# systems. Suppress only that narrow message; results are validated finite.
warnings.filterwarnings("ignore", category=RuntimeWarning,
                        message=".*encountered in matmul.*")

from .config import CURRENT_SEASON, FIRST_SEASON, PROCESSED_DIR
from .ratings.epa import SEASON_CARRYOVER, carryover_view
from .ratings.v2 import replay_v2
from .rivalry import compute_rivalry_frame, live_rivalry_features
from .version import (FORECAST_MODEL_VERSION, MARGIN_MODEL_VERSION,
                      TOTAL_MODEL_VERSION, WINNER_MODEL_VERSION)

FORECAST_STATE_PATH = PROCESSED_DIR / "forecast_state.json"

MIN_GAMES_FOR_FIT = 10
QB_SHRINK = 50.0
REST_FILL = 7.0

_BASE = ("off_h", "off_a", "def_h", "def_a", "pace", "home")
_SCHED = ("rest_diff", "dome", "div_game")
_QB = ("qb_epa_h", "qb_epa_a")
_RIVALRY = ("h2h_win_rate_h", "h2h_n")
_WEATHER = ("temp_f", "wind_mph", "precip_in")
_SPLIT = ("pass_off_h", "pass_off_a", "pass_def_h", "pass_def_a",
          "rush_off_h", "rush_off_a", "rush_def_h", "rush_def_a")

_TOTAL_BASE = ("off_h", "off_a", "def_h", "def_a", "pace")

FEATURE_SETS: Dict[str, Dict[str, tuple]] = {
    "base": {
        "winner": _BASE,
        "margin": _BASE,
        "total": _TOTAL_BASE,
    },
    "full": {
        "winner": _BASE + _SCHED + _QB + _RIVALRY,
        "margin": _BASE + _SCHED + _SPLIT + _QB + _RIVALRY,
        "total": _TOTAL_BASE + _SCHED + _SPLIT + _QB + _WEATHER,
    },
}
DEFAULT_FEATURE_SET = "full"


# ---------------------------------------------------------------------------
# Feature frame (historical, one row per completed game)
# ---------------------------------------------------------------------------

def paired_features(features: pd.DataFrame, games: pd.DataFrame,
                    rivalry: Optional[pd.DataFrame] = None,
                    weather: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """One row per completed game with symmetric home/away features.

    Side identity comes from `team`/`home` in the replay output, which is
    built from the canonical schedule, so neutral-site games pair correctly.
    """
    h = features[features["home"] == 1].set_index("game_id")
    a = features[features["home"] == 0].set_index("game_id")
    both = h.join(a, lsuffix="_h", rsuffix="_a", how="inner")

    out = pd.DataFrame(index=both.index)
    out["season"] = both["season_h"].to_numpy()
    out["week"] = both["week_h"].to_numpy()
    out["gameday"] = both["gameday_h"].to_numpy()
    out["game_type"] = both["game_type_h"].to_numpy()
    out["home_team"] = both["team_h"].to_numpy()
    out["away_team"] = both["team_a"].to_numpy()
    out["off_h"] = both["off_rating_h"].to_numpy(dtype=float)
    out["off_a"] = both["off_rating_a"].to_numpy(dtype=float)
    out["def_h"] = both["opp_def_rating_a"].to_numpy(dtype=float)
    out["def_a"] = both["opp_def_rating_h"].to_numpy(dtype=float)
    out["pace"] = both["pace_avg_c_h"].to_numpy(dtype=float)
    out["neutral"] = both["neutral_site_h"].to_numpy(dtype=float)
    out["home"] = 1.0 - out["neutral"]
    out["rest_diff"] = (both["rest_days_h"].to_numpy(dtype=float)
                        - both["rest_days_a"].to_numpy(dtype=float))
    out["dome"] = both["dome_h"].to_numpy(dtype=float)
    out["pass_off_h"] = both["off_pass_h"].to_numpy(dtype=float)
    out["pass_off_a"] = both["off_pass_a"].to_numpy(dtype=float)
    out["pass_def_h"] = both["opp_def_pass_a"].to_numpy(dtype=float)
    out["pass_def_a"] = both["opp_def_pass_h"].to_numpy(dtype=float)
    out["rush_off_h"] = both["off_rush_h"].to_numpy(dtype=float)
    out["rush_off_a"] = both["off_rush_a"].to_numpy(dtype=float)
    out["rush_def_h"] = both["opp_def_rush_a"].to_numpy(dtype=float)
    out["rush_def_a"] = both["opp_def_rush_h"].to_numpy(dtype=float)
    out["qb_epa_h"] = both["qb_epa_h"].to_numpy(dtype=float)
    out["qb_epa_a"] = both["qb_epa_a"].to_numpy(dtype=float)
    out["points_h"] = both["points_h"].to_numpy(dtype=float)
    out["points_a"] = both["points_a"].to_numpy(dtype=float)

    gm = games.drop_duplicates("game_id").set_index("game_id")
    div = gm.get("div_game")
    out["div_game"] = (div.reindex(out.index).fillna(0)
                       .to_numpy(dtype=float) if div is not None
                       else 0.0)
    out["margin"] = out["points_h"] - out["points_a"]
    out["total"] = out["points_h"] + out["points_a"]
    out["home_win"] = out["margin"] > 0

    out = _merge_rivalry(out, rivalry)
    out = _merge_weather(out, weather)
    out["games_total_h"] = both["games_total_h"].to_numpy(dtype=float)
    out["games_total_a"] = both["games_total_a"].to_numpy(dtype=float)
    return out.reset_index()


def _merge_rivalry(out: pd.DataFrame,
                   rivalry: Optional[pd.DataFrame]) -> pd.DataFrame:
    defaults = {"h2h_n": 0.0, "h2h_win_rate_h": 0.5, "h2h_margin_h": 0.0}
    if rivalry is None or rivalry.empty:
        for col, value in defaults.items():
            out[col] = value
        return out
    out = out.merge(rivalry, on="game_id", how="left")
    for col, value in defaults.items():
        out[col] = out[col].fillna(value)
    return out


def _merge_weather(out: pd.DataFrame,
                   weather: Optional[pd.DataFrame]) -> pd.DataFrame:
    defaults = {"temp_f": 70.0, "wind_mph": 0.0, "precip_in": 0.0,
                "is_dome": 0.0, "has_weather": 0.0}
    if weather is None or weather.empty:
        for col, value in defaults.items():
            out[col] = value
        return out
    cols = ["game_id", *defaults.keys()]
    out = out.merge(weather[[c for c in cols if c in weather.columns]],
                    on="game_id", how="left")
    for col, value in defaults.items():
        if col not in out.columns:
            out[col] = value
        out[col] = out[col].fillna(value)
    return out


def _weather_available(paired: pd.DataFrame) -> bool:
    return ("has_weather" in paired.columns
            and float(paired["has_weather"].fillna(0).sum()) > 0)


def _effective_features(paired: pd.DataFrame, feature_set: str,
                        use_weather: bool = True) -> Dict[str, tuple]:
    """Feature tuples, dropping weather terms when no weather store exists.

    A missing weather store would otherwise leave constant columns collinear
    with the intercept and destabilize the fit. `use_weather=False` forces the
    weather-free fit for evaluation even when the store is populated.
    """
    fam = {k: tuple(v) for k, v in FEATURE_SETS[feature_set].items()}
    if not use_weather or not _weather_available(paired):
        fam["total"] = tuple(f for f in fam["total"] if f not in _WEATHER)
    return fam


def _design(df: pd.DataFrame, names: Sequence[str]) -> np.ndarray:
    cols = [np.ones(len(df))]
    for name in names:
        if name in df.columns:
            col = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float)
        else:
            col = np.zeros(len(df))
        col = np.nan_to_num(col, nan=0.0, posinf=0.0, neginf=0.0)
        cols.append(col)
    return np.column_stack(cols)


def fit_rows(paired: pd.DataFrame, seasons: Sequence[int]) -> pd.DataFrame:
    mask = (paired["season"].isin(list(seasons))
            & (paired["games_total_h"] >= MIN_GAMES_FOR_FIT)
            & (paired["games_total_a"] >= MIN_GAMES_FOR_FIT))
    return paired[mask]


def _ridge_solve(X: np.ndarray, y: np.ndarray,
                 rel_lambda: float = 1e-6) -> np.ndarray:
    n, p = X.shape
    lam = rel_lambda * max(1, n)
    gram = X.T @ X + np.eye(p) * lam
    try:
        return np.linalg.solve(gram, X.T @ y)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(X, y, rcond=None)[0]


def _fit_logistic(X: np.ndarray, y: np.ndarray,
                  rel_lambda: float = 1e-4, iters: int = 60) -> np.ndarray:
    """Ridge-penalized logistic regression via IRLS (no market inputs)."""
    n, p = X.shape
    beta = np.zeros(p)
    lam = rel_lambda * max(1, n)
    for _ in range(iters):
        eta = np.clip(X @ beta, -30.0, 30.0)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(mu * (1.0 - mu), 1e-9, None)
        z = eta + (y - mu) / w
        wx = X * w[:, None]
        gram = X.T @ wx + np.eye(p) * lam
        beta_new = np.linalg.solve(gram, X.T @ (w * z))
        if np.max(np.abs(beta_new - beta)) < 1e-8:
            beta = beta_new
            break
        beta = beta_new
    if not np.isfinite(beta).all():
        raise RuntimeError("non-finite logistic coefficients")
    return beta


def sigmoid(eta: float) -> float:
    return float(1.0 / (1.0 + np.exp(-np.clip(eta, -30.0, 30.0))))


def fit_forecast_models(paired: pd.DataFrame, seasons: Sequence[int],
                        feature_set: str = DEFAULT_FEATURE_SET,
                        use_weather: bool = True) -> dict:
    """Fit the three family models on warm, outcome-only rows."""
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"unknown feature_set {feature_set!r}")
    fam = _effective_features(paired, feature_set, use_weather=use_weather)
    rows = fit_rows(paired, seasons)
    if len(rows) < 500:
        raise RuntimeError(f"too few paired fit games ({len(rows)})")

    mf, tf, wf = fam["margin"], fam["total"], fam["winner"]

    xm = _design(rows, mf)
    ym = rows["margin"].to_numpy(dtype=float)
    coef_margin = _ridge_solve(xm, ym)
    resid_margin = ym - xm @ coef_margin

    xt = _design(rows, tf)
    yt = rows["total"].to_numpy(dtype=float)
    coef_total = _ridge_solve(xt, yt)
    resid_total = yt - xt @ coef_total

    no_ties = rows["margin"].to_numpy(dtype=float) != 0
    xw = _design(rows[no_ties], wf)
    yw = rows.loc[no_ties, "home_win"].to_numpy(dtype=float)
    coef_winner = _fit_logistic(xw, yw)

    if not (np.isfinite(resid_margin).all() and np.isfinite(resid_total).all()):
        raise RuntimeError("non-finite residuals; inspect features")

    return {
        "feature_set": feature_set,
        "fit_seasons": sorted(int(s) for s in set(seasons)),
        "n_paired_games": int(len(rows)),
        "winner": {
            "model_version": WINNER_MODEL_VERSION,
            "features": list(wf),
            "coefs": [float(c) for c in coef_winner],
        },
        "margin": {
            "model_version": MARGIN_MODEL_VERSION,
            "features": list(mf),
            "coefs": [float(c) for c in coef_margin],
            "resid_sd": round(float(np.std(resid_margin)), 3),
            "resid": [round(float(r), 2) for r in resid_margin],
        },
        "total": {
            "model_version": TOTAL_MODEL_VERSION,
            "features": list(tf),
            "coefs": [float(c) for c in coef_total],
            "resid_sd": round(float(np.std(resid_total)), 3),
            "resid": [round(float(r), 2) for r in resid_total],
        },
    }


# ---------------------------------------------------------------------------
# Live feature construction + projection
# ---------------------------------------------------------------------------

def _qb_epa(team_state: dict, qbs: Dict[str, dict]) -> float:
    qb_id = team_state.get("last_qb_id")
    qb = qbs.get(qb_id) if qb_id else None
    if not qb or qb.get("dropbacks", 0) <= 0:
        return 0.0
    weight = qb["dropbacks"] / (qb["dropbacks"] + QB_SHRINK)
    return weight * (qb.get("epa_c", 0.0) - team_state["off_pass"])


def _rest(value) -> float:
    try:
        x = float(value)
        return x if not np.isnan(x) else REST_FILL
    except (TypeError, ValueError):
        return REST_FILL


def game_features(state: dict, game: dict, rivalry: Optional[dict] = None,
                  weather: Optional[dict] = None) -> Optional[dict]:
    """Symmetric price-free features for one scheduled game, or None."""
    home, away = game.get("home_team"), game.get("away_team")
    teams = state.get("teams", {})
    th, ta = teams.get(home), teams.get(away)
    if th is None or ta is None:
        return None
    qbs = state.get("qbs", {})
    league_pace = state.get("league", {}).get("pace", 63.0)
    r = rivalry or {}
    w = weather or {}
    return {
        "off_h": float(th["off"]),
        "off_a": float(ta["off"]),
        "def_h": float(th["def"]),
        "def_a": float(ta["def"]),
        "pace": ((th.get("pace") if th.get("pace") is not None
                  else league_pace)
                 + (ta.get("pace") if ta.get("pace") is not None
                    else league_pace)) / 2.0 - league_pace,
        "home": 0.0 if game.get("neutral_site") else 1.0,
        "rest_diff": (_rest(game.get("home_rest"))
                      - _rest(game.get("away_rest"))),
        "dome": 1.0 if game.get("dome") else 0.0,
        "neutral": 1.0 if game.get("neutral_site") else 0.0,
        "div_game": float(game.get("div_game") or 0),
        "pass_off_h": float(th.get("off_pass", 0.0)),
        "pass_off_a": float(ta.get("off_pass", 0.0)),
        "pass_def_h": float(th.get("def_pass", 0.0)),
        "pass_def_a": float(ta.get("def_pass", 0.0)),
        "rush_off_h": float(th.get("off_rush", 0.0)),
        "rush_off_a": float(ta.get("off_rush", 0.0)),
        "rush_def_h": float(th.get("def_rush", 0.0)),
        "rush_def_a": float(ta.get("def_rush", 0.0)),
        "qb_epa_h": _qb_epa(th, qbs),
        "qb_epa_a": _qb_epa(ta, qbs),
        "h2h_n": float(r.get("h2h_n", 0.0)),
        "h2h_win_rate_h": float(r.get("h2h_win_rate_h", 0.5)),
        "temp_f": float(w.get("temp_f", 70.0) or 70.0),
        "wind_mph": float(w.get("wind_mph", 0.0) or 0.0),
        "precip_in": float(w.get("precip_in", 0.0) or 0.0),
    }


def project(state: dict, feats: dict) -> dict:
    """Line-free family projections for one game."""
    frame = pd.DataFrame([feats])
    mu_margin = float((_design(frame, state["margin"]["features"])
                       @ np.asarray(state["margin"]["coefs"], dtype=float))[0])
    mu_total = float((_design(frame, state["total"]["features"])
                      @ np.asarray(state["total"]["coefs"], dtype=float))[0])
    eta = float((_design(frame, state["winner"]["features"])
                 @ np.asarray(state["winner"]["coefs"], dtype=float))[0])
    p_home = sigmoid(eta)
    return {
        "mu_margin": mu_margin,
        "mu_total": mu_total,
        "p_home_win": p_home,
        "p_away_win": 1.0 - p_home,
    }


# ---------------------------------------------------------------------------
# State build / load
# ---------------------------------------------------------------------------

def _export_teams(teams: Dict[str, dict]) -> Dict[str, dict]:
    v2_fields = ("off", "def", "off_pass", "def_pass", "off_rush", "def_rush")
    out = {}
    for team, t in teams.items():
        view, regressed = carryover_view(t, CURRENT_SEASON, fields=v2_fields)
        games_season = (t["games_season"] if t["season"] == CURRENT_SEASON
                        else 0)
        out[team] = {
            "off": round(view["off"], 5),
            "def": round(view["def"], 5),
            "off_pass": round(view["off_pass"], 5),
            "def_pass": round(view["def_pass"], 5),
            "off_rush": round(view["off_rush"], 5),
            "def_rush": round(view["def_rush"], 5),
            "pace": round(t["pace"], 2) if t["pace"] is not None else None,
            "games_total": t["games_total"],
            "last_season": t["season"],
            "games_current_season": games_season,
            "carryover_applied": regressed,
            "last_qb_id": t.get("last_qb_id"),
        }
    return out


def rebuild_forecast_state(
        fit_seasons: Optional[Sequence[int]] = None,
        feature_set: str = DEFAULT_FEATURE_SET) -> dict:
    """Replay ratings, fit the family models, write forecast_state.json."""
    from .sources.nflverse import load_processed_v2
    from .sources.personnel import load_personnel
    from .sources.weather import load_weather

    games, team_games, situational, qb_games = load_processed_v2()
    features, replay_state = replay_v2(games, team_games, situational,
                                       qb_games)
    rivalry = compute_rivalry_frame(games)
    weather = load_weather()
    personnel = load_personnel()
    paired = paired_features(features, games, rivalry=rivalry, weather=weather)

    completed_seasons = sorted(
        int(s) for s in paired["season"].unique()
        if s > FIRST_SEASON and s < CURRENT_SEASON)
    seasons = list(fit_seasons) if fit_seasons else completed_seasons
    fit = fit_forecast_models(paired, seasons, feature_set=feature_set)

    state = {
        "model_version": FORECAST_MODEL_VERSION,
        "as_of": str(features["gameday"].max()),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "current_season": CURRENT_SEASON,
        "feature_set": feature_set,
        "season_carryover": SEASON_CARRYOVER,
        "league": {k: round(float(v), 5)
                   for k, v in replay_state["league"].items()},
        "teams": _export_teams(replay_state["teams"]),
        "qbs": {qid: {k: (round(v, 5) if isinstance(v, float) else v)
                      for k, v in q.items()}
                for qid, q in replay_state["qbs"].items()},
        "personnel_available": bool(personnel),
        **fit,
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    FORECAST_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    return state


def load_forecast_state(rebuild_if_missing: bool = True) -> dict:
    if not FORECAST_STATE_PATH.exists():
        if rebuild_if_missing:
            return rebuild_forecast_state()
        raise RuntimeError(
            "forecast_state.json missing; run `cli rebuild-forecast-state`")
    state = json.loads(FORECAST_STATE_PATH.read_text(encoding="utf-8"))
    return state
