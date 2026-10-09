"""No-key late-pregame injury pull (NFL).

Primary: ESPN site API injuries endpoint (no key, real-time game statuses).
Fallback: nflverse injuries CSV for the current season (practice-report
based). Both normalize into the `personnel.json` shape read by
`nfl_props.sources.personnel`: per team `qb_status` / `key_out` /
`backup_qb_id` / `updated_at` / `source`.

Fail-open contract: any fetch/parse failure -- or a degenerate zero-team
payload -- raises `PersonnelFeedError` so the caller
(`scripts/pregame_refresh.py`) skips the board write instead of publishing
on stale data. Writes are atomic (tmp + os.replace) and never clobber a
good cache with an empty fetch.

Conservative mapping notes:
- Out / Inactive / IR / PUP / NFI / Suspended / Doubtful -> "out".
  Doubtful players rarely suit up, so the backup is priced.
- Questionable -> "questionable" for the QB slot only (annotation-only,
  zero debit downstream). Questionable non-QBs are NOT added to `key_out`
  because the debit rule is questionable-never-debits.
- `key_out` carries OUT-status non-QBs only (position-mapped; the debit
  classifier ignores TE/K/P etc. by design -- conservative, no double
  count with the QB residual).
- `backup_qb_id` is left None: the feed has no depth-chart source, and the
  forecast already falls back to the most-used teammate (`last_team`
  linkage). A wrong explicit backup would be worse than none.
- Teams with no listed injuries are omitted (neutral by default downstream).
"""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..utils import fetch_bytes
from .personnel import PERSONNEL_PATH, PERSONNEL_RAW_DIR

ESPN_INJURIES_URL = ("https://site.api.espn.com/apis/site/v2/sports/"
                     "football/nfl/injuries")
NFLVERSE_INJURIES_URL = ("https://github.com/nflverse/nflverse-data/releases/"
                         "download/injuries/injuries_{season}.csv")
SOURCE_ESPN = "espn-site-api"
SOURCE_NFLVERSE = "nflverse-injuries"


class PersonnelFeedError(RuntimeError):
    """The pull failed or produced nothing usable; caller must not publish."""


def _map_status(raw: object) -> str:
    """Map a feed status string to a personnel qb_status."""
    s = str(raw or "").strip().lower()
    if not s:
        return "unknown"
    if (s in ("out", "inactive", "injured reserve", "ir", "pup", "nfi",
              "suspended", "doubtful")
            or "injured reserve" in s or s.startswith("out")):
        return "out"
    if "question" in s:
        return "questionable"
    if s in ("probable", "active", "available", "full participation"):
        return "confirmed"
    return "unknown"


# ESPN abbreviations -> the position codes the debit classifier reads.
# OT/OG must map to T/G (a bare "OT" matches no classifier prefix); TE/K/P
# and friends pass through and debit nothing by design (conservative).
_POSITION_ALIASES = {"OT": "T", "OG": "G"}


def _map_position(abbrev: object) -> Optional[str]:
    if not isinstance(abbrev, str) or not abbrev.strip():
        return None
    pos = abbrev.strip().upper()
    return _POSITION_ALIASES.get(pos, pos)


def _empty_team(source: str, stamp: str) -> dict:
    return {"qb_status": "unknown", "key_out": [], "backup_qb_id": None,
            "updated_at": stamp, "source": source}


