#!/usr/bin/env python3
"""Forecast-first NFL board entrypoint.

0. Pull injuries (ESPN, nflverse fallback) before rebuilding state, so
   QB-out substitution and injury debits reflect game-day inactives --
   atomic with the board build, no stale-cache window (same contract as
   scripts/pregame_refresh.py: pull failure -> exit 2, no board write).
1. Load/replay the three family models (winner / margin / total).
2. Universe = today's official nflverse schedule (incl. unpriced games).
3. Build price-free forecasts, attach Bovada + Polymarket references.
4. Print the three-section board, export versioned forecast history, post
   Discord when enabled.
5. Wave mode (--wave): scheduled mid-day reprint showing ONLY games with
   kickoff > now + 15 min ("remaining slate"), labeled
   "WAVE -- Remaining games (HH:MM ET)". Forces a fresh injury pull
   (fail-closed), skips when fewer than 3 games remain, and writes
   per-game pregame snapshots with source="wave".

Usage:
    python run_forecast_board.py
    python run_forecast_board.py --rebuild-state --discord
    python run_forecast_board.py --date 2026-09-13
    python run_forecast_board.py --wave --discord   # Sunday 2:30pm ET wave
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from nfl_props import config
from nfl_props.forecast_board import (_priced, _status_counts, build_board,
                                      detect_changes)
from nfl_props.forecast_output import (append_ledger, export_history,
                                       latest_history, render_board)
from nfl_props.forecasting import load_forecast_state, rebuild_forecast_state
from nfl_props.schedule import current_day_games
from nfl_props.snapshots import write_board_snapshots
from nfl_props.sources.personnel_feed import (
    PersonnelFeedError,
    fetch_and_store,
    personnel_is_fresh,
    summarize,
)
from nfl_props.utils import log

ET = ZoneInfo("America/New_York")
WAVE_PREGAME_CUTOFF_MINUTES = 15
WAVE_MIN_GAMES = 3


def _parse_now(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_ts(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pull_personnel(now: datetime, max_age_hours: float) -> int:
    """Fresh injury pull, fail-closed. Returns 0 on success, 2 on failure."""
    # Injury pull FIRST, then rebuild: the rebuilt state reads the
    # just-written cache, so debits/QB substitution and the board
    # agree. Closes the Thursday-board (TNF) gap that the Sunday
    # pregame refresh leaves behind.
    try:
        teams = fetch_and_store(now=now)
    except PersonnelFeedError as exc:
        log(f"[forecast] injury pull failed ({exc}); no board write")
        return 2
    except Exception as exc:  # fail-open on anything unexpected
        log(f"[forecast] injury pull failed ({exc}); no board write")
        return 2
    for line in summarize(teams):
        log(f"[forecast] personnel {line}")
    if not personnel_is_fresh(max_age_hours=max_age_hours, now=now):
        log("[forecast] personnel cache still stale after pull; "
            "no board write")
        return 2
    return 0


def _upcoming_forecasts(forecasts, now: datetime):
    """Filter to forecasts for games not yet started (15-min pregame buffer).

    Started/in-progress games stay in history for grading continuity but
    are excluded from the wave board output. Unparseable start times are
    kept (fail-open for display), mirroring the NHL template.
    """
    cutoff = now + timedelta(minutes=WAVE_PREGAME_CUTOFF_MINUTES)
    kept = []
    for fc in forecasts:
        start = _parse_ts(fc.kickoff_utc)
        if start is None:
            kept.append(fc)
            continue
        if start <= cutoff:
            continue
        kept.append(fc)
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description="nfl_props forecast-first board")
    ap.add_argument("--rebuild-state", action="store_true",
                    help="refit the family models from the store first")
    ap.add_argument("--no-refresh-odds", action="store_true")
    ap.add_argument("--no-polymarket", action="store_true")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--discord", action="store_true")
    ap.add_argument("--date", default=None,
                    help="override ET calendar day (YYYY-MM-DD)")
    ap.add_argument("--no-injury-pull", action="store_true",
                    help="skip the pre-build injury pull (offline/test use)")
    ap.add_argument("--injury-max-age-hours", type=float, default=20.0,
                    help="stale guard for the pulled personnel cache")
    ap.add_argument("--now", default=None, help="override now (ISO UTC)")
    ap.add_argument("--wave", action="store_true",
                    help="wave board: full refresh for remaining games only "
                         "(kickoff > now + 15 min); forces a fresh injury "
                         "pull (fail-closed); skips when fewer than "
                         f"{WAVE_MIN_GAMES} games remain")
    args = ap.parse_args()

    config.ensure_dirs()
    now = _parse_now(args.now) or datetime.now(timezone.utc)

    if args.rebuild_state or args.wave:
        if not args.no_injury_pull:
            rc = _pull_personnel(now, args.injury_max_age_hours)
            if rc:
                return rc
        if args.rebuild_state:
            rebuild_forecast_state()
    state = load_forecast_state()

    schedule_games = current_day_games(now=now, date_override=args.date)
    log(f"[forecast] current-day schedule games: {len(schedule_games)}")
    if not schedule_games:
        log("[forecast] no scheduled games today; nothing to publish")

    bovada_games, diags = [], {"skipped": True}
    if schedule_games:
        from nfl_props.sources.bovada import fetch_live_games
        try:
            bovada_games, diags = fetch_live_games(
                refresh=not args.no_refresh_odds,
                fetch_team_totals=False, now=now)
        except Exception as exc:  # primary source: continue unpriced
            log(f"[forecast] bovada unavailable ({exc}); forecasts unpriced")
            diags = {"error": str(exc)}

    poly_refs, poly_diags = {}, {"skipped": True}
    if schedule_games and not args.no_polymarket:
        try:
            from nfl_props.sources.polymarket import fetch_nfl_references
            poly_refs, poly_diags = fetch_nfl_references(now=now)
        except Exception as exc:  # secondary source: never block
            log(f"[forecast] polymarket skipped ({exc})")
            poly_diags = {"error": str(exc)}

    games_df = None
    try:
        from nfl_props.sources.nflverse import load_processed
        games_df, _ = load_processed()
    except Exception as exc:  # rivalry features stay neutral
        log(f"[forecast] games store unavailable for rivalry ({exc})")

    forecasts, summary = build_board(
        state, schedule_games, bovada_games=bovada_games,
        poly_refs=poly_refs, games_df=games_df)

    if args.wave:
        forecasts = _upcoming_forecasts(forecasts, now)
        log(f"[forecast] wave filter: {len(forecasts)} remaining games")
        if len(forecasts) < WAVE_MIN_GAMES:
            # Spec: fewer than 3 remaining games -> skip the wave board;
            # delta alerts cover the stragglers.
            log("[forecast] wave skipped: fewer than "
                f"{WAVE_MIN_GAMES} games remain")
            return 0
        summary = {
            "games": len(forecasts),
            "available": sum(1 for f in forecasts if f.available),
            "unavailable": sum(1 for f in forecasts if not f.available),
            "unavailable_games": [f"{f.away} @ {f.home}" for f in forecasts
                                  if not f.available],
            "priced": sum(1 for f in forecasts if _priced(f)),
            "unpriced": sum(1 for f in forecasts if not _priced(f)),
            "status_counts": _status_counts(forecasts),
        }

    prev = latest_history()
    changes = detect_changes(forecasts, prev)

    results = None
    try:  # footer record comes from the grader; never block the board on it
        from grade_forecast import rolling_results
        results = rolling_results(now=now)
    except Exception as exc:  # noqa: BLE001 - footer is best-effort
        log(f"[forecast] rolling results unavailable ({exc})")

    print(render_board(forecasts, summary, now=now, wave=args.wave,
                       changes=changes, results=results))

    try:
        n_snap = write_board_snapshots(
            forecasts, "wave" if args.wave else "early", now=now)
        log(f"[forecast] snapshots written: {n_snap} "
            f"({'wave' if args.wave else 'early'})")
    except Exception as exc:  # snapshots must never kill the board
        log(f"[forecast] snapshot write failed ({exc})")

    diagnostics = {"bovada": diags, "polymarket": poly_diags}
    if not args.no_export:
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        diag_path = config.DIAGNOSTICS_DIR / f"forecast_coverage_{stamp}.json"
        diag_path.write_text(json.dumps(diagnostics, indent=2, default=str),
                             encoding="utf-8")
        path = export_history(forecasts, summary, diagnostics, changes)
        append_ledger(forecasts, summary)
        log(f"[forecast] history -> {path}")

    send = args.discord or os.environ.get(
        "NFL_SEND_DISCORD", "").lower() in ("1", "true", "yes")
    if send:
        webhook = os.environ.get("NFL_DISCORD_WEBHOOK_URL", "")
        from nfl_props.notifiers.forecast_discord import send_forecast
        result = send_forecast(webhook, forecasts, summary, now=now,
                               wave=args.wave, changes=changes)
        if result.ok:
            log("[forecast] discord ok")
        else:
            log(f"[forecast] discord failed: {result.error or result.status_code}")
            return 1

    log(f"[forecast] games={summary['games']} available={summary['available']} "
        f"priced={summary['priced']} changes={len(changes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
