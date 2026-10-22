"""Per-game pregame snapshots for latest-snapshot grading.

Each board / wave board / delta alert writes one snapshot per game it
covers: the last bettable pick state before that game's start time.
Grading (grade_forecast.py) uses the latest snapshot with
``timestamp < kickoff`` for reference ROI; the board artifact remains the
fallback when no snapshot exists.

Snapshot storage: ``outputs/snapshots/{YYYY-MM-DD}/{forecast_id}.json``.
Snapshots are keyed by the stable ``forecast_id`` (``g:<id>`` when the
schedule provides one, else ``m:<away>@<home>:<day>``) and overwritten by
newer writes. NOT pruned automatically: `cleanup_snapshots()` exists and is
tested, but no scheduled task calls it, and deleting old snapshots would change
past grading (graded references fall back to the board artifact). Retention is
currently manual, by design — pending a decision (see
`docs/INTEGRITY_AUDIT_2026-10-22.md`).

Snapshot format (one pick entry per priced family):
    {
      "game_id": <forecast_id>,
      "away": "KC", "home": "BUF",
      "kickoff_utc": "2026-10-18T20:15:00+00:00",
      "picks": [
        {"family": "spread", "side": "away", "line": -3.0,
         "american": -110, "decimal": 1.909, "p_model": 0.58,
         "p_market": 0.52, "edge": 0.06, "ev": 0.05,
         "status": "PLAYABLE", "source": "bovada"},
        ...
      ],
      "model": {"p_home_win": 0.44, "projected_margin": -3.2,
                "projected_total": 47.5, "winner_pick": "away",
                "winner_confidence": 0.56},
      "timestamp": "2026-10-18T18:30:05+00:00",
      "source": "early" | "wave" | "delta",
    }
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from nfl_props import config

SNAPSHOTS_DIR = config.OUTPUTS_DIR / "snapshots"
RETENTION_DAYS = 30
SOURCES = ("early", "wave", "delta")
ET = ZoneInfo("America/New_York")

_SAFE = re.compile(r"[^A-Za-z0-9_@+.:-]")


def _safe_name(value: str) -> str:
    return _SAFE.sub("_", str(value or "unknown"))


def _parse_ts(value) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def snapshot_dir(date_str: str) -> Path:
    return SNAPSHOTS_DIR / date_str


def snapshot_path(game_id: str, date_str: str) -> Path:
    return snapshot_dir(date_str) / f"{_safe_name(game_id)}.json"


def _ref_pick(ref) -> dict:
    get = (lambda k: ref.get(k)) if isinstance(ref, dict) \
        else (lambda k: getattr(ref, k, None))
    return {
        "family": get("family"),
        "side": get("side"),
        "line": get("line"),
        "american": get("american"),
        "decimal": get("decimal"),
        "p_model": get("p_model"),
        "p_market": get("p_market"),
        "edge": get("edge"),
        "ev": get("ev"),
        "status": get("status"),
        "source": get("source"),
        # `note` is not a pricing input; it carries the demotion markers
        # (e.g. DEAD_LINE_SUSPECT from the EV/edge ceiling) that the
        # weekly recap counts. Snapshots must preserve it or grading sees
        # a demoted NO_VALUE pick with no reason attached.
        "note": get("note"),
    }


def snapshot_from_forecast(fc) -> dict:
    """Build the pick_data payload for write_snapshot from a forecast.

    Accepts a GameForecast or its as_dict()/history dict form.
    """
    get = (lambda k: fc.get(k)) if isinstance(fc, dict) \
        else (lambda k: getattr(fc, k, None))
    refs = get("references") or []
    model = {
        "p_home_win": get("p_home_win"),
        "projected_margin": get("projected_margin"),
        "projected_total": get("projected_total"),
        "winner_pick": get("winner_pick"),
        "winner_confidence": get("winner_confidence"),
    }
    return {
        "game_id": get("forecast_id") or get("game_id"),
        "away": get("away"),
        "home": get("home"),
        "kickoff_utc": get("kickoff_utc"),
        "picks": [_ref_pick(r) for r in refs],
        "model": model,
    }


def write_snapshot(game_id: str, pick_data: dict, source: str,
                   now: Optional[datetime] = None) -> Path:
    """Write one pregame snapshot. Newer writes overwrite older ones."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    now = now or datetime.now(timezone.utc)
    date_str = now.astimezone(ET).strftime("%Y-%m-%d")
    payload = {
        "game_id": game_id,
        "away": pick_data.get("away"),
        "home": pick_data.get("home"),
        "kickoff_utc": pick_data.get("kickoff_utc"),
        "picks": pick_data.get("picks") or [],
        "model": pick_data.get("model") or {},
        "timestamp": now.isoformat(),
        "source": source,
    }
    path = snapshot_path(game_id, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_board_snapshots(forecasts, source: str,
                          now: Optional[datetime] = None) -> int:
    """Write snapshots for every available forecast. Returns count written."""
    now = now or datetime.now(timezone.utc)
    n = 0
    for fc in forecasts:
        get = (lambda k: fc.get(k)) if isinstance(fc, dict) \
            else (lambda k: getattr(fc, k, None))
        if not get("available"):
            continue
        game_id = get("forecast_id") or get("game_id")
        if not game_id:
            continue
        write_snapshot(game_id, snapshot_from_forecast(fc), source, now=now)
        n += 1
    return n


def iter_snapshots(game_id: str) -> List[dict]:
    """Load every snapshot ever written for a game_id (newest first)."""
    name = f"{_safe_name(game_id)}.json"
    found: List[dict] = []
    if not SNAPSHOTS_DIR.is_dir():
        return found
    for day_dir in sorted(SNAPSHOTS_DIR.iterdir(), reverse=True):
        path = day_dir / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ts = _parse_ts(payload.get("timestamp"))
        if ts is None:
            continue
        payload["_timestamp"] = ts
        found.append(payload)
    found.sort(key=lambda p: p["_timestamp"], reverse=True)
    return found


def latest_snapshot_before(game_id: str,
                           before: datetime) -> Optional[dict]:
    """Latest snapshot with timestamp strictly before `before`.

    Grading rule: the latest bettable pregame state.
    """
    for snap in iter_snapshots(game_id):
        if snap["_timestamp"] < before:
            return snap
    return None


def cleanup_snapshots(retention_days: int = RETENTION_DAYS) -> int:
    """Delete snapshot day-dirs older than retention_days. Returns count."""
    if not SNAPSHOTS_DIR.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc).date() - timedelta(
        days=retention_days)
    removed = 0
    for day_dir in SNAPSHOTS_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            for path in sorted(day_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                    removed += 1
            day_dir.rmdir()
    return removed
