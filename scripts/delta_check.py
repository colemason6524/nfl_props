#!/usr/bin/env python3
"""Smart delta alert check (NFL, Phase 1).

Pulls fresh injury data, recomputes each game on the last printed board,
and posts a Discord delta alert ONLY when late news materially changes
pick value (Tier 1: edge shift / flip / key player; Tier 2: margin move
> 5 pts -> re-board). Silent otherwise.

Fail-open contract: any failure (no fresh board, pull failure, recompute
error) -> exit 0 with NO alert. Never alert on stale data.

When an alert posts, pregame snapshots (source="delta") are written for
the firing games so grading uses the updated picks.

Usage:
    python scripts/delta_check.py [--discord] [--now ISO] [--max-age-hours 12]

The --discord flag posts to NFL_DISCORD_WEBHOOK_URL when a trigger fires.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props import config
from nfl_props.delta import run_delta_check
from nfl_props.snapshots import write_board_snapshots
from nfl_props.utils import log


def _parse_now(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--discord", action="store_true",
                    help="post to Discord when a trigger fires")
    ap.add_argument("--now", default=None, help="override now (ISO UTC)")
    ap.add_argument("--max-age-hours", type=float, default=12.0,
                    help="max age of the printed board to diff against")
    ap.add_argument("--no-refresh-odds", action="store_true",
                    help="skip fresh Bovada pull (offline/test use)")
    args = ap.parse_args(argv)

    config.ensure_dirs()
    now = _parse_now(args.now) or datetime.now(timezone.utc)

    webhook = os.environ.get("NFL_DISCORD_WEBHOOK_URL", "") if args.discord else ""
    if args.discord and not webhook:
        log("[delta] --discord without NFL_DISCORD_WEBHOOK_URL; "
            "running in dry-run mode")

    result = run_delta_check(
        now=now, discord=args.discord, webhook_url=webhook,
        max_age_hours=args.max_age_hours,
        refresh_odds=not args.no_refresh_odds)

    log(f"[delta] checked={result.games_checked} "
        f"firing={result.games_firing} posted={result.posted}")
    if result.error:
        log(f"[delta] note: {result.error}")
    if result.posted and result.firing_forecasts:
        # The alert updated the bettable state: record the latest pregame
        # snapshot (source="delta") so grading uses the updated picks.
        try:
            n_snap = write_board_snapshots(
                result.firing_forecasts, "delta", now=now)
            log(f"[delta] snapshots written: {n_snap} (delta)")
        except Exception as exc:  # snapshots must never fail the check
            log(f"[delta] snapshot write failed ({exc})")
    if result.message and not result.posted:
        # Dry run (or discord disabled): print what WOULD have posted.
        print(result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
