"""Weekly public grade: week targeting, record/ROI format, delivery.

The recap splits PLAYABLE from NO_VALUE: `record`/`families`/`priced` cover
PLAYABLE references only, `no_value` carries the same bundle shape for the
demoted/unplayable ones, and `ceiling_hits` counts dead-line ceiling
demotions whether or not the game/reference was graded.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd

import grade_forecast as gf
from grade_forecast import (grade_week, render_weekly_recap,
                            _post_weekly_recap, _target_week)
from nfl_props.version import FORECAST_MODEL_VERSION

NOW = datetime(2026, 9, 15, 7, 30, tzinfo=timezone.utc)  # Tue 03:30 ET


def _ref(family, side, line, decimal, source="bovada", p_market=None,
         status="PLAYABLE", note=None):
    return {"family": family, "side": side, "line": line,
            "decimal": decimal, "source": source, "status": status,
            "p_market": p_market, "note": note}


def _fc(fid, away, home, kickoff, p_home, margin, total, refs):
    return {"forecast_id": fid, "available": True, "away": away,
            "home": home, "kickoff_utc": kickoff, "p_home_win": p_home,
            "projected_margin": margin, "projected_total": total,
            "references": refs}


def _games():
    return pd.DataFrame([
        # Week 1 (older completed week; must not leak into week 2).
        {"game_type": "REG", "season": 2026, "week": 1,
         "gameday": "2026-09-06", "away_team": "OLD_A",
         "home_team": "OLD_H", "home_score": 21.0, "away_score": 17.0},
        # Week 2: Thu / Sun / Mon.
        {"game_type": "REG", "season": 2026, "week": 2,
         "gameday": "2026-09-10", "away_team": "AWY",
         "home_team": "HOM", "home_score": 27.0, "away_score": 20.0},
        {"game_type": "REG", "season": 2026, "week": 2,
         "gameday": "2026-09-13", "away_team": "AAA",
         "home_team": "BBB", "home_score": 17.0, "away_score": 24.0},
        {"game_type": "REG", "season": 2026, "week": 2,
         "gameday": "2026-09-14", "away_team": "CCC",
         "home_team": "DDD", "home_score": 24.0, "away_score": 21.0},
        # Week 2 game still missing its final.
        {"game_type": "REG", "season": 2026, "week": 2,
         "gameday": "2026-09-13", "away_team": "PEN_A",
         "home_team": "PEN_H", "home_score": float("nan"),
         "away_score": float("nan")},
        # Week 3 future game: excluded from the target week.
        {"game_type": "REG", "season": 2026, "week": 3,
         "gameday": "2026-09-17", "away_team": "FUT_A",
         "home_team": "FUT_H", "home_score": float("nan"),
         "away_score": float("nan")},
    ])


def _snapshots(directory: Path):
    a_late = _fc("g:A", "AWY", "HOM", "2026-09-11T00:30:00+00:00",
                 0.65, 7.0, 47.0,
                 [_ref("moneyline", "home", None, 1.8, p_market=0.40),
                  _ref("spread", "home", -3.0, 1.91),
                  _ref("total", "over", 45.5, 1.91)])
    a_early = _fc("g:A", "AWY", "HOM", "2026-09-11T00:30:00+00:00",
                  0.65, 7.0, 47.0,
                  [_ref("moneyline", "home", None, 5.0),
                  _ref("spread", "home", -3.0, 1.91),
                  _ref("total", "over", 45.5, 1.91)])
    b = _fc("g:B", "AAA", "BBB", "2026-09-13T17:00:00+00:00",
            0.40, -5.0, 44.0,
            [_ref("moneyline", "away", None, 2.1, p_market=0.42),
             _ref("spread", "away", -3.5, 1.91),
             _ref("total", "under", 45.5, 1.91)])
    c = _fc("g:C", "CCC", "DDD", "2026-09-15T00:15:00+00:00",
            0.45, 3.0, 45.0,
            [_ref("moneyline", "away", None, 1.9, p_market=0.49),
             _ref("spread", "home", -3.0, 1.91),
             _ref("total", "over", 45.0, 1.91)])
    d = _fc("g:D", "PEN_A", "PEN_H", "2026-09-13T20:00:00+00:00",
            0.6, 4.0, 46.0,
            [_ref("moneyline", "home", None, 1.8)])
    old = _fc("g:OLD", "OLD_A", "OLD_H", "2026-09-07T00:30:00+00:00",
              0.6, 4.0, 44.0, [_ref("moneyline", "home", None, 1.8)])
    payloads = [
        ("early.json", "2026-09-10T12:00:00+00:00", [a_early]),
        ("late.json", "2026-09-10T20:00:00+00:00", [a_late, b, c, d, old]),
    ]
    paths = []
    for name, generated, forecasts in payloads:
        path = directory / name
        path.write_text(json.dumps({
            "model_version": FORECAST_MODEL_VERSION,
            "generated_at_utc": generated, "forecasts": forecasts,
        }))
        paths.append(path)
    return paths


class TestWeekTargeting(unittest.TestCase):
    def test_targets_most_recent_kicked_week(self):
        self.assertEqual(_target_week(_games(), NOW), (2026, 2))

    def test_no_completed_week_is_an_error(self):
        games = _games()
        future = games[games["week"] == 3]
        stats = grade_week(games=future, paths=[], now=NOW)
        self.assertIn("error", stats)

    def test_season_week_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            stats = grade_week(season=2026, week=1, games=_games(),
                               paths=_snapshots(d), now=NOW)
        self.assertEqual((stats["season"], stats["week"]), (2026, 1))
        self.assertEqual(stats["label"], "Week 1 (Sep 6)")


class TestWeeklyGrading(unittest.TestCase):
    def _stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            return grade_week(games=_games(), paths=_snapshots(d), now=NOW)

    def test_thu_sun_mon_grouped_one_week(self):
        stats = self._stats()
        self.assertEqual(stats["label"], "Week 2 (Sep 10-14)")
        self.assertEqual(len(stats["games"]), 3)
        self.assertEqual(stats["pending"], ["PEN_A @ PEN_H"])

    def test_latest_pregame_snapshot_wins(self):
        # The early snapshot priced the moneyline at 5.0 (+4.0u); the late
        # one at 1.8 (+0.8u). The total below only matches the late one.
        stats = self._stats()
        self.assertAlmostEqual(stats["priced"]["units"], 4.54, places=2)

    def test_record_and_families(self):
        stats = self._stats()
        self.assertEqual((stats["record"]["wins"], stats["record"]["losses"],
                          stats["record"]["pushes"]), (6, 1, 2))
        self.assertAlmostEqual(stats["record"]["pct"], 6 / 7, places=4)
        fams = stats["families"]
        self.assertEqual((fams["moneyline"]["wins"],
                          fams["moneyline"]["losses"],
                          fams["moneyline"]["pushes"]), (2, 1, 0))
        self.assertEqual((fams["spread"]["wins"], fams["spread"]["losses"],
                          fams["spread"]["pushes"]), (2, 0, 1))
        self.assertEqual((fams["total"]["wins"], fams["total"]["losses"],
                          fams["total"]["pushes"]), (2, 0, 1))

    def test_format_matches_public_recap(self):
        text = render_weekly_recap(self._stats())
        self.assertIn("NFL Board Recap - Week 2 (Sep 10-14)", text)
        self.assertIn("Week record: 6-1-2 (85.7%)", text)
        self.assertIn("  Moneylines: 2-1", text)
        self.assertIn("  Spreads: 2-0-1", text)
        self.assertIn("  Totals: 2-0-1", text)
        self.assertIn("NO_VALUE: none graded", text)
        self.assertIn("Dead-line ceiling hits: 0", text)
        self.assertIn("Priced ROI: +4.54u across 9 plays (+50.4%)", text)
        self.assertIn("Pending: 1 games", text)

    def test_every_fixture_ref_is_playable_so_no_value_is_empty(self):
        stats = self._stats()
        self.assertEqual(stats["no_value"]["priced"]["plays"], 0)
        self.assertEqual(stats["no_value"]["record"]["n"], 0)
        self.assertEqual(stats["no_value"]["record"]["wins"], 0)
        self.assertEqual(stats["no_value"]["record"]["losses"], 0)
        self.assertEqual(stats["ceiling_hits"], 0)

    def test_forecast_accuracy_kept_in_artifact(self):
        stats = self._stats()
        self.assertEqual(stats["winner"]["n"], 3)  # tie/pending excluded
        self.assertEqual(stats["winner"]["correct"], 2)
        self.assertAlmostEqual(stats["winner"]["accuracy"], 2 / 3, places=4)
        self.assertAlmostEqual(stats["winner"]["brier"], (.35**2 + .4**2 + .55**2) / 3, places=4)
        self.assertAlmostEqual(stats["projection"]["margin_residual"]["signed_mean"], -2 / 3, places=3)
        self.assertEqual(stats["projection"]["margin_residual"]["signed_median"], 0)
        self.assertAlmostEqual(stats["projection"]["margin_residual"]["mae"], 2 / 3, places=3)
        self.assertEqual(stats["projection"]["total_residual"]["signed_mean"], -1.0)
        self.assertEqual(stats["reference_forecast_error"]["spread_margin"]["sign"],
                         "actual minus predicted")
        self.assertEqual(stats["confidence_groups"]["60-70%"]["n"], 2)
        self.assertIn("unknown", stats["edge_bands"])
        self.assertEqual(len(stats["projection"]["largest_margin_residuals"]), 3)
        self.assertEqual(stats["projection"]["directional_reversals"], 0)
        confidence = stats["confidence_groups"]["60-70%"]
        self.assertEqual(confidence["correct"], 2)
        self.assertEqual(confidence["accuracy"], 1.0)
        self.assertEqual(confidence["mean_confidence"], 0.625)
        self.assertEqual(confidence["calibration_gap"], -0.375)
        self.assertEqual(stats["market_reversals"], {
            "n": 3, "correct": 2, "incorrect": 1, "win_rate": 0.6667})

    def test_tied_final_is_excluded_from_probability_metrics(self):
        games = pd.concat([_games(), pd.DataFrame([{
            "game_type": "REG", "season": 2026, "week": 2,
            "gameday": "2026-09-14", "away_team": "TIE_A",
            "home_team": "TIE_H", "home_score": 20.0, "away_score": 20.0,
        }])], ignore_index=True)
        with tempfile.TemporaryDirectory() as tmp:
            paths = _snapshots(Path(tmp))
            tie_path = Path(tmp) / "tie.json"
            tie_path.write_text(json.dumps({
                "model_version": FORECAST_MODEL_VERSION,
                "generated_at_utc": "2026-09-13T12:00:00+00:00",
                "forecasts": [_fc("g:TIE", "TIE_A", "TIE_H",
                    "2026-09-14T20:00:00+00:00", .9, 2.0, 40.0,
                    [_ref("moneyline", "home", None, 1.8)])],
            }))
            stats = grade_week(games=games, paths=paths + [tie_path], now=NOW)
        self.assertEqual(stats["winner"]["n"], 3)
        self.assertAlmostEqual(stats["winner"]["brier"], (.35**2 + .4**2 + .55**2) / 3, places=4)


SPLIT_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _split_games():
    """Week 6 (2026): one graded game plus one game with no final score."""
    return pd.DataFrame([
        {"game_type": "REG", "season": 2026, "week": 6,
         "gameday": "2026-10-18", "away_team": "AAA",
         "home_team": "BBB", "home_score": 24.0, "away_score": 17.0},
        {"game_type": "REG", "season": 2026, "week": 6,
         "gameday": "2026-10-18", "away_team": "PEN_A",
         "home_team": "PEN_H", "home_score": float("nan"),
         "away_score": float("nan")},
    ])


def _split_paths(directory: Path):
    """One graded game and one pending game, mixed PLAYABLE / NO_VALUE.

    Graded game (HOM 24 - AWY 17):
      moneyline home @1.8 PLAYABLE  -> win  (+0.80u)
      spread   home -10.0 @1.91 NO_VALUE -> loss (-1.00u)
    Pending game (no final score), two dead-line ceiling demotions: one
    priced ref and one unpriceable (decimal None) ref, so the count proves
    it happens before the gradeability/scoreboard skips.
    """
    graded = _fc("g:MIX", "AAA", "BBB", "2026-10-18T17:00:00+00:00",
                 0.6, 5.0, 45.0,
                 [_ref("moneyline", "home", None, 1.8),
                  _ref("spread", "home", -10.0, 1.91, status="NO_VALUE")])
    pending = _fc("g:PEND", "PEN_A", "PEN_H", "2026-10-18T20:00:00+00:00",
                  0.55, 2.0, 46.0,
                  [_ref("moneyline", "home", None, 1.85, status="NO_VALUE",
                        note="DEAD_LINE_SUSPECT"),
                   _ref("spread", "home", -3.0, None, status="NO_VALUE",
                        note="no reference price captured DEAD_LINE_SUSPECT")])
    path = directory / "split.json"
    path.write_text(json.dumps({
        "model_version": FORECAST_MODEL_VERSION,
        "generated_at_utc": "2026-10-18T12:00:00+00:00",
        "forecasts": [graded, pending],
    }))
    return [path]


class TestWeeklyNoValueSplit(unittest.TestCase):
    def _stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            return grade_week(season=2026, week=6, games=_split_games(),
                              paths=_split_paths(Path(tmp)), now=SPLIT_NOW)

    def test_no_value_ref_excluded_from_playable_buckets(self):
        stats = self._stats()
        # PLAYABLE only: the moneyline win, nothing else.
        self.assertEqual(stats["priced"]["plays"], 1)
        self.assertEqual((stats["record"]["wins"], stats["record"]["losses"],
                          stats["record"]["pushes"]), (1, 0, 0))
        self.assertEqual(stats["record"]["pct"], 1.0)
        self.assertAlmostEqual(stats["priced"]["units"], 0.8, places=2)
        self.assertEqual(stats["families"]["moneyline"]["n"], 1)
        self.assertEqual(stats["families"]["spread"]["n"], 0)
        self.assertEqual(stats["families"]["total"]["n"], 0)
        # The NO_VALUE spread loss lands in its own bundle.
        nv = stats["no_value"]
        self.assertEqual(nv["priced"]["plays"], 1)
        self.assertEqual((nv["record"]["wins"], nv["record"]["losses"],
                          nv["record"]["pushes"]), (0, 1, 0))
        self.assertAlmostEqual(nv["priced"]["units"], -1.0, places=2)
        self.assertEqual(nv["families"]["spread"]["losses"], 1)
        self.assertEqual(nv["families"]["moneyline"]["n"], 0)

    def test_recap_shows_split_and_ceiling_hits(self):
        text = render_weekly_recap(self._stats())
        self.assertIn("Week record: 1-0 (100.0%)", text)
        self.assertIn("  Moneylines: 1-0", text)
        self.assertNotIn("  Spreads:", text)  # playable family lines only
        self.assertIn("NO_VALUE: 0-1 (1 graded refs, excluded from the record)",
                      text)
        self.assertIn("Dead-line ceiling hits: 2", text)
        self.assertIn("Priced ROI: +0.80u across 1 plays (+80.0%)", text)
        self.assertIn("Pending: 1 games", text)

    def test_ceiling_hits_counted_before_gradeability_skip(self):
        stats = self._stats()
        # Both demoted refs sit on the pending (unscored) game: one priced,
        # one with decimal None. Neither is graded, both are counted.
        self.assertEqual(stats["ceiling_hits"], 2)
        self.assertEqual(stats["pending"], ["PEN_A @ PEN_H"])
        self.assertEqual(stats["no_value"]["priced"]["plays"], 1)


class TestWeeklyPooledReconciliation(unittest.TestCase):
    def test_playable_plus_no_value_equals_old_pooled(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = grade_week(season=2026, week=6, games=_split_games(),
                               paths=_split_paths(Path(tmp)), now=SPLIT_NOW)
        playable_n = stats["priced"]["plays"]
        no_value_n = stats["no_value"]["priced"]["plays"]
        # Before the split these were one pooled `priced.plays` == 2.
        self.assertEqual(playable_n + no_value_n, 2)
        # ... and one pooled unit total == -0.20u.
        self.assertAlmostEqual(stats["priced"]["units"]
                               + stats["no_value"]["priced"]["units"],
                               -0.2, places=2)
        # Decisions reconcile the same way.
        self.assertEqual(stats["record"]["wins"] + stats["record"]["losses"]
                         + stats["no_value"]["record"]["wins"]
                         + stats["no_value"]["record"]["losses"], 2)

    def test_pooled_buckets_survive_for_artifact_consumers(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = grade_week(season=2026, week=6, games=_split_games(),
                               paths=_split_paths(Path(tmp)), now=SPLIT_NOW)
        for key in ("record", "families", "priced"):
            self.assertIn(key, stats)
        self.assertIn("no_value", stats)
        self.assertIsInstance(stats["ceiling_hits"], int)
        for key in ("record", "families", "priced"):
            self.assertIn(key, stats["no_value"])


class TestWeeklyDelivery(unittest.TestCase):
    def _stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            return grade_week(games=_games(), paths=_snapshots(d), now=NOW)

    def test_one_post_then_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "delivered.json"
            calls = []

            class _Ok:
                ok = True
                status_code = 204
                error = None

            def fake_post(url, content=None):
                calls.append(content)
                return _Ok()

            stats = self._stats()
            with mock.patch.object(gf, "DELIVERY_MARKER", marker), \
                 mock.patch.dict("os.environ",
                                 {"NFL_DISCORD_WEBHOOK_URL": "https://x/y"}), \
                 mock.patch("nfl_props.notifiers.forecast_discord.post_webhook",
                            side_effect=fake_post):
                self.assertEqual(_post_weekly_recap(stats, force=False), 0)
                self.assertEqual(_post_weekly_recap(stats, force=False), 0)
                self.assertEqual(_post_weekly_recap(stats, force=True), 0)
            self.assertEqual(len(calls), 2)
            self.assertTrue(marker.exists())

    def test_missing_webhook_fails(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("NFL_DISCORD_WEBHOOK_URL", None)
            self.assertEqual(_post_weekly_recap(self._stats(), force=True), 1)


if __name__ == "__main__":
    unittest.main()
