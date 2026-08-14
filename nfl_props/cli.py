"""CLI: refresh-data | build | rebuild-state."""
from __future__ import annotations

import argparse
import sys

from . import config
from .utils import log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="nfl_props.cli")
    sub = ap.add_subparsers(dest="command", required=True)

    p_refresh = sub.add_parser("refresh-data",
                               help="download nflverse pbp + games.csv")
    p_refresh.add_argument("--refresh-all", action="store_true",
                           help="re-download completed-season pbp files too")

    sub.add_parser("build", help="build canonical parquet store + diagnostics")
    sub.add_parser("rebuild-state",
                   help="replay ratings and write live_state.json")
    sub.add_parser("rebuild-state-v2",
                   help="replay v2 shadow ratings and write live_state_v2_shadow.json")

    args = ap.parse_args(argv)
    config.ensure_dirs()

    if args.command == "refresh-data":
        from .sources.nflverse import refresh_data
        refresh_data(refresh_all=args.refresh_all)
    elif args.command == "build":
        from .sources.nflverse import build
        build()
    elif args.command == "rebuild-state":
        from .ratings.epa import rebuild_state
        state = rebuild_state()
        log(f"[cli] live state written (as_of={state['as_of']}, "
            f"{len(state['teams'])} teams)")
    elif args.command == "rebuild-state-v2":
        from .ratings.v2 import rebuild_state_v2
        shadow = rebuild_state_v2()
        log(f"[cli] v2 shadow state written (as_of={shadow['as_of']}, "
            f"{len(shadow['teams'])} teams, {len(shadow['qbs'])} qbs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
