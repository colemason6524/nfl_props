"""Dead-line ceiling 2026-10-21: forecast-path EV/edge guard.

An |edge| >= EDGE_CEILING (0.20) or |ev| >= EV_CEILING (0.30) on the forecast
path is a broken/dead line, not a betting opinion. Those refs are demoted
PLAYABLE -> NO_VALUE with DEAD_LINE_SUSPECT; they stay on the board artifact
and in the ledger so the hit count remains visible and countable.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nfl_props.forecast as fc_mod
from nfl_props.forecast import (
    DEAD_LINE_SUSPECT,
    EDGE_CEILING,
    EV_CEILING,
    GameForecast,
    apply_edge_ceiling,
    build_game_forecast,
)
from nfl_props.forecasting import FEATURE_SETS
from nfl_props.references import (
    NO_VALUE,
    PLAYABLE,
    ForecastReference,
    build_reference,
)
from nfl_props.sources.bovada import LiveGame, TwoWayPrice


def _ref(family="spread", status=PLAYABLE, edge=0.05, ev=0.05,
         p_model=0.55, side="home", line=-3.5):
    return ForecastReference(
        family=family, source="bovada", sources=["bovada"], side=side,
        line=line, american=-110, decimal=1.909, p_model=p_model,
        p_market=0.50, edge=edge, ev=ev, status=status, n_sources=1)


def _forecast(refs):
    return GameForecast(
        forecast_id="g:test", game_id="g:test", away="AWY", home="HOM",
        away_name="Away", home_name="Home",
        kickoff_utc="2026-10-19T17:00:00+00:00", season=2026, week=6,
        game_type="REG", neutral_site=False, venue="Test Field",
        available=True, references=refs)


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
        "game_id": 1, "season": 2026, "week": 6, "game_type": "REG",
        "away_team": "AWY", "home_team": "HOM",
        "kickoff_utc": "2026-10-19T17:00:00+00:00",
        "neutral_site": False, "dome": False, "div_game": 0,
        "home_rest": 7, "away_rest": 7, "venue": "Test Field",
        "temp_f": None, "wind_mph": None,
    }


def _bovada():
    return LiveGame(
        event_id="e1", away="AWY", home="HOM", away_name="Away",
        home_name="Home", start_time_utc="2026-10-19T17:00:00+00:00",
        link="", league_path="NFL",
        moneyline=TwoWayPrice(None, 0, 0, 1.9, 1.9),
        spread=TwoWayPrice(-3.0, 0, 0, 1.9, 1.9),
        game_total=TwoWayPrice(45.0, 0, 0, 1.9, 1.9))


class TestDeadLineCeiling(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(EDGE_CEILING, 0.20)
        self.assertEqual(EV_CEILING, 0.30)

    def test_absurd_edge_demoted(self):
        ref = _ref(edge=0.25, ev=0.50, p_model=0.9769)
        self.assertTrue(apply_edge_ceiling(ref, "WAS @ SF"))
        self.assertEqual(ref.status, NO_VALUE)
        self.assertTrue(ref.note and DEAD_LINE_SUSPECT in ref.note)

    def test_absurd_ev_demoted(self):
        ref = _ref(edge=0.15, ev=0.35)
        self.assertTrue(apply_edge_ceiling(ref, "AWY @ HOM"))
        self.assertEqual(ref.status, NO_VALUE)
        self.assertTrue(ref.note and DEAD_LINE_SUSPECT in ref.note)

    def test_negative_edge_is_also_absurd(self):
        ref = _ref(edge=-0.25, ev=-0.40)
        self.assertTrue(apply_edge_ceiling(ref, "AWY @ HOM"))
        self.assertEqual(ref.status, NO_VALUE)

    def test_normal_ref_unchanged(self):
        ref = _ref(edge=0.05, ev=0.05)
        self.assertFalse(apply_edge_ceiling(ref, "AWY @ HOM"))
        self.assertEqual(ref.status, PLAYABLE)
        self.assertIsNone(ref.note)

    def test_already_no_value_untouched(self):
        ref = _ref(status=NO_VALUE, edge=0.45, ev=0.90)
        self.assertFalse(apply_edge_ceiling(ref, "AWY @ HOM"))
        self.assertEqual(ref.status, NO_VALUE)
        self.assertIsNone(ref.note)

    def test_hit_logs_board_context(self):
        ref = _ref(family="spread", edge=0.4719, ev=0.9345, p_model=0.9769,
                   side="home", line=17.5)
        err = sys.stderr
        import io
        buf = io.StringIO()
        sys.stderr = buf
        try:
            apply_edge_ceiling(ref, "WAS @ SF")
        finally:
            sys.stderr = err
        line = buf.getvalue()
        for token in ("WAS @ SF", "spread", "home", "17.5", "0.9769",
                      "0.4719", "0.9345", DEAD_LINE_SUSPECT):
            self.assertIn(token, line)

    def test_build_game_forecast_applies_guard(self):
        # A zero/negative ceiling demotes every PLAYABLE ref; wiring check.
        with patch.object(fc_mod, "EDGE_CEILING", -1.0):
            fc = build_game_forecast(_game(), _fake_state(),
                                     bovada_game=_bovada())
        priced = [r for r in fc.references if r.decimal is not None
                  and r.ev is not None]
        self.assertTrue(priced)
        for ref in priced:
            self.assertEqual(ref.status, NO_VALUE, ref)
            self.assertTrue(ref.note and DEAD_LINE_SUSPECT in ref.note)

    def test_demoted_row_stays_in_board_artifact(self):
        from nfl_props import forecast_output
        ref = _ref(family="spread", edge=0.4719, ev=0.9345,
                   p_model=0.9769, side="home", line=17.5)
        fc = _forecast([ref])
        apply_edge_ceiling(ref, "AWY @ HOM")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(forecast_output, "FORECAST_HISTORY_DIR",
                              Path(tmp)):
                path = forecast_output.export_history(
                    [fc], {"games": 1, "priced": 1})
            payload = json.loads(Path(path).read_text())
        rows = payload["forecasts"][0]["references"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], NO_VALUE)
        self.assertIn(DEAD_LINE_SUSPECT, rows[0]["note"] or "")


if __name__ == "__main__":
    unittest.main()
