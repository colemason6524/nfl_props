#!/usr/bin/env python3
"""Late-pregame injury refresh (NFL): pull -> rebuild state -> board, ONE process.

This closes the gap between the last scheduled board (Sun 11:00 ET) and
kickoff: a fresh injury pull is folded into the SAME forecast state the
board is priced from, so QB-out substitution and injury debits reflect
game-day inactives. Run by the nfl-pregame-refresh systemd timer
(Sun 09:30 America/Detroit, additive to the existing board timers).

Order of operations (all in-process, no stale snapshot window):
  1. Pull injuries via personnel_feed.fetch_and_store() -> personnel.json.
  2. rebuild_forecast_state() -- reads the JUST-WRITTEN cache, so the
     state's personnel snapshot and the board's notes/debits agree.
  3. Regenerate the board exactly like run_forecast_board --rebuild-state
     --discord (same builders, same exporters, same Discord sender),
     minus the redundant second rebuild.

Fail-open / safety contract:
  - PULL FAILURE -> exit 2, NO board write, NO Discord. A stale or
    missing cache must never be re-published as fresh.
  - STALE GUARD -> after the pull, if the cache is still older than
    --max-age-hours, same as pull failure (exit 2, no board write).
  - Empty schedule (no games today) -> rebuild state only, exit 0.
  - Discord/odds failures behave exactly like run_forecast_board.

Usage:
    python scripts/pregame_refresh.py [--discord] [--date YYYY-MM-DD]
        [--max-age-hours 20] [--no-polymarket] [--no-export] [--now ISO]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props import config
from nfl_props.forecast_board import build_board, detect_changes
from nfl_props.forecast_output import (append_ledger, export_history,
                                       latest_history, render_board)
from nfl_props.forecasting import rebuild_forecast_state
from nfl_props.schedule import current_day_games
from nfl_props.sources.personnel_feed import (
    PersonnelFeedError,
    fetch_and_store,
    personnel_is_fresh,
    summarize,
)
from nfl_props.utils import log


def _parse_now(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--discord", action="store_true")
    ap.add_argument("--date", default=None)
    ap.add_argument("--now", default=None)
    ap.add_argument("--max-age-hours", type=float, default=20.0)
    ap.add_argument("--no-refresh-odds", action="store_true")
    ap.add_argument("--no-polymarket", action="store_true")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    now = _parse_now(args.now) or datetime.now(timezone.utc)

    # 1. Pull injuries. Failure -> NO board write (exit 2).
    try:
        teams = fetch_and_store(now=now)
    except PersonnelFeedError as exc:
        log(f"[pregame] injury pull failed ({exc}); no board write")
        return 2
    except Exception as exc:  # fail-open on anything unexpected
        log(f"[pregame] injury pull failed ({exc}); no board write")
        return 2
    for line in summarize(teams):
        log(f"[pregame] personnel {line}")

    # 2. Stale guard: the cache we just wrote must read fresh.
    if not personnel_is_fresh(max_age_hours=args.max_age_hours, now=now):
        log("[pregame] personnel cache still stale after pull; "
            "no board write")
        return 2

    # 3. Rebuild state in-process (reads the just-written cache: one
    # snapshot shared by QB substitution, debits, and notes) and regen
    # the board exactly like run_forecast_board --rebuild-state --discord.
    state = rebuild_forecast_state()
    log(f"[pregame] state rebuilt personnel_available="
        f"{state.get('personnel_available')} "
        f"snapshot_teams={len(state.get('personnel') or {})}")

    schedule_games = current_day_games(now=now, date_override=args.date)
    log(f"[pregame] current-day schedule games: {len(schedule_games)}")
    if not schedule_games:
        log("[pregame] no scheduled games today; state refreshed only")
        return 0

    bovada_games, diags = [], {"skipped": True}
    try:
        from nfl_props.sources.bovada import fetch_live_games
        try:
            bovada_games, diags = fetch_live_games(
                refresh=not args.no_refresh_odds,
                fetch_team_totals=False, now=now)
        except Exception as exc:
            log(f"[pregame] bovada unavailable ({exc}); forecasts unpriced")
            diags = {"error": str(exc)}
    except Exception as exc:
        log(f"[pregame] bovada import failed ({exc})")
        diags = {"error": str(exc)}

    poly_refs, poly_diags = {}, {"skipped": True}
    if not args.no_polymarket:
        try:
            from nfl_props.sources.polymarket import fetch_nfl_references
            poly_refs, poly_diags = fetch_nfl_references(now=now)
        except Exception as exc:
            log(f"[pregame] polymarket skipped ({exc})")
            poly_diags = {"error": str(exc)}

    games_df = None
    try:
        from nfl_props.sources.nflverse import load_processed
        games_df, _ = load_processed()
    except Exception as exc:
        log(f"[pregame] games store unavailable for rivalry ({exc})")

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
        log(f"[pregame] history -> {path}")

    send = args.discord or os.environ.get(
        "NFL_SEND_DISCORD", "").lower() in ("1", "true", "yes")
    if send:
        webhook = os.environ.get("NFL_DISCORD_WEBHOOK_URL", "")
        from nfl_props.notifiers.forecast_discord import send_forecast
        result = send_forecast(webhook, forecasts, summary, now=now)
        if result.ok:
            log("[pregame] discord ok")
        else:
            log(f"[pregame] discord failed: "
                f"{result.error or result.status_code}")
            return 1

    log(f"[pregame] games={summary['games']} available={summary['available']} "
        f"priced={summary['priced']} changes={len(changes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