def parse_espn_payload(payload: dict,
                       now: Optional[datetime] = None) -> Dict[str, dict]:
    """Normalize one ESPN site-API injuries payload to personnel shape."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(payload, dict):
        raise PersonnelFeedError("ESPN payload is not a JSON object")
    blocks = payload.get("injuries")
    if not isinstance(blocks, list) or not blocks:
        raise PersonnelFeedError("ESPN payload has no team blocks")
    stamp = now.isoformat()
    teams: Dict[str, dict] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        entries = block.get("injuries") or []
        if not isinstance(entries, list):
            continue
        for inj in entries:
            if not isinstance(inj, dict):
                continue
            athlete = inj.get("athlete") if isinstance(
                inj.get("athlete"), dict) else {}
            team = athlete.get("team") if isinstance(
                athlete.get("team"), dict) else {}
            abbr = team.get("abbreviation")
            if not abbr or not isinstance(abbr, str):
                continue  # fail-open per entry, never per feed
            abbr = abbr.strip().upper()
            status = _map_status(inj.get("status"))
            pos_raw = athlete.get("position")
            pos = _map_position(pos_raw.get("abbreviation") if isinstance(
                pos_raw, dict) else None)
            entry = teams.setdefault(abbr, _empty_team(SOURCE_ESPN, stamp))
            if pos == "QB":
                if status == "out":
                    entry["qb_status"] = "out"
                elif status == "questionable" and \
                        entry["qb_status"] != "out":
                    entry["qb_status"] = "questionable"
                elif entry["qb_status"] == "unknown":
                    entry["qb_status"] = "confirmed"
            else:
                if entry["qb_status"] == "unknown":
                    entry["qb_status"] = "confirmed"
                if status == "out" and pos:
                    entry["key_out"].append(
                        {"position": pos,
                         "player_id": str(inj.get("id"))
                         if inj.get("id") is not None else None})
    if not teams:
        raise PersonnelFeedError("ESPN payload produced zero teams")
    return teams


def parse_nflverse_csv(text: str, source: str = SOURCE_NFLVERSE,
                       now: Optional[datetime] = None) -> Dict[str, dict]:
    """Normalize an nflverse injuries CSV (latest REG week) to personnel."""
    now = now or datetime.now(timezone.utc)
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        raise PersonnelFeedError(f"nflverse CSV unparseable: {exc}") from exc
    reg = [r for r in rows if r.get("season_type") == "REG"
           and r.get("team") and r.get("position")]
    if not reg:
        raise PersonnelFeedError("nflverse CSV has no REG rows")
    try:
        latest = max(int(r.get("week") or 0) for r in reg)
    except (TypeError, ValueError) as exc:
        raise PersonnelFeedError(
            f"nflverse CSV has no usable week: {exc}") from exc
    stamp = now.isoformat()
    teams: Dict[str, dict] = {}
    for r in reg:
        try:
            if int(r.get("week") or 0) != latest:
                continue
        except (TypeError, ValueError):
            continue
        abbr = str(r["team"]).strip().upper()
        status = _map_status(r.get("report_status"))
        pos = _map_position(r.get("position"))
        entry = teams.setdefault(abbr, _empty_team(source, stamp))
        if pos == "QB":
            if status == "out":
                entry["qb_status"] = "out"
            elif status == "questionable" and entry["qb_status"] != "out":
                entry["qb_status"] = "questionable"
            elif entry["qb_status"] == "unknown":
                entry["qb_status"] = "confirmed"
        else:
            if entry["qb_status"] == "unknown":
                entry["qb_status"] = "confirmed"
            if status == "out" and pos:
                entry["key_out"].append(
                    {"position": pos, "player_id": r.get("gsis_id")})
    if not teams:
        raise PersonnelFeedError("nflverse CSV produced zero teams")
    return teams


def _current_season(now: datetime) -> int:
    return now.year if now.month >= 3 else now.year - 1


def fetch_espn_teams(now: Optional[datetime] = None) -> Dict[str, dict]:
    """Fetch + parse the ESPN feed; raises PersonnelFeedError on failure."""
    try:
        raw = fetch_bytes(ESPN_INJURIES_URL)
    except Exception as exc:
        raise PersonnelFeedError(f"ESPN fetch failed: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PersonnelFeedError(f"ESPN payload not JSON: {exc}") from exc
    return parse_espn_payload(payload, now=now)


def fetch_nflverse_teams(now: Optional[datetime] = None) -> Dict[str, dict]:
    """Fetch + parse the nflverse fallback; raises on failure."""
    now = now or datetime.now(timezone.utc)
    last_err: Exception = PersonnelFeedError("no seasons attempted")
    for season in (_current_season(now), _current_season(now) - 1):
        url = NFLVERSE_INJURIES_URL.format(season=season)
        try:
            raw = fetch_bytes(url)
        except Exception as exc:  # try the prior season before giving up
            last_err = exc
            continue
        try:
            return parse_nflverse_csv(raw.decode("utf-8"), now=now)
        except PersonnelFeedError as exc:
            last_err = exc
    raise PersonnelFeedError(f"nflverse fallback failed: {last_err}")


def _atomic_write_json(path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_and_store(now: Optional[datetime] = None,
                    archive_raw: bool = True) -> Dict[str, dict]:
    """Pull injuries (ESPN, nflverse fallback) and atomically store them.

    Returns the normalized personnel dict. Raises PersonnelFeedError when
    nothing usable came back -- the existing cache is left untouched.
    """
    now = now or datetime.now(timezone.utc)
    try:
        teams = fetch_espn_teams(now=now)
        source = SOURCE_ESPN
    except PersonnelFeedError:
        teams = fetch_nflverse_teams(now=now)
        source = SOURCE_NFLVERSE
    if not teams:
        raise PersonnelFeedError("degenerate zero-team payload; "
                                 "cache untouched")
    for entry in teams.values():
        entry["source"] = source
        entry["updated_at"] = now.isoformat()
    _atomic_write_json(PERSONNEL_PATH, teams)
    if archive_raw:
        try:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            PERSONNEL_RAW_DIR.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(
                PERSONNEL_RAW_DIR / f"injuries_{source}_{stamp}.json", teams)
        except Exception:
            pass  # audit archive must never block the pull
    return teams


def personnel_is_fresh(max_age_hours: float = 20.0,
                       now: Optional[datetime] = None) -> bool:
    """True when the on-disk cache was updated within `max_age_hours`."""
    if not PERSONNEL_PATH.exists():
        return False
    now = now or datetime.now(timezone.utc)
    try:
        payload = json.loads(PERSONNEL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    newest = None
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        try:
            ts = datetime.fromisoformat(
                str(entry.get("updated_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if newest is None or ts > newest:
            newest = ts
    if newest is None:
        return False
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return (now - newest).total_seconds() <= max_age_hours * 3600


def summarize(teams: Dict[str, dict]) -> List[str]:
    """One-line-per-affected-team summary for logs."""
    lines = []
    for abbr in sorted(teams):
        entry = teams[abbr]
        outs = [e.get("position") for e in entry.get("key_out", [])
                if isinstance(e, dict)]
        lines.append(f"{abbr}: qb={entry.get('qb_status')} "
                     f"out={outs or []}")
    return lines
