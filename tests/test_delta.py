"""Tests for the NFL smart delta alert system (Phase 1).

Covers the pure logic: board-state loading, reference diffing, injury
diffing, tier evaluation, and Discord formatting. Network/VM paths
(fetch_and_store, build_game_forecast) are mocked at the seam.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props import delta as D


def _ref(family, side, line, edge, status):
    return {"family": family, "side": side, "line": line,
            "edge": edge, "status": status}


def _fc(**kw):
    base = {
        "forecast_id": "f1", "away": "SEA", "home": "DEN",
        "projected_margin": 3.0, "projected_total": 44.5,
        "injury_debit_pts_home": 0.0, "injury_debit_pts_away": 0.0,
        "injury_trace": [], "references": [],
        "priced_qb_home": "QB1", "priced_qb_away": "QB2",
        "qb_substituted_home": False, "qb_substituted_away": False,
        "available": True,
    }
    base.update(kw)
    return base


def _new_fc(**kw):
    return SimpleNamespace(**_fc(**kw))


class LoadBoardStateTest(unittest.TestCase):
    def _write_board(self, tmpdir, age_hours, forecasts):
        gen = (datetime.now(timezone.utc)
               - timedelta(hours=age_hours)).isoformat()
        payload = {"generated_at_utc": gen, "forecasts": forecasts}
        p = Path(tmpdir) / "nfl_forecast_20261013T120000Z.json"
        p.write_text(json.dumps(payload))
        return p

    def test_fresh_board_loads(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self._write_board(td, 2, [_fc()])
            got = D.load_board_state(history_dir=Path(td))
            self.assertIsNotNone(got)
            self.assertEqual(len(got[0]), 1)

    def test_stale_board_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self._write_board(td, 24, [_fc()])
            self.assertIsNone(D.load_board_state(history_dir=Path(td)))

    def test_missing_board_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(D.load_board_state(history_dir=Path(td)))


class DiffReferencesTest(unittest.TestCase):
    def test_edge_shift_detected(self):
        old = [_ref("spread", "home", -3.5, 0.061, "PLAYABLE")]
        new = [_ref("spread", "home", -3.5, 0.012, "PLAYABLE")]
        (d,) = D.diff_references(old, new)
        self.assertAlmostEqual(d.edge_delta, -0.049, places=3)
        self.assertFalse(d.flipped)

    def test_flip_detected(self):
        old = [_ref("spread", "home", -3.5, 0.061, "PLAYABLE")]
        new = [_ref("spread", "home", -3.5, -0.01, "NO_VALUE")]
        (d,) = D.diff_references(old, new)
        self.assertTrue(d.flipped)

    def test_added_ref_ignored(self):
        old = [_ref("spread", "home", -3.5, 0.061, "PLAYABLE")]
        new = old + [_ref("total", "over", 44.5, 0.02, "PLAYABLE")]
        self.assertEqual(len(D.diff_references(old, new)), 1)


class DiffInjuriesTest(unittest.TestCase):
    def test_qb_out_detected(self):
        old = _fc(priced_qb_home="QB1", qb_substituted_home=False)
        new = _new_fc(priced_qb_home="QB9", qb_substituted_home=True)
        changes = D.diff_injuries(old, new)
        self.assertTrue(any("OUT" in c and "QB" in c for c in changes))

    def test_qb_back_active_detected(self):
        old = _fc(priced_qb_home="QB9", qb_substituted_home=True)
        new = _new_fc(priced_qb_home="QB1", qb_substituted_home=False)
        changes = D.diff_injuries(old, new)
        self.assertTrue(any("ACTIVE" in c for c in changes))

    def test_no_change_no_report(self):
        old = _fc()
        new = _new_fc()
        self.assertEqual(D.diff_injuries(old, new), [])

    def test_trace_new_out(self):
        old = _fc(injury_trace=[])
        new = _new_fc(injury_trace=[{
            "team": "DEN", "slot": "h", "debit_pts": -0.4,
            "attribution": [{"position": "WR", "raw": -0.4,
                             "applied": -0.4}],
            "priced_qb": "QB1", "qb_substituted": False}])
        changes = D.diff_injuries(old, new)
        self.assertTrue(any("WR" in c and "OUT" in c for c in changes))


class EvaluateTierTest(unittest.TestCase):
    def _delta(self, **kw):
        d = D.GameDelta(forecast_id="f1", away="SEA", home="DEN",
                        margin_old=3.0, margin_new=3.0,
                        total_old=44.5, total_new=44.5)
        for k, v in kw.items():
            setattr(d, k, v)
        return d

    def test_quiet_stays_zero(self):
        d = self._delta()
        tier, reasons = D.evaluate_tier(d)
        self.assertEqual(tier, 0)
        self.assertEqual(reasons, [])

    def test_edge_shift_tier1(self):
        d = self._delta(ref_deltas=[D.ReferenceDelta(
            "spread", "home", -3.5, -3.5, 0.061, 0.012,
            "PLAYABLE", "PLAYABLE")])
        tier, reasons = D.evaluate_tier(d)
        self.assertEqual(tier, 1)
        self.assertTrue(any("edge" in r for r in reasons))

    def test_flip_tier1(self):
        d = self._delta(ref_deltas=[D.ReferenceDelta(
            "spread", "home", -3.5, -3.5, 0.061, -0.01,
            "PLAYABLE", "NO_VALUE")])
        tier, _ = D.evaluate_tier(d)
        self.assertEqual(tier, 1)

    def test_margin_move_tier2(self):
        d = self._delta(margin_old=3.0, margin_new=-3.0)
        tier, reasons = D.evaluate_tier(d)
        self.assertEqual(tier, 2)
        self.assertTrue(any("margin" in r for r in reasons))

    def test_margin_just_under_stays_tier1_or_zero(self):
        d = self._delta(margin_old=3.0, margin_new=-1.5)
        tier, _ = D.evaluate_tier(d)
        self.assertLess(tier, 2)

    def test_key_qb_news_tier1_without_edge_move(self):
        d = self._delta(injury_changes=["QB9 (QB, DEN) OUT — late inactive"])
        tier, reasons = D.evaluate_tier(d)
        self.assertEqual(tier, 1)
        self.assertTrue(any("key player" in r for r in reasons))

    def test_both_directions(self):
        # "expected out but plays" must also fire
        d = self._delta(injury_changes=["QB1 (QB, DEN) ACTIVE — was OUT"])
        tier, _ = D.evaluate_tier(d)
        self.assertEqual(tier, 1)


class FormatAlertTest(unittest.TestCase):
    def test_format_matches_spec_shape(self):
        now_et = datetime(2026, 10, 15, 18, 45,
                          tzinfo=timezone.utc).astimezone()
        d = D.GameDelta(
            forecast_id="f1", away="BUF", home="LAR",
            margin_old=3.0, margin_new=1.0,
            total_old=54.5, total_new=52.0,
            injury_changes=["Josh Allen (ankle) INACTIVE"],
            ref_deltas=[D.ReferenceDelta(
                "spread", "home", -3.5, -1.0, 0.061, 0.012,
                "PLAYABLE", "NO_VALUE")],
            tier=1, reasons=["edge shift"])
        text = D.format_game_delta(d, now_et)
        self.assertIn("BUF @ LAR", text)
        self.assertIn("Josh Allen", text)
        self.assertIn("Was: PLAYABLE → Now: NO_VALUE", text)
        self.assertIn("+6.1%", text)
        self.assertIn("+1.2%", text)

    def test_empty_when_nothing_firing(self):
        d = D.GameDelta(forecast_id="f1", away="SEA", home="DEN",
                        margin_old=3.0, margin_new=3.0,
                        total_old=44.5, total_new=44.5, tier=0)
        self.assertEqual(D.format_alert([d], datetime.now(timezone.utc)),
                         "")

    def test_tier2_first_in_batch(self):
        t2 = D.GameDelta(forecast_id="a", away="A", home="B",
                         margin_old=3.0, margin_new=-3.0,
                         total_old=44.5, total_new=44.5, tier=2)
        t1 = D.GameDelta(forecast_id="b", away="C", home="D",
                         margin_old=1.0, margin_new=1.5,
                         total_old=44.5, total_new=44.5,
                         ref_deltas=[D.ReferenceDelta(
                             "spread", "home", -3.5, -3.5, 0.06, 0.01,
                             "PLAYABLE", "PLAYABLE")],
                         tier=1)
        text = D.format_alert([t1, t2], datetime.now(timezone.utc))
        self.assertLess(text.index("A @ B"), text.index("C @ D"))


class RunDeltaCheckTest(unittest.TestCase):
    def test_no_board_is_fail_open(self):
        with mock.patch.object(D, "load_board_state", return_value=None):
            res = D.run_delta_check()
            self.assertFalse(res.posted)
            self.assertIsNotNone(res.error)
            self.assertEqual(res.message, "")

    def test_pull_failure_is_fail_open(self):
        old = [_fc()]
        with mock.patch.object(
                D, "load_board_state",
                return_value=(old, datetime.now(timezone.utc))), \
             mock.patch("nfl_props.forecasting.load_forecast_state",
                        return_value={"personnel": {}}), \
             mock.patch.object(D, "refresh_state_personnel",
                               side_effect=RuntimeError("pull failed")):
            res = D.run_delta_check(refresh_odds=False)
            self.assertFalse(res.posted)
            self.assertIn("pull failed", res.error)

    def test_silent_when_nothing_changed(self):
        old = [_fc(references=[
            _ref("spread", "home", -3.5, 0.061, "PLAYABLE")])]
        new_fc = _new_fc(references=[
            _ref("spread", "home", -3.5, 0.061, "PLAYABLE")])
        with mock.patch.object(
                D, "load_board_state",
                return_value=(old, datetime.now(timezone.utc))), \
             mock.patch("nfl_props.forecasting.load_forecast_state",
                        return_value={"personnel": {}}), \
             mock.patch.object(D, "refresh_state_personnel",
                               return_value={}), \
             mock.patch.object(D, "recompute_game", return_value=new_fc):
            res = D.run_delta_check(refresh_odds=False)
            self.assertFalse(res.posted)
            self.assertEqual(res.message, "")
            self.assertEqual(res.games_checked, 1)
            self.assertEqual(res.games_firing, 0)

    def test_fires_on_edge_shift(self):
        old = [_fc(references=[
            _ref("spread", "home", -3.5, 0.061, "PLAYABLE")])]
        new_fc = _new_fc(references=[
            _ref("spread", "home", -1.0, 0.005, "NO_VALUE")],
            projected_margin=1.0)
        with mock.patch.object(
                D, "load_board_state",
                return_value=(old, datetime.now(timezone.utc))), \
             mock.patch("nfl_props.forecasting.load_forecast_state",
                        return_value={"personnel": {}}), \
             mock.patch.object(D, "refresh_state_personnel",
                               return_value={}), \
             mock.patch.object(D, "recompute_game", return_value=new_fc):
            res = D.run_delta_check(refresh_odds=False)
            self.assertEqual(res.games_firing, 1)
            self.assertIn("SEA @ DEN", res.message)
            self.assertFalse(res.posted)  # no discord without webhook




class UnpricedFlipTest(unittest.TestCase):
    def test_unpriced_transitions_do_not_flip(self):
        from nfl_props.delta import ReferenceDelta
        for old_s, new_s in (("NO_VALUE", "UNPRICED"),
                             ("UNPRICED", "PLAYABLE"),
                             ("UNPRICED", "UNPRICED")):
            rd = ReferenceDelta("spread", "home", -3.5, -3.5,
                                0.05, None, old_s, new_s)
            self.assertFalse(rd.flipped, f"{old_s}->{new_s}")

    def test_priced_flip_still_fires(self):
        from nfl_props.delta import ReferenceDelta
        rd = ReferenceDelta("spread", "home", -3.5, -3.5,
                            0.05, -0.01, "PLAYABLE", "NO_VALUE")
        self.assertTrue(rd.flipped)

if __name__ == "__main__":
    unittest.main()
