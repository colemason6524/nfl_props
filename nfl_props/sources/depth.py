"""nflverse depth-chart adapter (no key, cache-first, fail-open).

The injury model needs to know whether an OUT player is a starter or a
backup: a lone backup being inactive must not debit the team or flag the
starter as out. The nflverse depth charts release carries one snapshot per
`dt` with `pos_abb` / `pos_rank` (rank 1 = starter for that slot):

    https://github.com/nflverse/nflverse-data/releases/download/depth_charts/depth_charts_{season}.csv

Both the raw CSV (`data/raw/depth_charts_{season}.csv`) and the derived
index (`data/processed/depth_chart.json`) are gitignored caches.

Contract is fail-open everywhere: `refresh()` never raises (a failed
download keeps the previous index), `load_index()` returns `{}` on a
missing/corrupt/stale file, and `is_starter()` returns None (unknown)
whenever the player or position cannot be resolved. None means "old
behavior" downstream -- no caller is required to act on it.

No network happens on import.
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from ..config import HTTP_TIMEOUT_SECONDS, HTTP_USER_AGENT, PROCESSED_DIR, RAW_DIR
from ..utils import log

DEPTH_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
             "depth_charts/depth_charts_{season}.csv")
RAW_PATH_TEMPLATE = "depth_charts_{season}.csv"
PROCESSED_NAME = "depth_chart.json"

# Coarse position groups. Special teams (K, PK, P, LS, H, KR, PR) are
# deliberately absent: they must never be treated as starters for debits.
GROUP_MAP = {
    "QB": "QB",
    "RB": "RB", "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "C": "OL", "LG": "OL", "RG": "OL", "LT": "OL", "RT": "OL",
    "T": "OL", "G": "OL",
    "LDE": "DL", "RDE": "DL", "LDT": "DL", "RDT": "DL", "DE": "DL",
    "DT": "DL", "NT": "DL", "EDGE": "DL",
    "MLB": "LB", "WLB": "LB", "SLB": "LB", "LILB": "LB", "RILB": "LB",
    "LB": "LB", "ILB": "LB", "OLB": "LB",
    "LCB": "DB", "RCB": "DB", "SS": "DB", "FS": "DB", "NB": "DB",
    "CB": "DB", "S": "DB", "DB": "DB", "SAF": "DB",
}

_NAME_SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")

INDEX_MAX_AGE_DAYS = 7
DEFAULT_MAX_AGE_HOURS = 20.0


def raw_path(season: int) -> Path:
    return RAW_DIR / RAW_PATH_TEMPLATE.format(season=season)


def processed_path() -> Path:
    return PROCESSED_DIR / PROCESSED_NAME


def group_for(position) -> Optional[str]:
    """Map a position label (e.g. "WR", "LDE", "OT") to a coarse group."""
    if not isinstance(position, str):
        return None
    pos = position.strip().upper()
    if not pos:
        return None
    if pos in GROUP_MAP:
        return GROUP_MAP[pos]
    # Strip trailing slot digits ("WR1", "CB2") and punctuation.
    trimmed = pos.rstrip("0123456789-/_ ")
    if trimmed in GROUP_MAP:
        return GROUP_MAP[trimmed]
    return None


def norm_name(name) -> Optional[str]:
    """Normalize a player name to `f.lastname` (suffix-stripped, lowercase)."""
    if not isinstance(name, str):
        return None
    cleaned = name.replace(".", " ").replace(",", " ").strip()
    if not cleaned:
        return None
    parts = [p for p in cleaned.split() if p.lower() not in _NAME_SUFFIXES]
    if len(parts) >= 2:
        return f"{parts[0][0]}.{parts[-1]}".lower()
    if parts:
        return parts[0].lower()
    return None


_DEPTH_COLUMNS = ("dt", "team", "player_name", "espn_id", "gsis_id",
                  "pos_abb", "pos_rank")


def parse_depth_csv(text: str, season: int) -> dict:
    """Parse a depth-chart CSV into an index at its latest snapshot.

    Returns {"season": int, "as_of": dt,
             "teams": {team: {group: {key: {"name", "rank", "group"}}}}}.
    Only rows whose `pos_abb` maps to a coarse group are kept; every player
    is indexed under espn_id, gsis_id and normalized name, and `rank` is
    the minimum `pos_rank` seen for that player within the group.
    Single-pass `csv.reader` (the release file is ~670k rows) with the
    index reset whenever a newer snapshot stamp appears.
    """
    empty = {"season": season, "as_of": None, "teams": {}}
    reader = csv.reader(io.StringIO(text or ""))
    try:
        header = next(reader)
    except StopIteration:
        return empty
    cols = {name.strip(): i for i, name in enumerate(header)}
    if any(c not in cols for c in _DEPTH_COLUMNS):
        return empty
    dt_i, team_i, name_i, espn_i, gsis_i = (cols["dt"], cols["team"],
                                            cols["player_name"],
                                            cols["espn_id"], cols["gsis_id"])
    abb_i, rank_i = cols["pos_abb"], cols["pos_rank"]
    width = max(cols[c] for c in _DEPTH_COLUMNS) + 1
    teams: Dict[str, dict] = {}
    latest = None
    for row in reader:
        if len(row) < width:
            continue
        stamp = row[dt_i].strip()
        if not stamp:
            continue
        if latest is None or stamp > latest:
            latest = stamp
            teams = {}
        elif stamp != latest:
            continue
        group = group_for(row[abb_i])
        if group is None:
            continue
        team = row[team_i].strip().upper()
        if not team:
            continue
        try:
            rank = int(row[rank_i].strip() or 0)
        except (TypeError, ValueError):
            continue
        if rank <= 0:
            continue
        name = row[name_i].strip() or None
        keys = [k.strip() for k in (row[espn_i], row[gsis_i], norm_name(name))
                if k is not None and str(k).strip()]
        if not keys:
            continue
        group_index = teams.setdefault(team, {}).setdefault(group, {})
        existing = group_index.get(keys[0])
        prev = existing.get("rank") if isinstance(existing, dict) else None
        best = rank if prev is None else min(rank, prev)
        payload = {"name": name, "rank": best, "group": group}
        for key in keys:
            group_index[key] = payload
    if latest is None:
        return empty
    return {"season": season, "as_of": latest, "teams": teams}


def load_index() -> dict:
    """Read the processed depth index; {} when missing/corrupt/stale (>7d)."""
    path = processed_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get("teams"),
                                                       dict):
        return {}
    stamp = payload.get("as_of")
    age_source: Optional[datetime] = None
    if isinstance(stamp, str) and stamp:
        try:
            age_source = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            age_source = None
    if age_source is None:
        try:
            age_source = datetime.fromtimestamp(path.stat().st_mtime,
                                                tz=timezone.utc)
        except OSError:
            return {}
    if age_source.tzinfo is None:
        age_source = age_source.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - age_source).total_seconds() / 86400
    if age_days > INDEX_MAX_AGE_DAYS:
        return {}
    return payload


def is_starter(team: Optional[str], position, player_id=None, name=None,
               index: Optional[dict] = None) -> Optional[bool]:
    """True/False when the depth chart resolves the player, else None.

    Resolution order is player_id (espn or gsis id) then normalized name.
    Unknown position, team, or player -> None ("unknown", old behavior).
    """
    group = group_for(position)
    if group is None:
        return None
    idx = index if index is not None else load_index()
    if not isinstance(idx, dict):
        return None
    teams = idx.get("teams")
    if not isinstance(teams, dict) or not team:
        return None
    group_index = (teams.get(str(team).strip().upper()) or {}).get(group)
    if not isinstance(group_index, dict):
        return None
    candidates = []
    if player_id is not None:
        candidates.append(str(player_id).strip())
    normed = norm_name(name)
    if normed:
        candidates.append(normed)
    for key in candidates:
        if not key:
            continue
        entry = group_index.get(key)
        if isinstance(entry, dict):
            return entry.get("rank") == 1
    return None


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _raw_is_fresh(path: Path, max_age_hours: float, now: datetime) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    return (now - mtime).total_seconds() <= max_age_hours * 3600


def _download(url: str, dest: Path) -> None:
    """Stream `url` to `dest` atomically (tmp + os.replace)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent),
                               prefix=dest.name + ".", suffix=".tmp")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": HTTP_USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            with os.fdopen(fd, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def refresh(season: Optional[int] = None, now: Optional[datetime] = None,
            force: bool = False,
            max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> dict:
    """Rebuild the depth index; fail-open (never raises).

    Reuses the cached raw CSV when it is younger than `max_age_hours`
    (unless `force`), otherwise streams a fresh download. On any exception
    the previous processed index is returned unchanged.
    """
    now = now or datetime.now(timezone.utc)
    season = int(season or now.year)
    path = raw_path(season)
    try:
        if force or not (path.exists() and _raw_is_fresh(path, max_age_hours,
                                                         now)):
            _download(DEPTH_URL.format(season=season), path)
        text = path.read_text(encoding="utf-8", errors="replace")
        index = parse_depth_csv(text, season)
        if not index.get("teams"):
            raise ValueError("depth chart parse produced no teams")
        index["generated_at"] = now.isoformat()
        _atomic_write_text(processed_path(), json.dumps(index))
        log(f"depth: {len(index['teams'])} teams indexed "
            f"(as_of {index.get('as_of')})")
        return index
    except Exception as exc:  # fail-open: keep whatever we had
        log(f"depth: refresh failed ({exc}); keeping previous index")
        return load_index()
