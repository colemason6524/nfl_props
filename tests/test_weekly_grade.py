"""Weekly public grade: week targeting, record/ROI format, delivery."""
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


def _ref(family, side, line, decimal, source="bovada"):
    return {"family": family, "side": side, "line": line,
            "decimal": decimal, "source": source, "status": "PLAYABLE"}


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
                 [_ref("moneyline", "home", None, 1.8),
                  _ref("spread", "home", -3.0, 1.91),
                  _ref("total", "over", 45.5, 1.91)])
    a_early = _fc("g:A", "AWY", "HOM", "2026-09-11T00:30:00+00:00",
                  0.65, 7.0, 47.0,
                  [_ref("moneyline", "home", None, 5.0),
                  _ref("spread", "home", -3.0, 1.91),
                  _ref("total", "over", 45.5, 1.91)])
    b = _fc("g:B", "AAA", "BBB", "2026-09-13T17:00:00+00:00",
            0.40, -5.0, 44.0,
            [_ref("moneyline", "away", None, 2.1),
             _ref("spread", "away", -3.5, 1.91),
             _ref("total", "under", 45.5, 1.91)])
    c = _fc("g:C", "CCC", "DDD", "2026-09-15T00:15:00+00:00",
            0.55, 3.0, 45.0,
            [_ref("moneyline", "home", None, 1.9),
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
        self.assertAlmostEqual(stats["priced"]["units"], 6.44, places=2)

    def test_record_and_families(self):
        stats = self._stats()
        self.assertEqual((stats["record"]["wins"], stats["record"]["losses"],
                          stats["record"]["pushes"]), (7, 0, 2))
        self.assertAlmostEqual(stats["record"]["pct"], 1.0)
        fams = stats["families"]
        self.assertEqual((fams["moneyline"]["wins"],
                          fams["moneyline"]["losses"],
                          fams["moneyline"]["pushes"]), (3, 0, 0))
        self.assertEqual((fams["spread"]["wins"], fams["spread"]["losses"],
                          fams["spread"]["pushes"]), (2, 0, 1))
        self.assertEqual((fams["total"]["wins"], fams["total"]["losses"],
                          fams["total"]["pushes"]), (2, 0, 1))

    def test_format_matches_public_recap(self):
        text = render_weekly_recap(self._stats())
        self.assertIn("NFL Board Recap - Week 2 (Sep 10-14)", text)
        self.assertIn("Week record: 7-0-2 (100.0%)", text)
        self.assertIn("  Moneylines: 3-0", text)
        self.assertIn("  Spreads: 2-0-1", text)
        self.assertIn("  Totals: 2-0-1", text)
        self.assertIn("Priced ROI: +6.44u across 9 plays (+71.6%)", text)
        self.assertIn("Pending: 1 games", text)

    def test_forecast_accuracy_kept_in_artifact(self):
        stats = self._stats()
        self.assertEqual(stats["winner"], {"n": 3, "correct": 3,
                                           "accuracy": 1.0})


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
