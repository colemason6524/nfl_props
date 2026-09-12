"""Game-time weather from Open-Meteo (free, no key; archive + live forecast).

Weather is an environmental, symmetric factor: it suppresses scoring (totals)
more than it separates two teams, so it is offered to the total head only. Dome
venues skip the network entirely and return calm values with `is_dome=1`.

Live runs use the forecast endpoint at prediction time. Historical training
uses `backfill_weather()` (archive endpoint, raw-cached). Any failure is
fail-open: neutral values with `has_weather=0`, so a forecast is never blocked.
Attribution: Open-Meteo / CC BY 4.0.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from ..config import HTTP_TIMEOUT_SECONDS, PROCESSED_DIR, RAW_DIR
from ..utils import log

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_RAW_DIR = RAW_DIR / "weather"
WEATHER_PATH = PROCESSED_DIR / "game_weather.parquet"

HOURLY = ("temperature_2m", "wind_speed_10m", "precipitation")
UNITS = ("temperature_unit=fahrenheit&wind_speed_unit=mph"
         "&precipitation_unit=inch")
WINDOW_HOURS = 4
NEUTRAL = {"temp_f": 70.0, "wind_mph": 0.0, "precip_in": 0.0,
           "is_dome": 0, "has_weather": 0}

# Open-Meteo free tier is 600/min, 5,000/hour, 10,000/day. Stay under the
# hourly cap and space requests so a full backfill cannot trip a 429 storm.
HOURLY_LIMIT = 4800
MIN_REQUEST_SECONDS = 0.25
_minute_window: list = []
_last_request = [0.0]


def _throttle() -> None:
    now = time.time()
    if now - _last_request[0] < MIN_REQUEST_SECONDS:
        time.sleep(MIN_REQUEST_SECONDS - (now - _last_request[0]))
        now = time.time()
    _last_request[0] = now
    _minute_window.append(now)
    while _minute_window and now - _minute_window[0] > 3600:
        _minute_window.pop(0)
    if len(_minute_window) >= HOURLY_LIMIT:
        wait = 3600 - (now - _minute_window[0]) + 1
        log(f"[weather] hourly budget reached; sleeping {wait:.0f}s")
        time.sleep(max(1.0, wait))

# Home stadium coordinates; dome venues are listed for completeness but skip
# the API (the schedule `roof` column is authoritative at run time).
_COORDS = {
    "ARI": (33.5276, -112.2626, True), "ATL": (33.7554, -84.4008, True),
    "BAL": (39.2780, -76.6227, False), "BUF": (42.7738, -78.7870, False),
    "CAR": (35.2258, -80.8528, False), "CHI": (41.8623, -87.6167, False),
    "CIN": (39.0955, -84.5161, False), "CLE": (41.5061, -81.6995, False),
    "DAL": (32.7473, -97.0945, True), "DEN": (39.7439, -105.0201, False),
    "DET": (42.3400, -83.0456, True), "GB": (44.5013, -88.0622, False),
    "HOU": (29.6847, -95.4107, True), "IND": (39.7601, -86.1639, True),
    "JAX": (30.3239, -81.6373, False), "KC": (39.0489, -94.4839, False),
    "LA": (33.9535, -118.3392, True), "LAC": (33.9535, -118.3392, True),
    "LV": (36.0909, -115.1833, True), "MIA": (25.9580, -80.2389, False),
    "MIN": (44.9735, -93.2575, True), "NE": (42.0909, -71.2643, False),
    "NO": (29.9511, -90.0812, True), "NYG": (40.8135, -74.0745, False),
    "NYJ": (40.8135, -74.0745, False), "PHI": (39.9008, -75.1675, False),
    "PIT": (40.4468, -80.0158, False), "SEA": (47.5952, -122.3316, False),
    "SF": (37.4033, -121.9694, False), "TB": (27.9759, -82.5033, False),
    "TEN": (36.1665, -86.7713, False), "WAS": (38.9076, -77.0074, False),
}


def _get_json(url: str, retries: int = 1) -> Optional[dict]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "nfl_props/1.0 (research; Open-Meteo CC BY 4.0)",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and retries > 0:
            log("[weather] 429 rate limited; backing off 60s")
            time.sleep(60)
            return _get_json(url, retries=retries - 1)
        return None
    except (urllib.error.URLError, TimeoutError, OSError,
            json.JSONDecodeError):
        return None


def _roof_is_dome(roof) -> Optional[bool]:
    text = str(roof or "").strip().lower()
    if text in ("closed", "dome", "indoors"):
        return True
    if text in ("open", "outdoors"):
        return False
    return None


def _extract(payload: Optional[dict], kickoff_iso: Optional[str]) -> dict:
    if not payload:
        return dict(NEUTRAL)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not kickoff_iso or not times:
        return dict(NEUTRAL)
    try:
        kick = datetime.fromisoformat(str(kickoff_iso).replace("Z", "+00:00"))
    except ValueError:
        return dict(NEUTRAL)
    target = kick.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
    if target not in times:
        return dict(NEUTRAL)
    idx = times.index(target)

    def series(name):
        vals = hourly.get(name) or []
        out = []
        for i in range(idx, min(idx + WINDOW_HOURS, len(vals))):
            if vals[i] is not None:
                out.append(float(vals[i]))
        return out

    temps = series("temperature_2m")
    winds = series("wind_speed_10m")
    precips = series("precipitation")
    return {
        "temp_f": temps[0] if temps else 70.0,
        "wind_mph": max(winds) if winds else 0.0,
        "precip_in": sum(precips) if precips else 0.0,
        "is_dome": 0, "has_weather": 1,
    }


def live_weather_for(game: dict, kickoff_iso: Optional[str],
                     refresh: bool = False) -> dict:
    """Forecast weather for one live game (fail-open)."""
    home = (game or {}).get("home_team")
    coords = _COORDS.get(home)
    if not coords:
        return dict(NEUTRAL)
    lat, lon, static_dome = coords
    roof_dome = _roof_is_dome((game or {}).get("roof"))
    if ((game or {}).get("dome") or roof_dome is True
            or (roof_dome is None and static_dome)):
        out = dict(NEUTRAL)
        out["is_dome"] = 1
        return out
    date = str(kickoff_iso)[:10] if kickoff_iso else "unknown"
    WEATHER_RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = WEATHER_RAW_DIR / f"f_{home}_{date}.json"
    if cache.exists() and not refresh:
        try:
            return _extract(json.loads(cache.read_text(encoding="utf-8")),
                            kickoff_iso)
        except (json.JSONDecodeError, OSError):
            pass
    _throttle()
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": ",".join(HOURLY),
        "timezone": "UTC", "forecast_days": 7,
    })
    payload = _get_json(f"{FORECAST_URL}?{query}&{UNITS}")
    if payload is not None:
        cache.write_text(json.dumps(payload), encoding="utf-8")
    return _extract(payload, kickoff_iso)


def _season_payload(home: str, season: int, start_date: str, end_date: str,
                    refresh: bool = False) -> Optional[dict]:
    """One archive request per (team, season); raw-cached."""
    coords = _COORDS.get(home)
    if not coords:
        return None
    lat, lon, _ = coords
    WEATHER_RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = WEATHER_RAW_DIR / f"as_{home}_{season}.json"
    if cache.exists() and not refresh:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    _throttle()
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "start_date": start_date,
        "end_date": end_date, "hourly": ",".join(HOURLY), "timezone": "UTC",
    })
    payload = _get_json(f"{ARCHIVE_URL}?{query}&{UNITS}")
    if payload is not None:
        cache.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _date_cached(home: str, kickoff_iso: Optional[str]) -> Optional[dict]:
    date = str(kickoff_iso)[:10]
    path = WEATHER_RAW_DIR / f"a_{home}_{date}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_weather() -> pd.DataFrame:
    if WEATHER_PATH.exists():
        return pd.read_parquet(WEATHER_PATH)
    return pd.DataFrame(columns=["game_id", "temp_f", "wind_mph", "precip_in",
                                 "is_dome", "has_weather"])


def backfill_weather(seasons=None, refresh: bool = False,
                     limit: Optional[int] = None,
                     progress_every: int = 50) -> pd.DataFrame:
    """Backfill historical game-time weather; writes game_weather.parquet.

    Dome/closed-roof games are recorded as indoor without an API call. Outdoor
    games are fetched one archive request per (home team, season) and cached,
    so a full backfill is a few hundred requests rather than one per game.
    `limit` caps API requests for a quick sample run.
    """
    from collections import defaultdict

    from .nflverse import load_raw_games

    games = load_raw_games()
    if seasons:
        games = games[games["season"].isin(list(seasons))]
    games = games[games["home_score"].notna() & games["away_score"].notna()]
    has_roof = "roof" in games.columns

    rows = []
    groups = defaultdict(list)
    for g in games.itertuples():
        kickoff = _kickoff_for(g)
        roof = getattr(g, "roof", None) if has_roof else None
        roof_dome = _roof_is_dome(roof)
        static_dome = (_COORDS.get(g.home_team) or (None, None, False))[2]
        indoor = roof_dome is True or (roof_dome is None and static_dome)
        if not indoor and g.home_team in _COORDS:
            groups[(g.home_team, int(g.season))].append((g, kickoff))
        else:
            rows.append({"game_id": g.game_id, "temp_f": 70.0, "wind_mph": 0.0,
                         "precip_in": 0.0, "is_dome": 1 if indoor else 0,
                         "has_weather": 0})

    requests_made = 0
    for (home, season), glist in sorted(groups.items()):
        dates = sorted(str(k)[:10] for _, k in glist if k)
        if not dates:
            continue
        # Reuse per-date caches from an earlier run when present.
        missing = [(g, k) for g, k in glist
                   if _date_cached(home, k) is None]
        payload = None
        if missing:
            if limit is not None and requests_made >= limit:
                pass
            else:
                payload = _season_payload(home, season, dates[0], dates[-1],
                                          refresh=refresh)
                requests_made += 1
        for g, kickoff in glist:
            cached = None if refresh else _date_cached(home, kickoff)
            if cached is not None:
                weather = _extract(cached, kickoff)
            elif payload is not None:
                weather = _extract(payload, kickoff)
            else:
                weather = dict(NEUTRAL)
            rows.append({"game_id": g.game_id, **weather})
        if progress_every and requests_made % progress_every == 0 \
                and requests_made:
            log(f"[weather] {requests_made} season requests, "
                f"{len(rows)} rows built")

    df = pd.DataFrame(rows)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(WEATHER_PATH, index=False)
    log(f"[weather] wrote {len(df)} rows -> {WEATHER_PATH.name} "
        f"({requests_made} api requests)")
    return df


def _kickoff_for(row) -> Optional[str]:
    day = str(getattr(row, "gameday", ""))[:10]
    time = str(getattr(row, "gametime", "") or "13:00")
    try:
        dt = pd.to_datetime(f"{day} {time}")
        return dt.tz_localize("America/New_York").tz_convert("UTC").isoformat()
    except (ValueError, TypeError):
        return None
