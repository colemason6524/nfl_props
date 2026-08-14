#!/usr/bin/env python3
"""Live NFL team-market board.

1. Load live model state (rebuild with --rebuild-state)
2. Pull Bovada NFL lines (spread / ML / game total + team totals when posted)
3. Model fair probabilities (market never inside fair) -> EV vs de-vigged book
4. Tier Core / Lean / Watch (EV capped window)
5. Print board, export history JSON, optional Discord Core digest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from nfl_props import config
from nfl_props.board import screen_games
from nfl_props.output import export_history, render_board
from nfl_props.ratings.epa import load_state, rebuild_state
from nfl_props.sources.bovada import fetch_live_games
from nfl_props.utils import log


def main() -> int:
    ap = argparse.ArgumentParser(description="nfl_props live board")
    ap.add_argument("--rebuild-state", action="store_true",
                    help="replay ratings from the canonical store first")
    ap.add_argument("--no-refresh-odds", action="store_true",
                    help="use cached Bovada JSON if present")
    ap.add_argument("--no-team-totals", action="store_true",
                    help="skip per-event team-total fetches")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--discord", action="store_true",
                    help="post Core digest (needs NFL_DISCORD_WEBHOOK_URL)")
    args = ap.parse_args()

    config.ensure_dirs()
    if args.rebuild_state:
        rebuild_state()
    state = load_state()

    games, diags = fetch_live_games(refresh=not args.no_refresh_odds,
                                    fetch_team_totals=not args.no_team_totals)
    log(f"[board] bovada games: {len(games)} "
        f"(ml={diags['with_moneyline']} sp={diags['with_spread']} "
        f"gt={diags['with_game_total']} tt={diags['team_totals_found']})")
    if diags.get("team_totals_found") == 0 and not args.no_team_totals:
        log("[board] no team totals posted yet (Bovada usually posts them "
            "during game week)")

    candidates, summary = screen_games(games, state)
    print(render_board(candidates, summary))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    diag_path = config.DIAGNOSTICS_DIR / f"bovada_coverage_{stamp}.json"
    diag_path.write_text(json.dumps(diags, indent=2, default=str))

    if not args.no_export:
        history_path = export_history(candidates, summary,
                                      [g.as_dict() for g in games], diags)
        log(f"[board] history -> {history_path}")

    send = args.discord or os.environ.get("SEND_DISCORD", "").lower() in (
        "1", "true", "yes")
    if send:
        webhook = os.environ.get("NFL_DISCORD_WEBHOOK_URL", "")
        from nfl_props.notifiers.discord import core_embeds, send_discord_embeds
        result = send_discord_embeds(webhook, core_embeds(candidates))
        if result.ok:
            log("[board] discord ok")
        else:
            log(f"[board] discord failed: {result.error or result.status_code}")
            return 1

    log(f"[board] Core={summary['core']} Lean={summary['lean']} "
        f"Watch={summary['watch']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
