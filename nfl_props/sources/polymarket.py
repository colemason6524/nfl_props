"""Polymarket secondary reference source (best-effort, fail-open).

Bovada is primary; Polymarket is used only as a fallback reference when
Bovada is unavailable, stale, or empty. It is captured separately and never
averaged with Bovada at the source level (the forecast layer builds a
consensus probability from de-vigged sources).

Any error returns no references plus a diagnostic and the board proceeds.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..config import HTTP_TIMEOUT_SECONDS, POLYMARKET_GAMMA_BASE
from ..pricing import decimal_to_american
from ..teams import normalize_team
from ..utils import log

TAG_SLUGS = ("nfl-gameday", "nfl", "football")

_TOTAL_LINE = re.compile(r"O/U\s*([0-9]+(?:\.[0-9]+)?)")
_SPREAD_LINE = re.compile(r"([+-]?[0-9]+(?:\.[0-9]+)?)")
_SPREAD_TEAM = re.compile(r"Spread:\s*(.+?)\s*\(", re.IGNORECASE)

# Alternate-spread ladders carry dead rungs at the tail: no live book, ~zero
# quoted liquidity, yet their outcomePrices can still read 0.495/0.505 and so
# beat the real main line on gap-to-even-money. Observed dead rungs quote
# liquidityNum 0.05-71 while live rungs quote 29k-3M, so require a live bid
# plus at least this much quoted liquidity before a rung is eligible.
_SPREAD_MIN_LIQUIDITY = 1000.0
# Near-tie window (in probability points) for rung selection. The genuine
# main line is the rung closest to even money; this only decides between
# rungs that are effectively tied there, and the tie goes to the smallest
# |line| so a ladder tail can never win on a rounding difference. Kept tight
# (one price point): a wide window would sweep in the whole near-even cluster
# of a 30+ rung ladder and bias the pick toward the ladder's short end.
_SPREAD_GAP_TOLERANCE = 0.01


def _get(url: str) -> Optional[object]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "nfl_props/1.0 (reference-only)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            OSError, json.JSONDecodeError):
        return None


def _split_matchup(text: str) -> Optional[Tuple[str, str]]:
    for sep in (" vs. ", " vs ", " @ ", " at "):
        if sep in text:
            a, b = text.split(sep, 1)
            away, home = normalize_team(a.strip()), normalize_team(b.strip())
            if away and home:
                return away, home
    return None


def _num(value) -> Optional[float]:
    """Best-effort float; None for absent/blank/non-numeric values."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _book_is_live(market: dict) -> bool:
    """False for dead ladder tails: no live bid or ~zero quoted liquidity."""
    if "bestBid" in market:
        bid = _num(market.get("bestBid"))
        if bid is None or bid <= 0.0:
            return False
    if "liquidityNum" in market:
        liquidity = _num(market.get("liquidityNum"))
        if liquidity is not None and liquidity < _SPREAD_MIN_LIQUIDITY:
            return False
    return True


def _spread_rung(market: dict, outcomes: List[str], question: str
                 ) -> Tuple[Optional[float], Optional[str]]:
    """(signed line, team it is quoted for) for one spread rung.

    Polymarket's own `line` field is the canonical signed handicap of
    outcomes[0], so prefer it. Fall back to scraping the question text only
    for older captured payloads that never carried the field.
    """
    canonical = _num(market.get("line"))
    if canonical is not None:
        return canonical, normalize_team(outcomes[0])
    match = _SPREAD_LINE.search(question)
    if not match:
        return None, None
    team_match = _SPREAD_TEAM.search(question)
    line_team = (normalize_team(team_match.group(1))
                 if team_match else normalize_team(outcomes[0]))
    return float(match.group(1)), line_team


