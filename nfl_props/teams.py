"""Canonical NFL team IDs and normalization across sources.

Canonical abbreviations follow current nflverse usage (note `LA` for the Rams,
not `LAR`). games.csv keeps era abbreviations for relocated franchises, and
Bovada uses full display names, so every join goes through `normalize_team`.
A failed lookup returns None and must be surfaced by merge diagnostics, never
force-matched.
"""
from typing import Optional

CANONICAL_TEAMS = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
)

# Era / alternate abbreviations -> canonical
_ABBR_ALIASES = {
    "STL": "LA",    # Rams pre-2016
    "SD": "LAC",    # Chargers pre-2017
    "OAK": "LV",    # Raiders pre-2020
    "LAR": "LA",
    "WSH": "WAS",
    "JAC": "JAX",
    "GNB": "GB",
    "KAN": "KC",
    "NOR": "NO",
    "NWE": "NE",
    "SFO": "SF",
    "TAM": "TB",
    "LVR": "LV",
}

# Full display names (Bovada et al.) -> canonical
_NAME_ALIASES = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "los angeles rams": "LA",
    "la rams": "LA",
    "los angeles chargers": "LAC",
    "la chargers": "LAC",
    "las vegas raiders": "LV",
    "oakland raiders": "LV",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san diego chargers": "LAC",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "st. louis rams": "LA",
    "st louis rams": "LA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WAS",
    "washington football team": "WAS",
    "washington redskins": "WAS",
}

_CANONICAL_SET = set(CANONICAL_TEAMS)


def normalize_team(raw: object) -> Optional[str]:
    """Map any source spelling to a canonical abbreviation, or None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    upper = text.upper()
    if upper in _CANONICAL_SET:
        return upper
    if upper in _ABBR_ALIASES:
        return _ABBR_ALIASES[upper]
    return _NAME_ALIASES.get(text.lower())
