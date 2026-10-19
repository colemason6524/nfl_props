#!/usr/bin/env python3
"""Refresh the nflverse depth-chart index (cache-first, fail-open).

Run before the board so `personnel_feed` can attribute each OUT player to
starter or backup. Never fails the calling task: download/parse errors are
logged and exit 0 (the caller keeps the previous index, and an unknown
`starter` flag degrades to the pre-starter-aware behavior).

Usage:
    python scripts/refresh_depth_chart.py [--season 2026] [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props import config
from nfl_props.sources import depth
from nfl_props.utils import log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--force", action="store_true",
                    help="ignore the cached raw CSV and re-download")
    args = ap.parse_args(argv)
    try:
        config.ensure_dirs()
    except Exception as exc:  # fail-open
        log(f"depth: could not create data dirs ({exc})")
    try:
        index = depth.refresh(season=args.season, force=args.force)
        log(f"depth: {len(index.get('teams') or {})} teams on disk")
    except Exception as exc:  # refresh() is already fail-open; belt+braces
        log(f"depth: refresh raised ({exc}); continuing without it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