def _ref(family: str, side: str, line: Optional[float], decimal: float) -> dict:
    return {
        "family": family, "side": side, "line": line,
        "decimal": round(decimal, 4),
        "american": decimal_to_american(decimal),
        "source": "polymarket",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def _parse_event(event: dict) -> Optional[dict]:
    title = str(event.get("title") or event.get("question") or "")
    teams = _split_matchup(title)
    if not teams:
        return None
    away, home = teams
    ml_refs: List[dict] = []
    best_total: Optional[dict] = None
    best_total_gap = 1.0
    spread_candidates: List[dict] = []
    for market in event.get("markets") or []:
        mtype = str(market.get("sportsMarketType") or "").lower()
        try:
            outcomes = json.loads(market.get("outcomes") or "[]")
            prices = json.loads(market.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if mtype == "moneyline" and len(outcomes) == 2 and len(prices) == 2:
            price_by_team = {}
            for name, price in zip(outcomes, prices):
                canon = normalize_team(name)
                if canon:
                    price_by_team[canon] = float(price)
            if home in price_by_team and away in price_by_team:
                for side, team in (("home", home), ("away", away)):
                    p = price_by_team[team]
                    if 0.0 < p < 1.0:
                        ml_refs.append(_ref("moneyline", side, None, 1.0 / p))
        elif mtype == "totals":
            match = _TOTAL_LINE.search(str(market.get("question") or ""))
            if not match or len(outcomes) != 2:
                continue
            price_by_name = {str(o).lower(): float(p)
                             for o, p in zip(outcomes, prices)}
            p_over = price_by_name.get("over")
            if p_over is None:
                continue
            gap = abs(p_over - 0.5)
            if gap < best_total_gap and 0.0 < p_over < 1.0:
                best_total_gap = gap
                best_total = {
                    "line": float(match.group(1)),
                    "over_decimal": round(1.0 / p_over, 4),
                    "under_decimal": round(1.0 / (1.0 - p_over), 4),
                    "over_p": round(p_over, 4),
                }
        elif mtype == "spreads" and len(outcomes) == 2 and len(prices) == 2:
            if not _book_is_live(market):
                continue
            question = str(market.get("question") or "")
            line, handicap_team = _spread_rung(
                market, [str(o) for o in outcomes], question)
            if line is None:
                continue
            price_by_team = {}
            for name, price in zip(outcomes, prices):
                canon = normalize_team(name)
                if canon:
                    price_by_team[canon] = float(price)
            if home not in price_by_team or away not in price_by_team:
                continue
            # The line is quoted for one team (outcomes[0] when the market
            # carries its own `line`; otherwise the team named in the
            # question, e.g. "Spread: Cowboys (-2.5)").
            home_line = -abs(line) if handicap_team == home else abs(line)
            # The main line is the market closest to even money; keep every
            # eligible rung so near-ties can be broken deterministically.
            gap = abs(price_by_team[home] - 0.5)
            if gap < 1.0:
                spread_candidates.append({
                    "line": home_line,
                    "gap": gap,
                    "size": abs(home_line),
                    "home_decimal": round(1.0 / price_by_team[home], 4)
                    if price_by_team[home] > 0 else None,
                    "away_decimal": round(1.0 / price_by_team[away], 4)
                    if price_by_team[away] > 0 else None,
                })
    spread: Optional[dict] = None
    if spread_candidates:
        best_gap = min(c["gap"] for c in spread_candidates)
        near = [c for c in spread_candidates
                if c["gap"] <= best_gap + _SPREAD_GAP_TOLERANCE]
        pick = min(near, key=lambda c: (c["size"], c["gap"]))
        spread = {k: pick[k] for k in ("line", "home_decimal", "away_decimal")}
    if not ml_refs and not best_total and not spread:
        return None
    return {"away": away, "home": home, "moneyline": ml_refs,
            "total": best_total, "spread": spread}


def fetch_nfl_references(now: Optional[datetime] = None
                         ) -> Tuple[Dict[Tuple[str, str], dict], dict]:
    """(parsed_events_by_matchup, diagnostics). Always safe to call."""
    diags = {"source": "polymarket", "events_seen": 0, "matched": 0,
             "tags_tried": [], "errors": []}
    refs: Dict[Tuple[str, str], dict] = {}
    for slug in TAG_SLUGS:
        diags["tags_tried"].append(slug)
        payload = _get(f"{POLYMARKET_GAMMA_BASE}/events?closed=false&limit=100"
                       f"&tag_slug={slug}")
        if not isinstance(payload, list):
            diags["errors"].append(f"{slug}: no payload")
            continue
        diags["events_seen"] += len(payload)
        for event in payload:
            parsed = _parse_event(event)
            if not parsed:
                continue
            refs[(parsed["away"], parsed["home"])] = parsed
            diags["matched"] += 1
        if refs:
            break
    log(f"[polymarket] events_seen={diags['events_seen']} "
        f"matched={diags['matched']}")
    return refs, diags
