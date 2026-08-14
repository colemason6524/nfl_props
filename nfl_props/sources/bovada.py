"""Bovada NFL game lines + team totals (free JSON coupon API, no key/browser).

Same endpoint family as tennis_props. The main coupon carries spread /
moneyline / game total; team totals require one extra per-event fetch.
Fail-open: on fetch failure the last good payload is reused so a transient
outage produces a stale-but-diagnosed board instead of a silent empty one.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import CACHE_DIR, HTTP_TIMEOUT_SECONDS, HTTP_USER_AGENT
from ..pricing import american_to_decimal
from ..teams import normalize_team
from ..utils import log

BOVADA_BASE = "https://www.bovada.lv/services/sports/event/coupon/events/A/description"
BOVADA_NFL_URL = f"{BOVADA_BASE}/football/nfl?marketFilterId=def&preMatchOnly=true&lang=en"

# Team-total naming varies ("Total Points Scored by X", "Team Total - X",
# "X Total Points"); they are usually posted only during game week.
_TEAM_TOTAL_PATTERNS = (
    re.compile(r"total points (?:scored )?by (.+)", re.IGNORECASE),
    re.compile(r"(?:team )?total(?: points)?(?: scored)?\s*[-–:]\s*(.+)",
               re.IGNORECASE),
    re.compile(r"^(.+?)\s+(?:team )?total(?: points)?$", re.IGNORECASE),
)


def _team_total_team(description: str) -> Optional[str]:
    for pattern in _TEAM_TOTAL_PATTERNS:
        m = pattern.search(description)
        if m:
            team = normalize_team(m.group(1))
            if team:
                return team
    return None


@dataclass
class TwoWayPrice:
    line: Optional[float]
    am_a: int
    am_b: int
    dec_a: float
    dec_b: float


@dataclass
class LiveGame:
    event_id: str
    away: str                  # canonical abbr
    home: str
    away_name: str
    home_name: str
    start_time_utc: Optional[str]
    link: str
    league_path: str
    moneyline: Optional[TwoWayPrice] = None       # a=home, b=away
    spread: Optional[TwoWayPrice] = None          # line = home handicap; a=home, b=away
    game_total: Optional[TwoWayPrice] = None      # line = total; a=over, b=under
    team_totals: Dict[str, TwoWayPrice] = field(default_factory=dict)  # abbr -> O/U
    source: str = "bovada"

    def as_dict(self) -> dict:
        return asdict(self)


def _parse_american(value: object) -> int:
    text = str(value).strip().upper()
    if text in ("EVEN", "EV"):
        return 100
    return int(text.replace("+", ""))


def _price(outcome: dict) -> tuple[int, float, Optional[float]]:
    price = outcome["price"]
    am = _parse_american(price["american"])
    dec = float(price.get("decimal") or american_to_decimal(am))
    handicap = price.get("handicap")
    line = float(handicap) if handicap not in (None, "") else None
    return am, dec, line


def _is_open(market: dict) -> bool:
    return str(market.get("status", "O")).upper() in ("O", "OPEN", "")


def _is_game_period(market: dict) -> bool:
    period = market.get("period", {}) or {}
    desc = str(period.get("description", "")).lower()
    return desc in ("game", "match", "regulation time", "") or bool(period.get("main"))


def _fetch_json(url: str, cache_name: str, refresh: bool = True) -> Any:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text())
    req = Request(url, headers={
        "User-Agent": HTTP_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            payload = resp.read().decode("utf-8", "replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        if cache_path.exists():
            log(f"[bovada] fetch failed ({exc}); using cached {cache_name}")
            return json.loads(cache_path.read_text())
        raise RuntimeError(f"Bovada fetch failed: {exc}") from exc
    cache_path.write_text(payload)
    return json.loads(payload)


def fetch_nfl_payload(refresh: bool = True) -> Any:
    return _fetch_json(BOVADA_NFL_URL, "bovada_nfl.json", refresh=refresh)


def fetch_event_payload(link: str, refresh: bool = True) -> Any:
    safe = re.sub(r"[^a-z0-9]+", "_", link.lower()).strip("_")
    return _fetch_json(f"{BOVADA_BASE}{link}?lang=en",
                       f"bovada_event_{safe[:80]}.json", refresh=refresh)


def _split_teams(description: str) -> Optional[tuple[str, str]]:
    """Bovada US-sport descriptions are 'Away Team @ Home Team'."""
    for sep in (" @ ", " vs ", " VS "):
        if sep in description:
            a, b = description.split(sep, 1)
            return a.strip(), b.strip()
    return None


def _extract_main_markets(event: dict, game: LiveGame) -> None:
    for dg in event.get("displayGroups", []) or []:
        for market in dg.get("markets", []) or []:
            if not (_is_open(market) and _is_game_period(market)):
                continue
            desc = str(market.get("description", "")).lower()
            outcomes = market.get("outcomes") or []
            if len(outcomes) < 2:
                continue
            by_desc = {str(o.get("description", "")).strip().lower(): o
                       for o in outcomes}
            try:
                if desc == "moneyline" and game.moneyline is None:
                    o_h = by_desc.get(game.home_name.lower())
                    o_a = by_desc.get(game.away_name.lower())
                    if not (o_h and o_a):
                        continue
                    am_h, dec_h, _ = _price(o_h)
                    am_a, dec_a, _ = _price(o_a)
                    game.moneyline = TwoWayPrice(None, am_h, am_a, dec_h, dec_a)
                elif desc == "point spread" and game.spread is None:
                    o_h = by_desc.get(game.home_name.lower())
                    o_a = by_desc.get(game.away_name.lower())
                    if not (o_h and o_a):
                        continue
                    am_h, dec_h, line_h = _price(o_h)
                    am_a, dec_a, _ = _price(o_a)
                    if line_h is None:
                        continue
                    game.spread = TwoWayPrice(line_h, am_h, am_a, dec_h, dec_a)
                elif desc == "total" and game.game_total is None:
                    o_o, o_u = by_desc.get("over"), by_desc.get("under")
                    if not (o_o and o_u):
                        continue
                    am_o, dec_o, line = _price(o_o)
                    am_u, dec_u, _ = _price(o_u)
                    if line is None:
                        continue
                    game.game_total = TwoWayPrice(line, am_o, am_u, dec_o, dec_u)
            except (KeyError, TypeError, ValueError):
                continue


def _extract_team_totals(event_payload: Any, game: LiveGame) -> None:
    for group in event_payload or []:
        for event in group.get("events", []) or []:
            for dg in event.get("displayGroups", []) or []:
                for market in dg.get("markets", []) or []:
                    if not (_is_open(market) and _is_game_period(market)):
                        continue
                    desc = str(market.get("description", ""))
                    team = _team_total_team(desc)
                    if team not in (game.home, game.away) or team in game.team_totals:
                        continue
                    outcomes = market.get("outcomes") or []
                    by_desc = {str(o.get("description", "")).strip().lower(): o
                               for o in outcomes}
                    o_o, o_u = by_desc.get("over"), by_desc.get("under")
                    if not (o_o and o_u):
                        continue
                    try:
                        am_o, dec_o, line = _price(o_o)
                        am_u, dec_u, _ = _price(o_u)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if line is None:
                        continue
                    game.team_totals[team] = TwoWayPrice(line, am_o, am_u,
                                                         dec_o, dec_u)


def parse_games(payload: Any) -> tuple[List[LiveGame], dict]:
    games: List[LiveGame] = []
    diags = {"groups": 0, "events_seen": 0, "unparsed_descriptions": [],
             "unmatched_teams": []}
    for group in payload or []:
        diags["groups"] += 1
        path_parts = [str(p.get("description", "")) for p in group.get("path", [])]
        league_path = " / ".join(p for p in path_parts if p)
        for event in group.get("events", []) or []:
            diags["events_seen"] += 1
            desc = str(event.get("description", ""))
            teams = _split_teams(desc)
            if not teams:
                diags["unparsed_descriptions"].append(desc)
                continue
            away_name, home_name = teams
            away, home = normalize_team(away_name), normalize_team(home_name)
            if not (away and home):
                diags["unmatched_teams"].append(desc)
                continue
            start_ms = event.get("startTime")
            start_iso = None
            if start_ms:
                start_iso = datetime.fromtimestamp(
                    start_ms / 1000.0, tz=timezone.utc).isoformat()
            game = LiveGame(
                event_id=str(event.get("id") or event.get("link") or desc),
                away=away, home=home,
                away_name=away_name, home_name=home_name,
                start_time_utc=start_iso,
                link=str(event.get("link") or ""),
                league_path=league_path,
            )
            _extract_main_markets(event, game)
            games.append(game)
    return games, diags


def fetch_live_games(refresh: bool = True,
                     fetch_team_totals: bool = True) -> tuple[List[LiveGame], dict]:
    payload = fetch_nfl_payload(refresh=refresh)
    games, diags = parse_games(payload)
    tt_found = 0
    if fetch_team_totals:
        for game in games:
            if not game.link:
                continue
            try:
                event_payload = fetch_event_payload(game.link, refresh=refresh)
            except RuntimeError as exc:
                log(f"[bovada] event fetch failed for {game.link}: {exc}")
                continue
            _extract_team_totals(event_payload, game)
            tt_found += len(game.team_totals)
    diags.update({
        "games_parsed": len(games),
        "with_moneyline": sum(1 for g in games if g.moneyline),
        "with_spread": sum(1 for g in games if g.spread),
        "with_game_total": sum(1 for g in games if g.game_total),
        "team_totals_found": tt_found,
    })
    return games, diags
