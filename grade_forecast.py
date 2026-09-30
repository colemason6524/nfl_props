#!/usr/bin/env python3
"""Grade forecast-first snapshots: forecast quality + reference ROI.

Separate from the legacy grade.py. Reads outputs/forecast_history/
nfl_forecast_*.json, selects the latest pre-kickoff forecast per stable
forecast id, and reports:

  forecast  : winner accuracy (ties excluded), Brier, log loss, margin/total MAE
  reference : flat-1u ROI at captured prices, by family / source / label

Forecast accuracy and reference value are never pooled. NFL ties settle the
moneyline reference as a push (stake returned), never a loss.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from nfl_props import config
from nfl_props.config import FORECAST_BACKTESTS_DIR, FORECAST_HISTORY_DIR
from nfl_props.utils import log
from nfl_props.version import (FORECAST_GRADING_VERSION,
                               FORECAST_MODEL_VERSION)

WEEKLY_TZ = ZoneInfo(os.environ.get("NFL_WEEKLY_TZ", "America/New_York"))
DELIVERY_MARKER = FORECAST_BACKTESTS_DIR / "grade_weekly_delivered.json"
WEEKLY_FAMILIES = (
    ("moneyline", "Moneylines"),
    ("spread", "Spreads"),
    ("total", "Totals"),
)


def _parse_ts(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def select_latest_pregame(paths: List[Path]) -> Dict[str, dict]:
    chosen: Dict[str, dict] = {}
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log(f"[grade_forecast] skipping {path.name}: {exc}")
            continue
        if payload.get("model_version") != FORECAST_MODEL_VERSION:
            continue
        generated = _parse_ts(payload.get("generated_at_utc"))
        if generated is None:
            continue
        for fc in payload.get("forecasts", []):
            if not fc.get("available"):
                continue
            kickoff = _parse_ts(fc.get("kickoff_utc"))
            if kickoff is None or generated > kickoff:
                continue
            fid = fc.get("forecast_id")
            prev = chosen.get(fid)
            if prev is None or generated > prev["_generated"]:
                row = dict(fc)
                row["_generated"] = generated
                row["_kickoff"] = kickoff
                chosen[fid] = row
    return chosen


def match_final(games: pd.DataFrame, away: str, home: str,
                kickoff: datetime) -> Optional[pd.Series]:
    for delta in (0, -1, 1):
        day = (kickoff + timedelta(days=delta)).date().isoformat()
        rows = games[(games["away_team"] == away) & (games["home_team"] == home)
                     & (games["gameday"].astype(str).str[:10] == day)]
        if len(rows):
            return rows.iloc[0]
    return None


def grade_reference(ref: dict, home_pts: float, away_pts: float
                    ) -> Optional[str]:
    family, side, line = ref.get("family"), ref.get("side"), ref.get("line")
    if family == "moneyline":
        if side not in ("home", "away"):
            return None
        team, opp = ((home_pts, away_pts) if side == "home"
                     else (away_pts, home_pts))
        if team == opp:
            return "push"
        return "win" if team > opp else "loss"
    if family == "spread" and line is not None and side in ("home", "away"):
        team, opp = ((home_pts, away_pts) if side == "home"
                     else (away_pts, home_pts))
        adj = team + float(line)
        if adj == opp:
            return "push"
        return "win" if adj > opp else "loss"
    if family == "total" and line is not None and side in ("over", "under"):
        total = home_pts + away_pts
        if total == float(line):
            return "push"
        hit = total > float(line) if side == "over" else total < float(line)
        return "win" if hit else "loss"
    return None


def units(result: str, decimal: float) -> float:
    if result == "win":
        return decimal - 1.0
    if result == "loss":
        return -1.0
    return 0.0


def _edge_band(edge: Optional[float]) -> str:
    if edge is None:
        return "unknown"
    if edge < 0:
        return "negative"
    if edge < 0.03:
        return "0-3%"
    if edge < 0.06:
        return "3-6%"
    if edge < 0.10:
        return "6-10%"
    return "10%+"


def grade_all(since: str = "") -> dict:
    from nfl_props.sources.nflverse import load_raw_games
    games = load_raw_games()

    paths = sorted(FORECAST_HISTORY_DIR.glob("nfl_forecast_*.json"))
    if since:
        floor = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        paths = [p for p in paths
                 if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
                 >= floor]
    chosen = select_latest_pregame(paths)

    winner_n = winner_correct = 0
    brier_terms: List[float] = []
    logloss_terms: List[float] = []
    margin_err: List[float] = []
    total_err: List[float] = []
    pending = 0
    ref_rows: List[dict] = []

    for fc in chosen.values():
        final = match_final(games, fc["away"], fc["home"], fc["_kickoff"])
        if final is None or pd.isna(final["home_score"]) \
                or pd.isna(final["away_score"]):
            pending += 1
            continue
        hp, ap = float(final["home_score"]), float(final["away_score"])
        p_home = fc.get("p_home_win")
        if p_home is not None and hp != ap:
            y = 1.0 if hp > ap else 0.0
            winner_n += 1
            winner_correct += int((p_home >= 0.5) == bool(y))
            brier_terms.append((p_home - y) ** 2)
            p = min(max(p_home, 1e-9), 1 - 1e-9)
            logloss_terms.append(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
        if fc.get("projected_margin") is not None:
            margin_err.append(abs((hp - ap) - float(fc["projected_margin"])))
        if fc.get("projected_total") is not None:
            total_err.append(abs((hp + ap) - float(fc["projected_total"])))
        for ref in fc.get("references", []):
            if ref.get("decimal") is None:
                continue
            result = grade_reference(ref, hp, ap)
            if result is None:
                continue
            ref_rows.append({
                "family": ref.get("family"), "source": ref.get("source"),
                "status": ref.get("status"),
                "edge_band": _edge_band(ref.get("edge")),
                "result": result,
                "units": units(result, float(ref["decimal"])),
            })

    def _ref_report() -> dict:
        out: Dict[str, dict] = {}
        for key in ("family", "source", "status", "edge_band"):
            groups: Dict[str, List[dict]] = {}
            for row in ref_rows:
                groups.setdefault(str(row[key]), []).append(row)
            out[key] = {
                name: {
                    "n": len(rows),
                    "wins": sum(1 for r in rows if r["result"] == "win"),
                    "losses": sum(1 for r in rows if r["result"] == "loss"),
                    "pushes": sum(1 for r in rows if r["result"] == "push"),
                    "units": round(sum(r["units"] for r in rows), 2),
                    "roi": round(sum(r["units"] for r in rows) / len(rows), 4),
                }
                for name, rows in sorted(groups.items())
            }
        return out

    return {
        "grading_version": FORECAST_GRADING_VERSION,
        "model_version": FORECAST_MODEL_VERSION,
        "graded_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshots": len(paths),
        "forecasts_considered": len(chosen),
        "pending": pending,
        "winner": {
            "n": winner_n,
            "correct": winner_correct,
            "accuracy": round(winner_correct / winner_n, 4) if winner_n else None,
            "brier": round(float(np.mean(brier_terms)), 4) if brier_terms else None,
            "log_loss": (round(float(np.mean(logloss_terms)), 4)
                         if logloss_terms else None),
        },
        "projection": {
            "margin_mae": (round(float(np.mean(margin_err)), 3)
                           if margin_err else None),
            "total_mae": (round(float(np.mean(total_err)), 3)
                          if total_err else None),
        },
        "references": _ref_report(),
    }


def render_report(stats: dict) -> str:
    lines = [
        f"NFL forecast grade — {FORECAST_GRADING_VERSION} "
        f"({FORECAST_MODEL_VERSION})",
        f"generated {stats['graded_at_utc']} | snapshots {stats['snapshots']} "
        f"| forecasts {stats['forecasts_considered']} | pending "
        f"{stats['pending']}",
        "",
        "Forecast quality:",
        f"  winner n={stats['winner']['n']} "
        f"accuracy={stats['winner']['accuracy']} "
        f"brier={stats['winner']['brier']} "
        f"log_loss={stats['winner']['log_loss']}",
        f"  margin MAE={stats['projection']['margin_mae']} "
        f"total MAE={stats['projection']['total_mae']}",
        "",
        "Reference ROI (flat 1u at captured price; descriptive only):",
    ]
    for key, groups in stats["references"].items():
        lines.append(f"  by {key}:")
        for name, g in groups.items():
            lines.append(
                f"    {name:<12} n={g['n']:<4} {g['wins']}-{g['losses']}-"
                f"{g['pushes']} units={g['units']:+.2f} roi={g['roi']:+.1%}")
    return "\n".join(lines)


def _target_week(games: pd.DataFrame, now: datetime
                 ) -> Optional[Tuple[int, int]]:
    """Most recent regular-season (season, week) fully kicked off before today.

    "Completed" is schedule-based (every game-day before today in ET) so the
    just-finished week is selected even if a final score is still missing;
    those games grade as pending, never as losses.
    """
    if games is None or games.empty:
        return None
    reg = games[games["game_type"] == "REG"]
    if reg.empty:
        return None
    today = now.astimezone(WEEKLY_TZ).date().isoformat()
    days = reg["gameday"].astype(str).str[:10]
    past = reg[days < today]
    if past.empty:
        return None
    weeks = sorted({(int(s), int(w))
                    for s, w in zip(past["season"], past["week"])})
    return weeks[-1]


def _week_label(games: pd.DataFrame, season: int, week: int) -> str:
    rows = games[(games["game_type"] == "REG")
                 & (games["season"] == season) & (games["week"] == week)]
    days = sorted({str(d)[:10] for d in rows["gameday"].astype(str)})

    def fmt(day: str) -> str:
        return datetime.fromisoformat(day).strftime("%b %-d")

    if not days:
        return f"Week {week}"
    if days[0] == days[-1]:
        return f"Week {week} ({fmt(days[0])})"
    if days[0][:7] == days[-1][:7]:
        return (f"Week {week} ({fmt(days[0])}-"
                f"{datetime.fromisoformat(days[-1]).day})")
    return f"Week {week} ({fmt(days[0])}-{fmt(days[-1])})"


def _wl_rows(rows: List[dict]) -> dict:
    return {
        "n": len(rows),
        "wins": sum(1 for r in rows if r["result"] == "win"),
        "losses": sum(1 for r in rows if r["result"] == "loss"),
        "pushes": sum(1 for r in rows if r["result"] == "push"),
    }


def _record_text(wins: int, losses: int, pushes: int) -> str:
    text = f"{wins}-{losses}"
    if pushes:
        text += f"-{pushes}"
    return text


def grade_week(season: Optional[int] = None, week: Optional[int] = None,
               games: Optional[pd.DataFrame] = None,
               paths: Optional[List[Path]] = None,
               now: Optional[datetime] = None) -> dict:
    """Grade one regular-season week: record + priced ROI + forecast quality.

    Selects the latest pre-kickoff forecast per game (same rule as the
    cumulative grader), keeps only games from the target (season, week),
    and grades moneyline / spread / total references at captured prices.
    """
    from nfl_props.sources.nflverse import load_raw_games
    now = now or datetime.now(timezone.utc)
    if games is None:
        games = load_raw_games()
    paths = sorted(paths) if paths is not None else sorted(
        FORECAST_HISTORY_DIR.glob("nfl_forecast_*.json"))
    if season is None or week is None:
        target = _target_week(games, now)
        if target is None:
            return {"error": "no completed regular-season week found"}
        season, week = target
    season, week = int(season), int(week)

    chosen = select_latest_pregame(paths)
    label = _week_label(games, season, week)

    ref_rows: List[dict] = []
    game_rows: List[str] = []
    pending: List[str] = []
    winner_n = winner_correct = 0
    brier_terms: List[float] = []
    logloss_terms: List[float] = []
    margin_err: List[float] = []
    total_err: List[float] = []
    margin_residuals: List[dict] = []
    total_residuals: List[dict] = []
    reference_errors: Dict[str, List[float]] = {"spread": [], "total": []}
    confidence_rows: List[dict] = []
    directional_reversals = 0
    market_reversal_n = market_reversal_correct = market_reversal_incorrect = 0

    for fc in chosen.values():
        final = match_final(games, fc["away"], fc["home"], fc["_kickoff"])
        if final is None or str(final.get("game_type")) != "REG" \
                or int(final["season"]) != season \
                or int(final["week"]) != week:
            continue
        matchup = f"{fc['away']} @ {fc['home']}"
        if pd.isna(final["home_score"]) or pd.isna(final["away_score"]):
            pending.append(matchup)
            continue
        hp, ap = float(final["home_score"]), float(final["away_score"])
        actual_margin, actual_total = hp - ap, hp + ap
        game_rows.append(matchup)
        p_home = fc.get("p_home_win")
        if p_home is not None and hp != ap:
            y = 1.0 if hp > ap else 0.0
            winner_n += 1
            winner_correct += int((p_home >= 0.5) == bool(y))
            brier_terms.append((float(p_home) - y) ** 2)
            p = min(max(float(p_home), 1e-9), 1 - 1e-9)
            logloss_terms.append(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
            confidence = fc.get("winner_confidence")
            confidence = float(confidence if confidence is not None else max(p_home, 1 - p_home))
            group = ("50-60%" if confidence < .60 else "60-70%" if confidence < .70
                     else "70-80%" if confidence < .80 else "80-100%")
            won = (p_home >= .5) == bool(y)
            confidence_rows.append({"group": group, "correct": won,
                                    "confidence": confidence})
            # Winner model-vs-market favorite reversal (game level). The stored
            # `p_market` is the consensus de-vigged market probability for the
            # *model's chosen side* (see nfl_props.references.build_reference),
            # so a chosen side the market prices below 0.5 is the market
            # underdog: the model is reversing the market's favorite. This is
            # separate from the margin sign count `directional_reversals`
            # above; ties are excluded because this sits inside the decided
            # winner block.
            model_side = "home" if float(p_home) >= .5 else "away"
            market_p = None
            for ref in fc.get("references", []):
                if ref.get("family") == "moneyline" \
                        and ref.get("side") == model_side \
                        and ref.get("p_market") is not None:
                    market_p = float(ref["p_market"])
                    break
            if market_p is not None and market_p < 0.5:
                market_reversal_n += 1
                if won:
                    market_reversal_correct += 1
                else:
                    market_reversal_incorrect += 1
        if fc.get("projected_margin") is not None:
            predicted = float(fc["projected_margin"])
            residual = actual_margin - predicted
            margin_err.append(abs(residual))
            margin_residuals.append({"matchup": matchup, "actual": actual_margin,
                                     "predicted": predicted, "residual": residual,
                                     "abs_residual": abs(residual)})
            if actual_margin and predicted and (actual_margin > 0) != (predicted > 0):
                directional_reversals += 1
        if fc.get("projected_total") is not None:
            predicted = float(fc["projected_total"])
            residual = actual_total - predicted
            total_err.append(abs(residual))
            total_residuals.append({"matchup": matchup, "actual": actual_total,
                                    "predicted": predicted, "residual": residual,
                                    "abs_residual": abs(residual)})
        for ref in fc.get("references", []):
            line = ref.get("line")
            if ref.get("family") == "spread" and line is not None \
                    and ref.get("side") in ("home", "away"):
                # Home-side line is the home handicap; away-side line is the
                # away handicap. Convert each to home-minus-away margin.
                predicted = (-float(line) if ref.get("side") == "home"
                             else float(line))
                reference_errors["spread"].append(actual_margin - predicted)
            elif ref.get("family") == "total" and line is not None:
                reference_errors["total"].append(actual_total - float(line))
            if ref.get("decimal") is None:
                continue
            result = grade_reference(ref, hp, ap)
            if result is None:
                continue
            ref_rows.append({
                "family": ref.get("family"), "source": ref.get("source"),
                "status": ref.get("status"),
                "edge_band": _edge_band(ref.get("edge")),
                "result": result,
                "units": units(result, float(ref["decimal"])),
            })

    record = _wl_rows(ref_rows)
    decided = record["wins"] + record["losses"]
    families = {fam: _wl_rows([r for r in ref_rows if r["family"] == fam])
                for fam, _ in WEEKLY_FAMILIES}
    sources: Dict[str, List[dict]] = {}
    for row in ref_rows:
        sources.setdefault(str(row["source"]), []).append(row)
    unit_total = round(sum(r["units"] for r in ref_rows), 2)

    def error_summary(values: List[float]) -> dict:
        return {"n": len(values), "mae": round(float(np.mean(np.abs(values))), 3) if values else None,
                "signed_mean": round(float(np.mean(values)), 3) if values else None,
                "signed_median": round(float(np.median(values)), 3) if values else None,
                "sign": "actual minus predicted"}

    confidence_groups: Dict[str, List[dict]] = {}
    for row in confidence_rows:
        confidence_groups.setdefault(row["group"], []).append(row)
    confidence_report: Dict[str, dict] = {}
    for name, rows in sorted(confidence_groups.items()):
        n = len(rows)
        correct = sum(r["correct"] for r in rows)
        accuracy = round(correct / n, 4)
        mean_confidence = round(float(np.mean([r["confidence"] for r in rows])),
                                4)
        # Selected confidence minus observed outcome (win=1, loss=0): a
        # positive gap means the bucket was overconfident on this sample.
        confidence_report[name] = {
            "n": n,
            "correct": correct,
            "accuracy": accuracy,
            "mean_confidence": mean_confidence,
            "calibration_gap": round(mean_confidence - accuracy, 4),
        }

    def grouped_outcomes(key: str) -> dict:
        groups: Dict[str, List[dict]] = {}
        for row in ref_rows:
            groups.setdefault(str(row[key]), []).append(row)
        return {name: {**_wl_rows(rows), "units": round(sum(r["units"] for r in rows), 2)}
                for name, rows in sorted(groups.items())}

    return {
        "grading_version": FORECAST_GRADING_VERSION,
        "model_version": FORECAST_MODEL_VERSION,
        "mode": "weekly",
        "graded_at_utc": now.isoformat(),
        "season": season,
        "week": week,
        "label": label,
        "games": sorted(game_rows),
        "pending": sorted(pending),
        "winner": {
            "n": winner_n,
            "correct": winner_correct,
            "accuracy": (round(winner_correct / winner_n, 4) if winner_n else None),
            "brier": round(float(np.mean(brier_terms)), 4) if brier_terms else None,
            "log_loss": round(float(np.mean(logloss_terms)), 4) if logloss_terms else None,
        },
        "market_reversals": {
            "n": market_reversal_n,
            "correct": market_reversal_correct,
            "incorrect": market_reversal_incorrect,
            "win_rate": (round(market_reversal_correct / market_reversal_n, 4)
                         if market_reversal_n else None),
        },
        "projection": {
            "margin_mae": round(float(np.mean(margin_err)), 3) if margin_err else None,
            "total_mae": round(float(np.mean(total_err)), 3) if total_err else None,
            "margin_residual": error_summary([r["residual"] for r in margin_residuals]),
            "total_residual": error_summary([r["residual"] for r in total_residuals]),
            "directional_reversals": directional_reversals,
            "largest_margin_residuals": sorted(margin_residuals, key=lambda r: (-r["abs_residual"], r["matchup"]))[:5],
            "largest_total_residuals": sorted(total_residuals, key=lambda r: (-r["abs_residual"], r["matchup"]))[:5],
        },
        "reference_forecast_error": {
            "spread_margin": error_summary(reference_errors["spread"]),
            "total": error_summary(reference_errors["total"]),
        },
        "confidence_groups": confidence_report,
        "edge_bands": grouped_outcomes("edge_band"),
        "record": {**record, "pct": (round(record["wins"] / decided, 4)
                                     if decided else None)},
        "families": families,
        "priced": {
            "units": unit_total,
            "plays": len(ref_rows),
            "roi": (round(unit_total / len(ref_rows), 4)
                    if ref_rows else None),
        },
        "sources": {
            name: {
                **_wl_rows(rows),
                "units": round(sum(r["units"] for r in rows), 2),
                "roi": (round(sum(r["units"] for r in rows) / len(rows), 4)
                        if rows else None),
            }
            for name, rows in sorted(sources.items())
        },
    }


def render_weekly_recap(stats: dict) -> str:
    """Public weekly recap: week record, family records, priced ROI."""
    rec = stats["record"]
    pct = " (n/a)" if rec["pct"] is None else f" ({rec['pct']:.1%})"
    lines = [
        f"NFL Board Recap - {stats['label']}",
        "",
        f"Week record: {_record_text(rec['wins'], rec['losses'], rec['pushes'])}{pct}",
    ]
    for fam_key, fam_label in WEEKLY_FAMILIES:
        g = stats["families"][fam_key]
        if g["n"]:
            lines.append(f"  {fam_label}: "
                         f"{_record_text(g['wins'], g['losses'], g['pushes'])}")
    priced = stats["priced"]
    if priced["plays"]:
        lines += ["", f"Priced ROI: {priced['units']:+.2f}u across "
                      f"{priced['plays']} plays ({priced['roi']:+.1%})"]
    else:
        lines += ["", "Priced ROI: n/a (no graded references)"]
    if stats["pending"]:
        lines.append(f"Pending: {len(stats['pending'])} games")
    return "\n".join(lines)


def _load_weekly_delivered() -> set:
    if not DELIVERY_MARKER.exists():
        return set()
    try:
        return set(json.loads(DELIVERY_MARKER.read_text()).get("weeks", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _record_weekly_delivered(key: str) -> None:
    keys = _load_weekly_delivered()
    keys.add(key)
    DELIVERY_MARKER.parent.mkdir(parents=True, exist_ok=True)
    DELIVERY_MARKER.write_text(json.dumps({"weeks": sorted(keys)}, indent=1),
                               encoding="utf-8")


def _post_weekly_recap(stats: dict, force: bool) -> int:
    """Post the weekly recap once per season/week. Returns 0 ok, 1 failure."""
    webhook = os.environ.get("NFL_DISCORD_WEBHOOK_URL", "")
    if not webhook:
        log("[grade_forecast] discord on but NFL_DISCORD_WEBHOOK_URL unset")
        return 1
    key = f"{stats['season']}-w{int(stats['week']):02d}"
    if key in _load_weekly_delivered() and not force:
        log(f"[grade_forecast] week {key} already delivered; skipping")
        return 0
    from nfl_props.notifiers.forecast_discord import (chunk_messages,
                                                      post_webhook)
    for chunk in chunk_messages(render_weekly_recap(stats)):
        result = post_webhook(webhook, chunk)
        if not result.ok:
            log(f"[grade_forecast] discord failed: "
                f"{result.error or result.status_code}")
            return 1
    _record_weekly_delivered(key)
    log("[grade_forecast] weekly discord ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="grade forecast-first snapshots")
    ap.add_argument("--since", default="", help="ISO date lower bound")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--weekly", action="store_true",
                    help="week-scoped public recap for the just-completed week")
    ap.add_argument("--season", type=int, default=None,
                    help="weekly mode: override season (default: auto)")
    ap.add_argument("--week", type=int, default=None,
                    help="weekly mode: override week (default: auto)")
    ap.add_argument("--discord", action="store_true",
                    help="weekly mode: post the recap (also honors "
                    "NFL_SEND_DISCORD)")
    ap.add_argument("--no-discord", action="store_true")
    ap.add_argument("--force-send", action="store_true",
                    help="weekly mode: repost even if this week was delivered")
    args = ap.parse_args()

    config.ensure_dirs()
    if args.weekly:
        stats = grade_week(season=args.season, week=args.week)
        if stats.get("error"):
            print(f"weekly grade: {stats['error']}")
            return 1
        text = render_weekly_recap(stats)
        print(text)
        if not args.no_export:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            tag = f"{stats['season']}w{int(stats['week']):02d}"
            FORECAST_BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
            (FORECAST_BACKTESTS_DIR /
             f"grade_weekly_{tag}_{stamp}.txt").write_text(text,
                                                           encoding="utf-8")
            (FORECAST_BACKTESTS_DIR /
             f"grade_weekly_{tag}_{stamp}.json").write_text(
                 json.dumps(stats, indent=2), encoding="utf-8")
            log(f"[grade_forecast] wrote weekly {tag} report")
        env_send = os.environ.get("NFL_SEND_DISCORD", "").lower() in (
            "1", "true", "yes")
        send = (args.discord or env_send) and not args.no_discord
        if send:
            return _post_weekly_recap(stats, force=args.force_send)
        return 0
    stats = grade_all(args.since)
    text = render_report(stats)
    print(text)
    if not args.no_export:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        FORECAST_BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
        (FORECAST_BACKTESTS_DIR / f"grade_forecast_{stamp}.txt").write_text(
            text, encoding="utf-8")
        (FORECAST_BACKTESTS_DIR / f"grade_forecast_{stamp}.json").write_text(
            json.dumps(stats, indent=2), encoding="utf-8")
        log(f"[grade_forecast] wrote {stamp} report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
