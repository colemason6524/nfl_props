"""Official NFL schedule loading and current-day game selection.

The forecast universe is the nflverse schedule, not the sportsbook coupon, so
unpriced games still publish. Games are filtered to one Eastern-Time calendar
day and to kickoffs strictly in the future (a game that has started is not a
forecast).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

import pandas as pd

from .config import FORECAST_TZ
from .sources.nflverse import load_processed


def load_schedule() -> pd.DataFrame:
    """Canonical schedule rows with a parsed UTC `kickoff_utc` column."""
    games, _ = load_processed()
    df = games.copy()
    if "location" not in df.columns:
        df["location"] = None
    if "div_game" not in df.columns:
        df["div_game"] = 0
    if "roof" not in df.columns:
        df["roof"] = None
    day = df["gameday"].astype(str).str[:10]
    time = df.get("gametime")
    if time is None:
        time = "13:00"
    time = time.fillna("13:00").astype(str).replace("", "13:00")
    stamps = pd.to_datetime(day + " " + time, errors="coerce")
    try:
        stamps = (stamps.dt.tz_localize(FORECAST_TZ, ambiguous="NaT",
                                        nonexistent="shift_forward")
                  .dt.tz_convert("UTC"))
    except (TypeError, AttributeError):
        stamps = pd.to_datetime(day, errors="coerce", utc=True)
    df["kickoff_utc"] = stamps
    return df


def _neutral(row) -> bool:
    return str(getattr(row, "location", "") or "") == "Neutral"


def _dome(row) -> bool:
    return str(getattr(row, "roof", "") or "").lower() in ("closed", "dome")


def row_to_game(row) -> dict:
    gid = getattr(row, "game_id", None)
    return {
        "game_id": (str(gid) if gid is not None and not pd.isna(gid)
                    else None),
        "season": int(row.season),
        "week": int(row.week) if pd.notna(getattr(row, "week", None)) else None,
        "game_type": str(getattr(row, "game_type", "REG") or "REG"),
        "away_team": row.away_team,
        "home_team": row.home_team,
        "kickoff_utc": (row.kickoff_utc.isoformat()
                        if pd.notna(row.kickoff_utc) else None),
        "neutral_site": _neutral(row),
        "dome": _dome(row),
        "roof": str(getattr(row, "roof", "") or "") or None,
        "div_game": int(getattr(row, "div_game", 0) or 0),
        "home_rest": _rest(getattr(row, "home_rest", None)),
        "away_rest": _rest(getattr(row, "away_rest", None)),
        "venue": str(getattr(row, "stadium", "") or "") or None,
        "temp_f": _num(getattr(row, "temp", None)),
        "wind_mph": _num(getattr(row, "wind", None)),
    }


def _num(value) -> Optional[float]:
    try:
        x = float(value)
        return None if pd.isna(x) else x
    except (TypeError, ValueError):
        return None


def _rest(value) -> Optional[float]:
    return _num(value)


def current_day_games(games: Optional[pd.DataFrame] = None,
                      now: Optional[datetime] = None,
                      date_override: Optional[str] = None) -> List[dict]:
    """Scheduled games on the target ET day that have not started yet."""
    df = load_schedule() if games is None else games
    if df.empty:
        return []
    now = now or datetime.now(timezone.utc)
    if date_override:
        target = date.fromisoformat(str(date_override)[:10])
    else:
        target = now.astimezone(_tz()).date()
    df = df.copy()
    df["_day"] = pd.to_datetime(df["gameday"].astype(str).str[:10],
                                errors="coerce").dt.date
    rows = df[df["_day"] == target]
    out: List[dict] = []
    for row in rows.itertuples():
        kickoff = getattr(row, "kickoff_utc", None)
        if kickoff is None or pd.isna(kickoff):
            continue
        if kickoff <= now:
            continue
        out.append(row_to_game(row))
    out.sort(key=lambda g: (g["kickoff_utc"] or "9999", g["away_team"],
                            g["home_team"]))
    return out


def _tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo(FORECAST_TZ)
