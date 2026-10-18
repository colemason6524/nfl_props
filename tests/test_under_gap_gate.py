"""Totals calibration 2026-10-18: UNDER gap gate regression tests.

Marginal UNDERs (model-under gap below NFL_UNDER_MIN_GAP, default 2.5) get
no pick at all (side=None + UNDER_GAP_BELOW_FLOOR), so they can never be
PLAYABLE downstream. OVERs and spread refs are untouched by the gate.
"""
import importlib
import os
import unittest
from unittest.mock import patch

from nfl_props import forecast as fc_mod
from nfl_props.forecast import build_game_forecast
from nfl_props.forecasting import FEATURE_SETS
from nfl_props.references import PLAYABLE
from nfl_props.sources.bovada import LiveGame, TwoWayPrice


def _coefs(features, intercept, **special):
    values = [intercept]
    for name in features:
        values.append(float(special.get(name, 0.0)))
    return values


def _fake_state():
    winner_features = list(FEATURE_SETS["full"]["winner"])
    margin_features = list(FEATURE_SETS["full"]["margin"])
    total_features = list(FEATURE_SETS["full"]["total"])
    teams = {}
    for team, off in (("HOM", 0.10), ("AWY", 0.00)):
        teams[team] = {
            "off": off, "def": off, "off_pass": off, "def_pass": off,
            "off_rush": off, "def_rush": off, "pace": 63.0,
            "last_qb_id": None, "carryover_applied": False,
            "games_current_season": 4,
        }
    return {
        "model_version": "nfl-forecast-v1",
        "league": {"pace": 63.0},
        "teams": teams,
        "qbs": {},
        "winner": {
            "model_version": "nfl-winner-logit-v1",
            "features": winner_features,
            "coefs": _coefs(winner_features, 0.2, off_h=1.0, off_a=-1.0),
        },
        "margin": {
            "model_version": "nfl-margin-ridge-v1",
            "features": margin_features,
            "coefs": _coefs(margin_features, 2.0, off_h=1.0, off_a=-1.0),
            "resid_sd": 10.0,
            "resid": [float(x) for x in range(-14, 15)],
        },
        "total": {
            "model_version": "nfl-total-ridge-v1",
            "features": total_features,
            "coefs": _coefs(total_features, 45.0),
            "resid_sd": 10.0,
            "resid": [float(x) for x in range(-14, 15)],
        },
    }


def _game():
    return {
        "game_id": 1, "season": 2026, "week": 2, "game_type": "REG",
        "away_team": "AWY", "home_team": "HOM",
        "kickoff_utc": "2026-09-13T17:00:00+00:00",
        "neutral_site": False, "dome": False, "div_game": 0,
        "home_rest": 7, "away_rest": 7, "venue": "Test Field",
        "temp_f": None, "wind_mph": None,
    }


def _bovada(total_line, spread_line=-3.0):
    return LiveGame(
        event_id="e1", away="AWY", home="HOM", away_name="Away",
        home_name="Home", start_time_utc="2026-09-13T17:00:00+00:00",
        link="", league_path="NFL",
        moneyline=TwoWayPrice(None, 0, 0, 1.9, 1.9),
        spread=TwoWayPrice(spread_line, 0, 0, 1.9, 1.9),
        game_total=TwoWayPrice(total_line, 0, 0, 1.9, 1.9))


class TestUnderGapGate(unittest.TestCase):
    def setUp(self):
        self.state = _fake_state()
        self.mu = build_game_forecast(_game(), self.state).projected_total
        self._patch = patch.object(fc_mod, "UNDER_MIN_GAP", 2.5)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _total_ref(self, gap):
        """gap = line - mu; positive gap leans under."""
        fc = build_game_forecast(
            _game(), self.state, bovada_game=_bovada(self.mu + gap))
        refs = {r.family: r for r in fc.references}
        return refs["total"], fc

    def test_marginal_under_blocked(self):
        ref, _ = self._total_ref(1.0)
        self.assertIsNone(ref.side)
        self.assertIsNone(ref.p_model)
        self.assertNotEqual(ref.status, PLAYABLE)
        self.assertTrue(ref.note and "UNDER_GAP_BELOW_FLOOR" in ref.note)

    def test_deep_under_passes(self):
        ref, _ = self._total_ref(4.0)
        self.assertEqual(ref.side, "under")
        self.assertIsNotNone(ref.p_model)

    def test_overs_unaffected(self):
        for gap in (-0.5, -2.0):
            ref, _ = self._total_ref(gap)
            self.assertEqual(ref.side, "over", f"gap={gap}")

    def test_spread_ref_unaffected_by_blocked_under(self):
        _, fc = self._total_ref(1.0)
        refs = {r.family: r for r in fc.references}
        self.assertIsNone(refs["total"].side)
        self.assertIsNotNone(refs["spread"].side)

    def test_env_defaults(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("NFL_UNDER_MIN_GAP", "NFL_TOTAL_RESID_SCALE")}
        with patch.dict(os.environ, env, clear=True):
            importlib.reload(fc_mod)
        try:
            self.assertEqual(fc_mod.UNDER_MIN_GAP, 2.5)
            self.assertEqual(fc_mod.TOTAL_RESID_SCALE, 1.15)
        finally:
            importlib.reload(fc_mod)


if __name__ == "__main__":
    unittest.main()
