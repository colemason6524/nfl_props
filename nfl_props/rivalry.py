"""Point-in-time head-to-head (rivalry) features.

All-time series context, computed strictly from completed games before the
game being forecast. This is the "all-time rivalry" input: how the current
home team has fared against this opponent historically, and how many meetings
there have been. No future information is used in replay or live.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


def _completed(games: pd.DataFrame) -> pd.DataFrame:
    df = games[games["home_score"].notna() & games["away_score"].notna()
               & games["home_team"].notna() & games["away_team"].notna()].copy()
    return df.sort_values(["gameday", "game_id"])


def _update(state: dict, home: str, away: str, margin_home: float) -> None:
    key = tuple(sorted((home, away)))
    st = state.setdefault(key, {"n": 0, "wins_first": 0.0,
                                "margin_first": 0.0})
    first = key[0]
    st["n"] += 1
    if home == first:
        st["wins_first"] += 1.0 if margin_home > 0 else 0.0
        st["margin_first"] += margin_home
    else:
        st["wins_first"] += 1.0 if margin_home < 0 else 0.0
        st["margin_first"] += -margin_home


def _view(state: dict, home: str, away: str) -> dict:
    key = tuple(sorted((home, away)))
    st = state.get(key)
    if not st or st["n"] == 0:
        return {"h2h_n": 0.0, "h2h_win_rate_h": 0.5, "h2h_margin_h": 0.0}
    first = key[0]
    if home == first:
        wr = st["wins_first"] / st["n"]
        margin = st["margin_first"] / st["n"]
    else:
        wr = 1.0 - st["wins_first"] / st["n"]
        margin = -st["margin_first"] / st["n"]
    return {"h2h_n": float(st["n"]), "h2h_win_rate_h": float(wr),
            "h2h_margin_h": float(margin)}


def compute_rivalry_frame(games: pd.DataFrame) -> pd.DataFrame:
    """Per-game prior head-to-head features (point-in-time)."""
    state: Dict[tuple, dict] = {}
    rows: List[dict] = []
    for g in _completed(games).itertuples():
        row = {"game_id": g.game_id}
        row.update(_view(state, g.home_team, g.away_team))
        rows.append(row)
        _update(state, g.home_team, g.away_team,
                float(g.home_score) - float(g.away_score))
    if not rows:
        return pd.DataFrame(columns=["game_id", "h2h_n", "h2h_win_rate_h",
                                     "h2h_margin_h"])
    return pd.DataFrame(rows)


def live_rivalry_features(games: pd.DataFrame, home: str, away: str,
                          before_iso: Optional[str]) -> dict:
    """Head-to-head state entering `before_iso` for one live matchup."""
    if games is None or games.empty or home is None or away is None:
        return {"h2h_n": 0.0, "h2h_win_rate_h": 0.5, "h2h_margin_h": 0.0}
    df = _completed(games)
    if before_iso:
        day = str(before_iso)[:10]
        df = df[df["gameday"].astype(str).str[:10] <= day]
    pair = df[((df["home_team"] == home) & (df["away_team"] == away))
              | ((df["home_team"] == away) & (df["away_team"] == home))]
    if pair.empty:
        return {"h2h_n": 0.0, "h2h_win_rate_h": 0.5, "h2h_margin_h": 0.0}
    wins = 0
    margins = []
    for g in pair.itertuples():
        margin_home = float(g.home_score) - float(g.away_score)
        if g.home_team == home:
            wins += 1 if margin_home > 0 else 0
            margins.append(margin_home)
        else:
            wins += 1 if margin_home < 0 else 0
            margins.append(-margin_home)
    n = len(pair)
    return {"h2h_n": float(n), "h2h_win_rate_h": wins / n,
            "h2h_margin_h": sum(margins) / n}
