"""Game-level forecast records: the product.

One `GameForecast` per scheduled current-day game. The forecast (moneyline
pick, projected margin, projected total) is built from model state alone;
market references are attached afterwards and can never change it. A game with
an unrated side is still published, marked available=False, so coverage never
silently disappears.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .forecasting import game_features, project
from .ratings.epa import game_total_probs, spread_probs
from .references import (BOVADA, POLYMARKET, ForecastReference,
                         build_reference)
from .rivalry import live_rivalry_features
from .sources.weather import live_weather_for
from .version import FORECAST_MODEL_VERSION


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value).strip()
    return text or None


def stable_forecast_id(game: dict) -> str:
    gid = game.get("game_id")
    if gid is not None:
        try:
            if not isinstance(gid, float) or gid == gid:
                return f"g:{gid}"
        except (TypeError, ValueError):
            pass
    day = str(game.get("gameday") or game.get("kickoff_utc") or "")[:10]
    return (f"m:{game.get('away_team')}@{game.get('home_team')}:{day}")


@dataclass
class GameForecast:
    forecast_id: str
    game_id: Optional[str]
    away: str
    home: str
    away_name: str
    home_name: str
    kickoff_utc: Optional[str]
    season: Optional[int]
    week: Optional[int]
    game_type: str
    neutral_site: bool
    venue: Optional[str]
    available: bool
    model_version: str = FORECAST_MODEL_VERSION
    family_versions: Dict[str, str] = field(default_factory=dict)
    winner_pick: Optional[str] = None
    winner_confidence: Optional[float] = None
    p_home_win: Optional[float] = None
    p_away_win: Optional[float] = None
    projected_margin: Optional[float] = None
    projected_total: Optional[float] = None
    margin_sd: Optional[float] = None
    total_sd: Optional[float] = None
    features_used: Dict[str, float] = field(default_factory=dict)
    references: List[ForecastReference] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["references"] = [r.as_dict() for r in self.references]
        return d


def _poly_side(poly: Optional[dict], family: str, side: str,
               opp: str) -> Optional[tuple]:
    if not poly:
        return None
    if family == "moneyline":
        refs = {r.get("side"): r for r in (poly.get("moneyline") or [])}
        if side in refs and opp in refs:
            return refs[side]["decimal"], refs[opp]["decimal"]
        return None
    if family == "spread":
        sp = poly.get("spread")
        if not sp:
            return None
        d_side = sp.get(f"{side}_decimal")
        d_opp = sp.get(f"{opp}_decimal")
        if d_side and d_opp:
            return d_side, d_opp
        return None
    if family == "total":
        total = poly.get("total")
        if not total:
            return None
        if side == "over":
            return total.get("over_decimal"), total.get("under_decimal")
        return total.get("under_decimal"), total.get("over_decimal")
    return None


def _prices(family: str, side: str, opp: str, bovada_game,
            poly: Optional[dict]) -> List[tuple]:
    prices: List[tuple] = []
    if bovada_game is not None:
        market = (bovada_game.moneyline if family == "moneyline"
                  else bovada_game.spread if family == "spread"
                  else bovada_game.game_total)
        if market is not None:
            if family == "total":
                d_side = market.dec_a if side == "over" else market.dec_b
                d_opp = market.dec_b if side == "over" else market.dec_a
            else:
                d_side = market.dec_a if side == "home" else market.dec_b
                d_opp = market.dec_b if side == "home" else market.dec_a
            prices.append((BOVADA, d_side, d_opp))
    poly_pair = _poly_side(poly, family, side, opp)
    if poly_pair:
        prices.append((POLYMARKET, poly_pair[0], poly_pair[1]))
    return prices


def _resolve_spread_line(bovada_game, poly: Optional[dict]) -> Optional[float]:
    if bovada_game is not None and bovada_game.spread is not None:
        if bovada_game.spread.line is not None:
            return float(bovada_game.spread.line)
    if poly and poly.get("spread") and poly["spread"].get("line") is not None:
        return float(poly["spread"]["line"])
    return None


def _resolve_total_line(bovada_game, poly: Optional[dict]) -> Optional[float]:
    if (bovada_game is not None and bovada_game.game_total is not None
            and bovada_game.game_total.line is not None):
        return float(bovada_game.game_total.line)
    if poly and poly.get("total") and poly["total"].get("line") is not None:
        return float(poly["total"]["line"])
    return None


def build_game_forecast(game: dict, state: dict,
                        bovada_game=None,
                        poly: Optional[dict] = None,
                        games_df=None) -> GameForecast:
    home = _clean(game.get("home_team"))
    away = _clean(game.get("away_team"))
    kickoff = _clean(game.get("kickoff_utc"))
    base = dict(
        forecast_id=stable_forecast_id(game),
        game_id=game.get("game_id"),
        away=away or "?",
        home=home or "?",
        away_name=away or "?",
        home_name=home or "?",
        kickoff_utc=kickoff,
        season=game.get("season"),
        week=game.get("week"),
        game_type=str(game.get("game_type") or "REG"),
        neutral_site=bool(game.get("neutral_site")),
        venue=_clean(game.get("venue")),
        family_versions={
            "winner": state["winner"]["model_version"],
            "margin": state["margin"]["model_version"],
            "total": state["total"]["model_version"],
        },
    )

    teams = state.get("teams", {})
    if home not in teams or away not in teams:
        fc = GameForecast(**base, available=False)
        fc.notes.append("team not rated; no forecast")
        return fc

    rivalry = live_rivalry_features(games_df, home, away, kickoff) \
        if games_df is not None else None
    weather = live_weather_for(game, kickoff)
    feats = game_features(state, game, rivalry=rivalry, weather=weather)
    if feats is None:
        fc = GameForecast(**base, available=False)
        fc.notes.append("insufficient ratings; no forecast")
        return fc

    proj = project(state, feats)
    p_home = proj["p_home_win"]
    winner_pick = "home" if p_home >= 0.5 else "away"
    rm = np.asarray(state["margin"]["resid"], dtype=float)
    rt = np.asarray(state["total"]["resid"], dtype=float)

    fc = GameForecast(
        **base,
        available=True,
        winner_pick=winner_pick,
        winner_confidence=round(max(p_home, 1.0 - p_home), 4),
        p_home_win=round(p_home, 4),
        p_away_win=round(1.0 - p_home, 4),
        projected_margin=round(proj["mu_margin"], 1),
        projected_total=round(proj["mu_total"], 1),
        margin_sd=state["margin"].get("resid_sd"),
        total_sd=state["total"].get("resid_sd"),
        features_used={k: round(v, 4) for k, v in feats.items()})

    # Moneyline: side fixed by the winner model.
    p_ml = fc.p_home_win if winner_pick == "home" else fc.p_away_win
    ml_opp = "away" if winner_pick == "home" else "home"
    fc.references.append(build_reference(
        "moneyline", winner_pick, None, p_ml, 0.0,
        _prices("moneyline", winner_pick, ml_opp, bovada_game, poly)))

    # Spread: posted line defines the proposition; projection picks the side.
    spread_line = _resolve_spread_line(bovada_game, poly)
    if spread_line is not None:
        probs = spread_probs(proj["mu_margin"], rm, home_spread=spread_line)
        if proj["mu_margin"] + spread_line > 0:
            side, side_line = "home", spread_line
            p_model = probs["p_home_cover"]
            opp = "away"
        else:
            side, side_line = "away", -spread_line
            p_model = probs["p_away_cover"]
            opp = "home"
        fc.references.append(build_reference(
            "spread", side, side_line, p_model, probs["p_push"],
            _prices("spread", side, opp, bovada_game, poly)))
    else:
        fc.references.append(build_reference(
            "spread", None, None, None, 0.0, [], note="no posted line"))

    # Total: posted number defines the proposition; projection picks the side.
    total_line = _resolve_total_line(bovada_game, poly)
    if total_line is not None:
        probs = game_total_probs(proj["mu_total"], rt, total_line)
        if proj["mu_total"] > total_line:
            side, p_model, opp = "over", probs["p_over"], "under"
        else:
            side, p_model, opp = "under", probs["p_under"], "over"
        fc.references.append(build_reference(
            "total", side, total_line, p_model, probs["p_push"],
            _prices("total", side, opp, bovada_game, poly)))
    else:
        fc.references.append(build_reference(
            "total", None, None, None, 0.0, [], note="no posted line"))

    if teams[home].get("carryover_applied") or \
            teams[away].get("carryover_applied"):
        fc.notes.append("prior-season carryover applied")
    if state.get("personnel_available"):
        from .sources.personnel import personnel_context
        for team in (home, away):
            ctx = personnel_context(team)
            if ctx.get("qb_status") in ("out", "questionable"):
                fc.notes.append(f"{team} QB {ctx['qb_status']}")
    return fc
