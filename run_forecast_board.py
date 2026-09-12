#!/usr/bin/env python3
"""Forecast-first NFL board entrypoint.

1. Load/replay the three family models (winner / margin / total).
2. Universe = today's official nflverse schedule (incl. unpriced games).
3. Build price-free forecasts, attach Bovada + Polymarket references.
4. Print the three-section board, export versioned forecast history, post
   Discord when enabled.

Usage:
    python run_forecast_board.py
    python run_forecast_board.py --rebuild-state --discord
    python run_forecast_board.py --date 2026-09-13
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from nfl_props import config
from nfl_props.forecast_board import build_board, detect_changes
from nfl_props.forecast_output import (append_ledger, export_history,
                                       latest_history, render_board)
from nfl_props.forecasting import load_forecast_state, rebuild_forecast_state
from nfl_props.schedule import current_day_games
from nfl_props.utils import log


def _parse_now(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
    ap.add_argument("--now", default=None, help="override now (ISO UTC)")
    args = ap.parse_args()

    config.ensure_dirs()
    now = _parse_now(args.now) or datetime.now(timezone.utc)

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

    prev = latest_history()
    changes = detect_changes(forecasts, prev)

    print(render_board(forecasts, summary, now=now))

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
        result = send_forecast(webhook, forecasts, summary, now=now)
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
