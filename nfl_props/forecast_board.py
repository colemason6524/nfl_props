"""Forecast board assembly: schedule universe -> forecasts -> references.

The universe is the official nflverse schedule for the current day, NOT the
sportsbook coupon, so unpriced games still publish. Bovada (primary) and
Polymarket (fallback) references attach afterwards.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .forecast import GameForecast, build_game_forecast


def bovada_index(bovada_games: List) -> Dict[Tuple[str, str], object]:
    return {(g.away, g.home): g for g in bovada_games or []}


def build_board(state: dict, schedule_games: List[dict],
                bovada_games: Optional[List] = None,
                poly_refs: Optional[Dict[Tuple[str, str], dict]] = None,
                games_df=None) -> Tuple[List[GameForecast], dict]:
    bidx = bovada_index(bovada_games or [])
    poly_refs = poly_refs or {}
    unmatched_bovada = set(bidx)
    forecasts: List[GameForecast] = []
    for game in schedule_games:
        key = (game.get("away_team"), game.get("home_team"))
        bg = bidx.get(key)
        if bg is not None:
            unmatched_bovada.discard(key)
        forecasts.append(build_game_forecast(
            game, state, bovada_game=bg, poly=poly_refs.get(key),
            games_df=games_df))

    forecasts.sort(key=lambda f: (f.kickoff_utc or "9999", f.away, f.home))
    summary = {
        "games": len(forecasts),
        "available": sum(1 for f in forecasts if f.available),
        "unavailable": sum(1 for f in forecasts if not f.available),
        "unavailable_games": [f"{f.away} @ {f.home}" for f in forecasts
                              if not f.available],
        "priced": sum(1 for f in forecasts if _priced(f)),
        "unpriced": sum(1 for f in forecasts if not _priced(f)),
        "bovada_matched": len(bidx) - len(unmatched_bovada),
        "bovada_unmatched_live": [f"{a} @ {h}" for (a, h) in
                                  sorted(unmatched_bovada)],
        "status_counts": _status_counts(forecasts),
    }
    return forecasts, summary


def _priced(fc: GameForecast) -> bool:
    return any(r.decimal is not None for r in fc.references)


def _status_counts(forecasts: List[GameForecast]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for fc in forecasts:
        for ref in fc.references:
            key = f"{ref.family}:{ref.status}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def detect_changes(current: List[GameForecast],
                   previous: Optional[List[dict]]) -> List[dict]:
    if not previous:
        return []
    prev = {p.get("forecast_id"): p for p in previous if p.get("forecast_id")}
    changes: List[dict] = []
    for fc in current:
        old = prev.get(fc.forecast_id)
        if not old:
            changes.append({"forecast_id": fc.forecast_id,
                            "matchup": f"{fc.away} @ {fc.home}",
                            "kind": "NEW_FORECAST"})
            continue
        reasons: List[str] = []
        if old.get("winner_pick") != fc.winner_pick:
            reasons.append(f"winner {old.get('winner_pick')} -> "
                           f"{fc.winner_pick}")
        for label, key, threshold in (
                ("margin", "projected_margin", 3.0),
                ("total", "projected_total", 3.0)):
            a, b = old.get(key), getattr(fc, key)
            if a is not None and b is not None and abs(a - b) >= threshold:
                reasons.append(f"{label} {a:+.1f} -> {b:+.1f}")
        if reasons:
            changes.append({"forecast_id": fc.forecast_id,
                            "matchup": f"{fc.away} @ {fc.home}",
                            "kind": "MATERIAL_CHANGE", "reasons": reasons})
    return changes
