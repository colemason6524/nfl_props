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

TAG_SLUGS = ("nfl", "nfl-gameday", "football")

_TOTAL_LINE = re.compile(r"O/U\s*([0-9]+(?:\.[0-9]+)?)")
_SPREAD_LINE = re.compile(r"([+-]?[0-9]+(?:\.[0-9]+)?)")


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
    best_gap = 1.0
    spread: Optional[dict] = None
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
            if gap < best_gap and 0.0 < p_over < 1.0:
                best_gap = gap
                best_total = {
                    "line": float(match.group(1)),
                    "over_decimal": round(1.0 / p_over, 4),
                    "under_decimal": round(1.0 / (1.0 - p_over), 4),
                    "over_p": round(p_over, 4),
                }
        elif mtype == "spreads" and len(outcomes) == 2 and len(prices) == 2:
            question = str(market.get("question") or "")
            match = _SPREAD_LINE.search(question)
            if not match:
                continue
            line = float(match.group(1))
            price_by_team = {}
            for name, price in zip(outcomes, prices):
                canon = normalize_team(name)
                if canon:
                    price_by_team[canon] = float(price)
            if home in price_by_team and away in price_by_team:
                # The question names the handicapped team next to the number.
                home_favored = home.lower() in question.lower().split(str(line))[0].lower()
                home_line = -abs(line) if home_favored else abs(line)
                spread = {
                    "line": home_line,
                    "home_decimal": round(1.0 / price_by_team[home], 4)
                    if price_by_team[home] > 0 else None,
                    "away_decimal": round(1.0 / price_by_team[away], 4)
                    if price_by_team[away] > 0 else None,
                }
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
