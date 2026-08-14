#!/usr/bin/env python3
"""Grade exported board snapshots against nflverse final scores.

For every (matchup, market, side) it grades only the LATEST snapshot taken
before kickoff, so line moves across the week don't double-count. Pushes are
a wash. Ungraded rows (game not final yet in games.csv) are reported, not
dropped silently — unresolved until the data refreshes is normal.

Default scope: Core + Lean (the actionable tiers). --include-watch adds the
research tier.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from nfl_props import config
from nfl_props.config import BACKTESTS_DIR, HISTORY_DIR
from nfl_props.utils import log


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_plays(history_dir: Path, since: str, include_watch: bool) -> list[dict]:
    tiers = {"Core", "Lean"} | ({"Watch"} if include_watch else set())
    latest: dict[tuple, dict] = {}
    for path in sorted(history_dir.glob("nfl_board_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log(f"[grade] skipping unreadable {path.name}: {exc}")
            continue
        generated = _parse_ts(payload["generated_at_utc"])
        if since and generated.date().isoformat() < since:
            continue
        for c in payload.get("candidates", []):
            if c.get("tier") not in tiers:
                continue
            if not c.get("start_time_utc"):
                continue
            kickoff = _parse_ts(c["start_time_utc"])
            if generated > kickoff:
                continue  # in-play/stale snapshot; grade pregame opinions only
            key = (c["away"], c["home"], kickoff.date().isoformat(),
                   c["market"], c["side"])
            prev = latest.get(key)
            if prev is None or generated > prev["_generated"]:
                row = dict(c)
                row["_generated"] = generated
                row["_kickoff"] = kickoff
                latest[key] = row
    return list(latest.values())


def load_shadow_projections(history_dir: Path, since: str) -> list[dict]:
    """Latest pre-kickoff v1/v2 score projection for each game."""
    latest: dict[tuple, dict] = {}
    for path in sorted(history_dir.glob("nfl_board_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log(f"[grade] skipping unreadable {path.name}: {exc}")
            continue
        generated = _parse_ts(payload["generated_at_utc"])
        if since and generated.date().isoformat() < since:
            continue
        for projection in payload.get("summary", {}).get("projections", []):
            required = ("mu_home", "mu_away", "mu_home_v2", "mu_away_v2",
                        "start_time_utc", "away", "home")
            if any(projection.get(k) is None for k in required):
                continue
            kickoff = _parse_ts(projection["start_time_utc"])
            if generated > kickoff:
                continue
            key = (projection["away"], projection["home"],
                   kickoff.date().isoformat())
            prev = latest.get(key)
            if prev is None or generated > prev["_generated"]:
                row = dict(projection)
                row["_generated"] = generated
                row["_kickoff"] = kickoff
                latest[key] = row
    return list(latest.values())


def match_game(games: pd.DataFrame, away: str, home: str,
               kickoff: datetime) -> pd.Series | None:
    for delta in (0, -1, 1):
        day = (kickoff + timedelta(days=delta)).date().isoformat()
        rows = games[(games["away_team"] == away) & (games["home_team"] == home)
                     & (games["gameday"] == day)]
        if len(rows):
            return rows.iloc[0]
    return None


def grade_play(play: dict, game: pd.Series) -> str | None:
    home_pts, away_pts = game["home_score"], game["away_score"]
    if pd.isna(home_pts) or pd.isna(away_pts):
        return None
    market, side, line = play["market"], play["side"], play.get("line")

    if market == "MONEYLINE":
        team_pts, opp_pts = ((home_pts, away_pts) if side == play["home"]
                             else (away_pts, home_pts))
        if team_pts == opp_pts:
            return "push"
        return "win" if team_pts > opp_pts else "loss"

    if market == "SPREAD":
        team_pts, opp_pts = ((home_pts, away_pts) if side == play["home"]
                             else (away_pts, home_pts))
        adj = team_pts + float(line)
        if adj == opp_pts:
            return "push"
        return "win" if adj > opp_pts else "loss"

    if market == "GAME_TOTAL":
        total = home_pts + away_pts
        if total == float(line):
            return "push"
        hit = total > float(line) if side == "OVER" else total < float(line)
        return "win" if hit else "loss"

    if market == "TEAM_TOTAL":
        team, direction = side.split()
        pts = home_pts if team == play["home"] else away_pts
        if pts == float(line):
            return "push"
        hit = pts > float(line) if direction == "OVER" else pts < float(line)
        return "win" if hit else "loss"
    return None


def grade_shadow_projection(projection: dict, game: pd.Series) -> dict | None:
    home_pts, away_pts = game["home_score"], game["away_score"]
    if pd.isna(home_pts) or pd.isna(away_pts):
        return None
    home_pts, away_pts = float(home_pts), float(away_pts)
    actual_margin = home_pts - away_pts
    actual_total = home_pts + away_pts
    mu_h1, mu_a1 = float(projection["mu_home"]), float(projection["mu_away"])
    mu_h2 = float(projection["mu_home_v2"])
    mu_a2 = float(projection["mu_away_v2"])
    return {
        "away": projection["away"],
        "home": projection["home"],
        "kickoff_utc": projection["start_time_utc"],
        "mu_home_v1": mu_h1,
        "mu_away_v1": mu_a1,
        "mu_home_v2": mu_h2,
        "mu_away_v2": mu_a2,
        "points_mae_v1": (abs(home_pts - mu_h1) + abs(away_pts - mu_a1)) / 2.0,
        "points_mae_v2": (abs(home_pts - mu_h2) + abs(away_pts - mu_a2)) / 2.0,
        "margin_mae_v1": abs(actual_margin - (mu_h1 - mu_a1)),
        "margin_mae_v2": abs(actual_margin - (mu_h2 - mu_a2)),
        "total_mae_v1": abs(actual_total - (mu_h1 + mu_a1)),
        "total_mae_v2": abs(actual_total - (mu_h2 + mu_a2)),
        "final": f"{projection['away']} {int(away_pts)} @ "
                 f"{projection['home']} {int(home_pts)}",
    }


def summarize_shadow(rows: list[dict]) -> list[str]:
    def mean(key: str) -> float:
        return sum(float(r[key]) for r in rows) / len(rows)

    p1, p2 = mean("points_mae_v1"), mean("points_mae_v2")
    m1, m2 = mean("margin_mae_v1"), mean("margin_mae_v2")
    t1, t2 = mean("total_mae_v1"), mean("total_mae_v2")
    return [
        f"  {'model':<8} {'points MAE':>10} {'margin MAE':>11} {'total MAE':>10}",
        f"  {'v1':<8} {p1:>10.3f} {m1:>11.3f} {t1:>10.3f}",
        f"  {'v2':<8} {p2:>10.3f} {m2:>11.3f} {t2:>10.3f}",
        f"  {'v2-v1':<8} {p2 - p1:>+10.3f} {m2 - m1:>+11.3f} "
        f"{t2 - t1:>+10.3f}",
    ]


def summarize(rows: list[dict], group_key) -> list[str]:
    out = []
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(group_key(r), []).append(r)
    for name in sorted(groups):
        g = groups[name]
        w = sum(1 for r in g if r["result"] == "win")
        l = sum(1 for r in g if r["result"] == "loss")
        p = sum(1 for r in g if r["result"] == "push")
        pnl = sum((r["decimal"] - 1.0) if r["result"] == "win"
                  else (-1.0 if r["result"] == "loss" else 0.0) for r in g)
        roi = pnl / (w + l) if (w + l) else 0.0
        out.append(f"  {name:<12} {w}-{l}-{p}  pnl {pnl:+.2f}u  roi {roi:+.1%}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="grade nfl_props history")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--since", default="", help="YYYY-MM-DD snapshot floor")
    ap.add_argument("--include-watch", action="store_true")
    ap.add_argument("--no-refresh", action="store_true",
                    help="grade against the already-downloaded games.csv")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    if not args.no_refresh:
        from nfl_props.sources.nflverse import refresh_data
        refresh_data(seasons=[])  # games.csv only; pbp not needed to grade
    from nfl_props.sources.nflverse import load_raw_games
    games = load_raw_games()

    plays = load_plays(args.history_dir, args.since, args.include_watch)
    log(f"[grade] {len(plays)} latest pre-kickoff plays in scope")

    graded, pending, unmatched = [], [], []
    for play in plays:
        game = match_game(games, play["away"], play["home"], play["_kickoff"])
        if game is None:
            unmatched.append(play)
            continue
        result = grade_play(play, game)
        if result is None:
            pending.append(play)
            continue
        row = {k: v for k, v in play.items() if not k.startswith("_")}
        row["result"] = result
        row["final"] = f"{play['away']} {int(game['away_score'])} @ " \
                       f"{play['home']} {int(game['home_score'])}"
        graded.append(row)

    shadow = load_shadow_projections(args.history_dir, args.since)
    shadow_graded, shadow_pending, shadow_unmatched, shadow_excluded = [], [], [], []
    for projection in shadow:
        game = match_game(games, projection["away"], projection["home"],
                          projection["_kickoff"])
        if game is None:
            shadow_unmatched.append(projection)
            continue
        if str(game.get("game_type", "REG")) != "REG":
            shadow_excluded.append(projection)
            continue
        row = grade_shadow_projection(projection, game)
        if row is None:
            shadow_pending.append(projection)
        else:
            shadow_graded.append(row)

    lines = [
        "nfl_props grade report",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        f"scope: Core+Lean{'+Watch' if args.include_watch else ''}"
        f"{' since ' + args.since if args.since else ''}",
        f"graded {len(graded)} | pending {len(pending)} | "
        f"unmatched {len(unmatched)}",
    ]
    if graded:
        lines.append("\nby tier:")
        lines += summarize(graded, lambda r: r["tier"])
        lines.append("by market:")
        lines += summarize(graded, lambda r: r["market"])
        lines.append("\nplays:")
        for r in sorted(graded, key=lambda r: (r["tier"], r["market"])):
            line = f" {r['line']}" if r.get("line") is not None else ""
            lines.append(f"  [{r['tier']:<5}] {r['away']} @ {r['home']} "
                         f"{r['market']} {r['side']}{line} "
                         f"({r['american']:+d}) -> {r['result'].upper()} "
                         f"({r['final']})")
    if unmatched:
        lines.append(f"\nunmatched games (check team map / dates): "
                     f"{[(p['away'], p['home']) for p in unmatched[:10]]}")
    lines.append(
        f"\nv2 shadow projections: graded {len(shadow_graded)} | "
        f"pending {len(shadow_pending)} | unmatched {len(shadow_unmatched)} | "
        f"excluded non-REG {len(shadow_excluded)}")
    if shadow_graded:
        lines += summarize_shadow(shadow_graded)

    report = "\n".join(lines)
    print(report)
    if not args.no_export and (graded or shadow_graded):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = BACKTESTS_DIR / f"grade_{stamp}.txt"
        path.write_text(report)
        if graded:
            (BACKTESTS_DIR / f"grade_{stamp}_rows.json").write_text(
                json.dumps(graded, indent=1, default=str))
        if shadow_graded:
            (BACKTESTS_DIR / f"grade_{stamp}_v2_rows.json").write_text(
                json.dumps(shadow_graded, indent=1, default=str))
        log(f"[grade] report -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
