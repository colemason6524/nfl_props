"""Manual personnel-cache updater (Phase A, NFL only).

Upserts one team's entry in `data/processed/personnel.json`, creating the
file (or its parent dirs) if missing. Reads the existing file when present,
validates `qb_status`, and writes atomically (tmp file + rename). Teams or
keys not mentioned are left alone; unknown teams start from NEUTRAL defaults.

Usage:
    python -m nfl_props.scripts.update_personnel --team KC \\
        --qb-status confirmed --source manual
    python -m nfl_props.scripts.update_personnel --team KC \\
        --qb-status questionable --key-out WR1 --key-out WR2 \\
        --backup-qb-id KC_BACKUP_01 --source manual
    printf 'KC,confirmed\\nBUF,questionable\\n' | \\
        python -m nfl_props.scripts.update_personnel --table - --source manual

`--table` accepts a CSV file (or `-` for stdin) with rows
`TEAM,qb_status[,backup_qb_id]`; other fields come from the flags.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from nfl_props.config import PROCESSED_DIR
from nfl_props.sources.personnel import (
    NEUTRAL,
    PERSONNEL_PATH,
    VALID_QB_STATUSES,
    normalize_key_out,
)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upsert a personnel-cache entry (fail-open manual feed).")
    parser.add_argument("--team", help="Team abbreviation, e.g. KC")
    parser.add_argument("--qb-status", default="unknown",
                        choices=VALID_QB_STATUSES,
                        help="QB status (default: unknown)")
    parser.add_argument("--key-out", dest="key_out", action="append",
                        default=[],
                        help="Key-out position; repeatable (e.g. --key-out WR1)")
    parser.add_argument("--backup-qb-id", default=None,
                        help="Backup QB id (default: None)")
    parser.add_argument("--source", default="manual",
                        help="Source label (default: manual)")
    parser.add_argument("--table",
                        help="CSV file (or - for stdin) with "
                             "TEAM,qb_status[,backup_qb_id] rows")
    parser.add_argument("--path", default=str(PERSONNEL_PATH),
                        help="Personnel cache path (default: processed dir)")
    return parser.parse_args(argv)


def read_rows(args: argparse.Namespace):
    """Yield (team, qb_status, backup_qb_id) rows from flags and/or --table."""
    rows = []
    if args.table:
        handle = sys.stdin if args.table == "-" else open(args.table, newline="",
                                                          encoding="utf-8")
        with handle:
            reader = csv.reader(handle)
            for row in reader:
                if not row or not row[0].strip():
                    continue
                team = row[0].strip().upper()
                qb_status = row[1].strip().lower() if len(row) > 1 and row[1].strip() \
                    else args.qb_status
                if qb_status not in VALID_QB_STATUSES:
                    raise SystemExit(
                        f"error: invalid qb_status {qb_status!r}; "
                        f"expected one of {VALID_QB_STATUSES}")
                backup = row[2].strip() if len(row) > 2 and row[2].strip() else None
                rows.append((team, qb_status, backup))
    elif args.team:
        rows.append((args.team.strip().upper(), args.qb_status, args.backup_qb_id))
    else:
        raise SystemExit("pass --team TEAM or --table FILE")
    return rows


def upsert(cache: dict, team: str, qb_status: str,
           key_out: list, backup_qb_id, source: str) -> dict:
    if qb_status not in VALID_QB_STATUSES:
        raise ValueError(f"invalid qb_status {qb_status!r}; "
                         f"expected one of {VALID_QB_STATUSES}")
    entry = dict(cache.get(team, {})) if isinstance(cache.get(team), dict) else {}
    base = dict(NEUTRAL)
    base.update(entry)
    base["qb_status"] = qb_status
    if key_out:
        base["key_out"] = normalize_key_out(key_out)
    else:
        base["key_out"] = normalize_key_out(base.get("key_out"))
    if backup_qb_id is not None:
        base["backup_qb_id"] = backup_qb_id
    elif "backup_qb_id" not in base:
        base["backup_qb_id"] = None
    base["updated_at"] = datetime.now(timezone.utc).isoformat()
    base["source"] = source
    cache[team] = base
    return cache


def write_atomic(path: str, payload: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".personnel.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = read_rows(args)
    path = args.path
    try:
        with open(path, encoding="utf-8") as handle:
            cache = json.load(handle)
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    for team, qb_status, backup in rows:
        key_out = args.key_out
        try:
            cache = upsert(cache, team, qb_status, key_out, backup, args.source)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    write_atomic(path, cache)
    print(f"updated {len(rows)} team(s) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
