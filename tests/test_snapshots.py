"""Tests for wave boards + pregame snapshots (latest-snapshot grading).

Covers nfl_props.snapshots (write/read/latest-before/cleanup), the
--wave filter + label in run_forecast_board, and the grading override
in grade_forecast._with_snapshot_pick.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props import snapshots as S

KICKOFF = datetime(2026, 10, 19, 0, 20, tzinfo=timezone.utc)  # 8:20pm ET
T0 = datetime(2026, 10, 18, 13, 30, tzinfo=timezone.utc)   # 9:30am ET early
T1 = datetime(2026, 10, 18, 18, 30, tzinfo=timezone.utc)   # 2:30pm ET wave
T2 = datetime(2026, 10, 18, 22, 45, tzinfo=timezone.utc)   # 6:45pm ET delta
T3 = datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc)     # after kickoff


def _pick_data(**kw):
    base = {
        "away": "KC", "home": "BUF",
        "kickoff_utc": KICKOFF.isoformat(),
        "picks": [{
            "family": "spread", "side": "away", "line": -3.0,
            "american": -110, "decimal": 1.909, "p_model": 0.58,
            "p_market": 0.52, "edge": 0.06, "ev": 0.05,
            "status": "PLAYABLE", "source": "bovada",
        }],
        "model": {"p_home_win": 0.44, "projected_margin": -3.2,
                  "projected_total": 47.5},
    }
    base.update(kw)
    return base


class SnapshotIOTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp(prefix="nfl_snap_"))
        self._patcher = mock.patch.object(S, "SNAPSHOTS_DIR", self._tmp)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_write_snapshot_format(self):
        path = S.write_snapshot("g:1", _pick_data(), "early", now=T0)
        self.assertTrue(path.is_file())
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in ("game_id", "picks", "model", "timestamp", "source",
                    "away", "home", "kickoff_utc"):
            self.assertIn(key, payload, key)
        self.assertEqual(payload["game_id"], "g:1")
        self.assertEqual(payload["source"], "early")
        self.assertEqual(payload["timestamp"], T0.isoformat())
        self.assertEqual(len(payload["picks"]), 1)
        self.assertEqual(payload["picks"][0]["family"], "spread")

    def test_write_snapshot_bad_source(self):
        with self.assertRaises(ValueError):
            S.write_snapshot("g:1", _pick_data(), "bogus", now=T0)

    def test_write_snapshot_overwrites(self):
        S.write_snapshot("g:1", _pick_data(), "early", now=T0)
        data = _pick_data()
        data["picks"][0]["edge"] = 0.09
        S.write_snapshot("g:1", data, "wave", now=T1)
        payload = json.loads(
            S.snapshot_path("g:1", "2026-10-18").read_text(
                encoding="utf-8"))
        self.assertEqual(payload["source"], "wave")
        self.assertEqual(payload["picks"][0]["edge"], 0.09)

    def test_latest_snapshot_before(self):
        # Snapshots overwrite by game_id: the stored file is always the
        # latest write; latest_snapshot_before honors the kickoff bound.
        S.write_snapshot("g:1", _pick_data(), "early", now=T0)
        S.write_snapshot("g:1", _pick_data(), "wave", now=T1)
        S.write_snapshot("g:1", _pick_data(), "delta", now=T2)
        latest = S.latest_snapshot_before("g:1", KICKOFF)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["source"], "delta")
        self.assertEqual(latest["timestamp"], T2.isoformat())

    def test_snapshot_after_kickoff_ignored(self):
        S.write_snapshot("g:1", _pick_data(), "delta", now=T3)
        self.assertIsNone(S.latest_snapshot_before("g:1", KICKOFF))

    def test_no_snapshots_returns_none(self):
        self.assertIsNone(S.latest_snapshot_before("g:nope", KICKOFF))

    def test_latest_wins_across_days(self):
        # Snapshots from an earlier ET date dir still lose to newer ones.
        S.write_snapshot("g:1", _pick_data(), "early",
                         now=T0 - timedelta(days=1))
        S.write_snapshot("g:1", _pick_data(), "wave", now=T1)
        latest = S.latest_snapshot_before("g:1", KICKOFF)
        self.assertEqual(latest["source"], "wave")

    def test_cleanup_snapshots(self):
        S.write_snapshot("g:1", _pick_data(), "early", now=T0)
        old = self._tmp / "2026-09-01"
        old.mkdir(parents=True)
        (old / "g:old.json").write_text("{}", encoding="utf-8")
        removed = S.cleanup_snapshots(retention_days=30)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(S.snapshot_path("g:1", "2026-10-18").exists())

    def test_snapshot_from_forecast_dict(self):
        fc = {
            "forecast_id": "g:7", "away": "KC", "home": "BUF",
            "kickoff_utc": KICKOFF.isoformat(), "available": True,
            "p_home_win": 0.44, "projected_margin": -3.2,
            "projected_total": 47.5, "winner_pick": "away",
            "winner_confidence": 0.56,
            "references": [{
                "family": "moneyline", "side": "away", "line": None,
                "american": 150, "decimal": 2.5, "p_model": 0.44,
                "p_market": 0.40, "edge": 0.04, "ev": 0.10,
                "status": "PLAYABLE", "source": "polymarket",
            }],
        }
        data = S.snapshot_from_forecast(fc)
        self.assertEqual(data["game_id"], "g:7")
        self.assertEqual(data["picks"][0]["family"], "moneyline")
        self.assertEqual(data["model"]["p_home_win"], 0.44)

    def test_ref_pick_preserves_note(self):
        # The weekly recap counts DEAD_LINE_SUSPECT demotions from the ref
        # note, so a snapshot must not drop it when grading prefers the
        # snapshot over the board artifact.
        pick = S._ref_pick({"family": "spread", "side": "home",
                            "line": -3.0, "decimal": 1.909,
                            "status": "NO_VALUE",
                            "note": "DEAD_LINE_SUSPECT"})
        self.assertEqual(pick["note"], "DEAD_LINE_SUSPECT")

    def test_note_survives_snapshot_roundtrip(self):
        from nfl_props.forecast import DEAD_LINE_SUSPECT
        data = S.snapshot_from_forecast({
            "forecast_id": "g:9", "away": "KC", "home": "BUF",
            "kickoff_utc": KICKOFF.isoformat(), "available": True,
            "references": [{"family": "spread", "side": "home",
                            "line": -3.0, "decimal": 1.909,
                            "status": "NO_VALUE", "source": "bovada",
                            "note": DEAD_LINE_SUSPECT}],
        })
        S.write_snapshot("g:9", data, "wave", now=T1)
        latest = S.latest_snapshot_before("g:9", KICKOFF)
        self.assertIn(DEAD_LINE_SUSPECT, latest["picks"][0]["note"])

    def test_write_board_snapshots_skips_unavailable(self):
        fcs = [
            {"forecast_id": "g:1", "available": True, "away": "A",
             "home": "B", "references": []},
            {"forecast_id": "g:2", "available": False, "away": "C",
             "home": "D", "references": []},
        ]
        n = S.write_board_snapshots(fcs, "wave", now=T1)
        self.assertEqual(n, 1)
        self.assertTrue(S.snapshot_path("g:1", "2026-10-18").exists())
        self.assertFalse(S.snapshot_path("g:2", "2026-10-18").exists())


class WaveFilterTest(unittest.TestCase):
    def test_upcoming_filters_started_games(self):
        import run_forecast_board as RFB
        from types import SimpleNamespace

        def fc(kickoff):
            return SimpleNamespace(
                kickoff_utc=kickoff.isoformat() if kickoff else None)

        now = T1  # 2:30pm ET wave
        games = [
            fc(datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)),   # 1pm: out
            fc(datetime(2026, 10, 18, 20, 15, tzinfo=timezone.utc)),  # 4:15: in
            fc(datetime(2026, 10, 19, 0, 20, tzinfo=timezone.utc)),   # 8:20: in
            fc(None),  # unparseable: fail-open, kept
        ]
        kept = RFB._upcoming_forecasts(games, now)
        self.assertEqual(len(kept), 3)
        self.assertNotIn(games[0], kept)

    def test_upcoming_respects_15min_buffer(self):
        import run_forecast_board as RFB
        from types import SimpleNamespace

        now = T1
        # Kickoff 10 min after now -> excluded; 20 min -> kept.
        soon = SimpleNamespace(
            kickoff_utc=(now + timedelta(minutes=10)).isoformat())
        later = SimpleNamespace(
            kickoff_utc=(now + timedelta(minutes=20)).isoformat())
        kept = RFB._upcoming_forecasts([soon, later], now)
        self.assertEqual(kept, [later])

    def test_wave_render_label(self):
        from nfl_props.forecast_output import render_board

        text = render_board([], {"games": 0, "available": 0, "priced": 0},
                             now=T1, wave=True)
        self.assertIn("\U0001f30a WAVE — Remaining games (2:30pm ET)", text)

    def test_wave_render_changes_note(self):
        from nfl_props.forecast_output import render_board

        changes = [{
            "forecast_id": "g:1", "matchup": "KC @ BUF",
            "kind": "MATERIAL_CHANGE", "reasons": ["margin +3.2 -> +5.1"]}]
        text = render_board([], {"games": 0, "available": 0, "priced": 0},
                             now=T1, wave=True, changes=changes)
        self.assertIn("\u21bb Updated from early board (1):", text)
        self.assertIn("KC @ BUF", text)

    def test_non_wave_render_unchanged(self):
        from nfl_props.forecast_output import render_board

        text = render_board([], {"games": 0, "available": 0, "priced": 0},
                             now=T1)
        self.assertTrue(text.startswith("\U0001f4b0 POO DOLLA BETS \u2014 NFL"))
        self.assertIn("No plays today.", text)
        self.assertNotIn("WAVE", text)


class GradingOverrideTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp(prefix="nfl_snap_"))
        self._patcher = mock.patch.object(S, "SNAPSHOTS_DIR", self._tmp)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fc(self):
        return {
            "forecast_id": "g:1", "away": "KC", "home": "BUF",
            "p_home_win": 0.44, "projected_margin": -3.2,
            "_kickoff": KICKOFF,
            "references": [{
                "family": "spread", "side": "away", "line": -3.0,
                "decimal": 1.909, "edge": 0.06, "status": "PLAYABLE",
                "source": "bovada",
            }],
        }

    def test_snapshot_overrides_references(self):
        import grade_forecast as G

        fc = self._fc()
        data = _pick_data()
        data["picks"][0]["line"] = -6.5  # delta-updated line
        data["picks"][0]["status"] = "NO_VALUE"
        S.write_snapshot("g:1", data, "delta", now=T2)
        out = G._with_snapshot_pick(fc)
        self.assertEqual(out["references"][0]["line"], -6.5)
        self.assertEqual(out["references"][0]["status"], "NO_VALUE")
        self.assertEqual(out["_snapshot_source"], "delta")
        # Forecast-quality fields untouched.
        self.assertEqual(out["p_home_win"], 0.44)
        self.assertEqual(out["projected_margin"], -3.2)

    def test_no_snapshot_falls_back_to_board(self):
        import grade_forecast as G

        fc = self._fc()
        out = G._with_snapshot_pick(fc)
        self.assertIs(out["references"], fc["references"])
        self.assertNotIn("_snapshot_source", out)

    def test_post_kickoff_snapshot_ignored(self):
        import grade_forecast as G

        S.write_snapshot("g:1", _pick_data(), "delta", now=T3)
        fc = self._fc()
        out = G._with_snapshot_pick(fc)
        self.assertIs(out["references"], fc["references"])

    def test_grade_reference_works_on_snapshot_pick(self):
        import grade_forecast as G

        # BUF 27, KC 24, home -3: 27-3 == 24 -> push.
        ref = {"family": "spread", "side": "home", "line": -3.0}
        self.assertEqual(G.grade_reference(ref, 27.0, 24.0), "push")
        # Away KC -3, KC 30 @ BUF 20: 30-3 > 20 -> win.
        ref2 = {"family": "spread", "side": "away", "line": -3.0}
        self.assertEqual(G.grade_reference(ref2, 20.0, 30.0), "win")


if __name__ == "__main__":
    unittest.main()
