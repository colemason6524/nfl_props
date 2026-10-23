#!/usr/bin/env python3
"""Grade forecast-first snapshots: forecast quality + reference ROI.

Separate from the legacy grade.py. Reads outputs/forecast_history/
nfl_forecast_*.json, selects the latest pre-kickoff forecast per stable
forecast id, and reports:

  forecast  : winner accuracy (ties excluded), Brier, log loss, margin/total MAE
  reference : flat-1u ROI at captured prices, by family / source / label

Reference grading uses the latest pregame snapshot (outputs/snapshots/)
when one exists with timestamp < kickoff -- i.e. what was actually
bettable after wave boards / delta alerts -- falling back to the board
artifact otherwise. Forecast accuracy is always graded from the board
artifact.

Forecast accuracy and reference value are never pooled. NFL ties settle the
moneyline reference as a push (stake returned), never a loss.

The season record is scoped to PLAYABLE references, exactly like the weekly
recap: NO_VALUE references (EV below the playable threshold, or demoted by
the dead-line ceiling) are graded and reported separately, and the old
all-references figures survive only as an explicitly labelled `pooled`
diagnostic.
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
from nfl_props.snapshots import latest_snapshot_before
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


def _with_snapshot_pick(fc: dict) -> dict:
    """Swap in the latest pregame snapshot's picks for reference grading.

    Grading uses the latest bettable state: if a wave board or delta alert
    updated a pick after the board artifact was written, the snapshot with
    the latest ``timestamp < kickoff`` wins. Falls back to the board
    artifact when no snapshot exists (historical backfill path).
    Forecast-quality fields (p_home_win, projected margin/total) always
    come from the board artifact -- snapshots only override references.
    """
    fid = fc.get("forecast_id") or fc.get("game_id")
    kickoff = fc.get("_kickoff")
    if not fid or kickoff is None:
        return fc
    try:
        snap = latest_snapshot_before(str(fid), kickoff)
    except Exception:
        return fc
    if not snap or not snap.get("picks"):
        return fc
    out = dict(fc)
    out["references"] = snap["picks"]
    out["_snapshot_source"] = snap.get("source")
    out["_snapshot_ts"] = snap.get("timestamp")
    return out


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
        fc = _with_snapshot_pick(fc)
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
        "headline": _season_headline(ref_rows),
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
        "Season record (PLAYABLE references only; flat 1u at captured price):",
    ]
    headline = stats["headline"]
    for label, key in (("PLAYABLE", "playable"), ("NO_VALUE", "no_value"),
                       ("pooled", "pooled")):
        bundle = headline[key]
        rec = bundle["record"]
        roi = bundle["roi"] if bundle["roi"] is not None else 0.0
        lines.append(
            f"  {label:<9} "
            f"{_record_text(rec['wins'], rec['losses'], rec['pushes'])}"
            f"  units={bundle['units']:+.2f} roi={roi:+.1%}"
            f"  ({bundle['plays']} graded refs)")
    lines += [
        "  (pooled = PLAYABLE + NO_VALUE, diagnostic only: NO_VALUE refs "
        "were never advertised picks)",
        "",
        "Reference breakdowns (pooled over all priced refs: PLAYABLE + "
        "NO_VALUE):",
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


def _ref_bundle(rows: List[dict]) -> dict:
    """Record + flat-1u totals for one set of graded reference rows."""
    rec = _wl_rows(rows)
    decided = rec["wins"] + rec["losses"]
    units_total = round(sum(r["units"] for r in rows), 2)
    return {
        "record": {**rec,
                   "pct": (round(rec["wins"] / decided, 4) if decided
                           else None)},
        "units": units_total,
        "plays": len(rows),
        "roi": round(units_total / len(rows), 4) if rows else None,
    }


def _season_headline(ref_rows: List[dict]) -> dict:
    """Season record split by advertisement status.

    `playable` is the board's record: a NO_VALUE reference was never a
    playable pick, so pooling it into the season record/ROI misstates what
    the board put forward. `no_value` carries the same shape for everything
    priced but not PLAYABLE, and `pooled` keeps the old all-references
    accounting, so playable n + no_value n still equals the old graded n.
    """
    from nfl_props.references import PLAYABLE
    playable = [r for r in ref_rows if r["status"] == PLAYABLE]
    rest = [r for r in ref_rows if r["status"] != PLAYABLE]
    return {
        "scope": "PLAYABLE references only",
        "playable": _ref_bundle(playable),
        "no_value": _ref_bundle(rest),
        "pooled": _ref_bundle(ref_rows),
    }


def grade_week(season: Optional[int] = None, week: Optional[int] = None,
               games: Optional[pd.DataFrame] = None,
               paths: Optional[List[Path]] = None,
               now: Optional[datetime] = None) -> dict:
    """Grade one regular-season week: record + priced ROI + forecast quality.

    Selects the latest pre-kickoff forecast per game (same rule as the
    cumulative grader), keeps only games from the target (season, week),
    and grades moneyline / spread / total references at captured prices.
    """
    from nfl_props.forecast import DEAD_LINE_SUSPECT
    from nfl_props.references import PLAYABLE
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
    ceiling_hits = 0
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
        fc = _with_snapshot_pick(fc)
        final = match_final(games, fc["away"], fc["home"], fc["_kickoff"])
        if final is None or str(final.get("game_type")) != "REG" \
                or int(final["season"]) != season \
                or int(final["week"]) != week:
            continue
        matchup = f"{fc['away']} @ {fc['home']}"
        # Dead-line ceiling hits are counted here, before any gradeability
        # or scoreboard skip: a demoted reference counts whether or not the
        # game has a final score and whether or not a price was captured.
        # The signal is the demotion itself -- nfl_props.forecast
        # .apply_edge_ceiling stamps DEAD_LINE_SUSPECT on the ref note.
        for ref in fc.get("references", []):
            if DEAD_LINE_SUSPECT in str(ref.get("note") or ""):
                ceiling_hits += 1
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
                "note": ref.get("note"),
                "edge_band": _edge_band(ref.get("edge")),
                "result": result,
                "units": units(result, float(ref["decimal"])),
            })

    # Only PLAYABLE references are the board's advertised record: a
    # NO_VALUE ref (EV below the playable threshold, or demoted by the
    # dead-line EV/edge ceiling) was never a playable pick, so pooling it
    # into the headline record/ROI misstates what the board put forward.
    # Everything priced but not PLAYABLE is reported separately under
    # `no_value`, so playable n + no_value n still equals the old pooled
    # graded n.
    playable_rows = [r for r in ref_rows if r["status"] == PLAYABLE]
    no_value_rows = [r for r in ref_rows if r["status"] != PLAYABLE]

    def split_bundle(rows: List[dict]) -> dict:
        rec = _wl_rows(rows)
        decided = rec["wins"] + rec["losses"]
        units_total = round(sum(r["units"] for r in rows), 2)
        return {
            "record": {**rec, "pct": (round(rec["wins"] / decided, 4)
                                     if decided else None)},
            "families": {fam: _wl_rows([r for r in rows
                                        if r["family"] == fam])
                         for fam, _ in WEEKLY_FAMILIES},
            "priced": {
                "units": units_total,
                "plays": len(rows),
                "roi": (round(units_total / len(rows), 4) if rows
                        else None),
            },
        }

    playable_bundle = split_bundle(playable_rows)
    no_value_bundle = split_bundle(no_value_rows)
    record = playable_bundle["record"]
    families = playable_bundle["families"]
    # Source and edge-band breakdowns stay pooled over every graded ref, so
    # the artifact keeps the accounting it has always carried.
    sources: Dict[str, List[dict]] = {}
    for row in ref_rows:
        sources.setdefault(str(row["source"]), []).append(row)

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
        # `record` / `families` / `priced` keep their names (backward
        # compatible) but now cover PLAYABLE references only; the NO_VALUE
        # split and the dead-line ceiling-hit count sit alongside them.
        "record": record,
        "families": families,
        "priced": playable_bundle["priced"],
        "no_value": no_value_bundle,
        "ceiling_hits": ceiling_hits,
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


def render_weekly_recap(stats: dict,
                        results: Optional[dict] = None) -> str:
    """Public weekly recap card in the house format.

    Header / tier rows / footer / sign-off, nothing else: no internal
    vocabulary (NO_VALUE, ceiling hits, reason codes) and no ROI internals on
    the page. Rows and units come from the grade report itself, so the card
    can never disagree with the ledger. The Leans row prints only when the
    report carries a leans bundle (sections with no picks are omitted).
    """
    from nfl_props import house
    rec = stats["record"]
    priced = stats.get("priced") or {}
    rows: List[tuple] = []
    if (rec.get("wins") or rec.get("losses") or rec.get("pushes")):
        rows.append((house.PLAYS_EMOJI, "Plays",
                     house.record_text({"wins": rec["wins"],
                                        "losses": rec["losses"],
                                        "pushes": rec["pushes"],
                                        "units": priced.get("units"),
                                        "plays": priced.get("plays")
                                        or (rec["wins"] + rec["losses"]
                                            + rec["pushes"])})))
    leans = stats.get("leans") or {}
    if leans.get("plays"):
        rows.append((house.LEANS_EMOJI, "Leans", house.record_text(leans)))
    return house.render_card(
        rows, results, day=str(stats.get("label") or ""),
        empty_note="No picks graded this week.")


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


def rolling_results(now: Optional[datetime] = None,
                    games: Optional[pd.DataFrame] = None) -> dict:
    """Rolling records for the house footer: last slate, 7d, 30d.

    Gradeable = PLAYABLE references graded at the captured price (the same
    rows, price and settle rule the season record uses), so the footer can
    never disagree with the ledger. Windows are kickoff-based in ET; "last
    night" is the most recent completed slate, never a fixed 24h box.
    """
    from nfl_props.references import PLAYABLE
    now = now or datetime.now(timezone.utc)
    rows: List[dict] = []
    if games is None:
        from nfl_props.sources.nflverse import load_raw_games
        games = load_raw_games()
    if games is not None and not games.empty:
        chosen = select_latest_pregame(
            sorted(FORECAST_HISTORY_DIR.glob("nfl_forecast_*.json")))
        for fc in chosen.values():
            fc = _with_snapshot_pick(fc)
            final = match_final(games, fc["away"], fc["home"], fc["_kickoff"])
            if final is None or pd.isna(final.get("home_score")) \
                    or pd.isna(final.get("away_score")):
                continue
            hp, ap = float(final["home_score"]), float(final["away_score"])
            for ref in fc.get("references", []):
                if ref.get("status") != PLAYABLE or ref.get("decimal") is None:
                    continue
                result = grade_reference(ref, hp, ap)
                if result is None:
                    continue
                rows.append({"kickoff": fc["_kickoff"], "result": result,
                             "units": units(result, float(ref["decimal"]))})

    def bundle(batch: List[dict]) -> dict:
        rec = _wl_rows(batch)
        total = round(sum(r["units"] for r in batch), 2)
        return {**rec, "units": total, "plays": len(batch),
                "roi": round(total / len(batch), 4) if batch else None}

    today = now.astimezone(WEEKLY_TZ).date()
    dated = [r for r in rows if r.get("kickoff") is not None]
    past = [r for r in dated
            if r["kickoff"].astimezone(WEEKLY_TZ).date() < today]
    last_night: List[dict] = []
    label = None
    if past:
        latest = max(r["kickoff"].astimezone(WEEKLY_TZ).date() for r in past)
        last_night = [r for r in past
                      if r["kickoff"].astimezone(WEEKLY_TZ).date() == latest]
        label = latest.strftime("%a %b %-d")
    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)
    return {
        "last_night": bundle(last_night),
        "last_night_label": label,
        "last_7d": bundle([r for r in dated if r["kickoff"] > cutoff_7]),
        "last_30d": bundle([r for r in dated if r["kickoff"] > cutoff_30]),
    }


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
        try:
            results = rolling_results()
        except Exception as exc:  # footer must never block the card
            log(f"[grade_forecast] rolling results unavailable: {exc}")
            results = None
        text = render_weekly_recap(stats, results=results)
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
