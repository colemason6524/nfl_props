"""Golden-payload tests for the NFL house format (board + recap card).

The spec (HOUSE_FORMAT_SPEC_DRAFT.md) governs content and shape, not the
rendering channel, so these tests pin the exact page text both renderers
produce: one brand block, one slate line, starred PLAYS / eyed LEANS only,
footer with the last slate + last 7d, and the fixed sign-off.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from nfl_props import house
from nfl_props.forecast import GameForecast
from nfl_props.forecast_output import render_board as render_forecast_board
from nfl_props.output import render_board as render_legacy_board
from nfl_props.pricing import american_to_decimal, decimal_to_american
from nfl_props.references import (NO_VALUE, PLAYABLE, UNPRICED,
                                  ForecastReference)
from nfl_props.tiers import Candidate

NOW = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)  # Wed 10:30 ET


def _ref(family, side, line, decimal, status, p_model, edge, ev=0.05):
    return ForecastReference(
        family=family, source="bovada", sources=["bovada"], side=side,
        line=line,
        american=(decimal_to_american(decimal) if decimal is not None else None),
        decimal=decimal, p_model=p_model, p_market=0.5, edge=edge, ev=ev,
        status=status, n_sources=1)


def _fc(fid, away, home, refs):
    return GameForecast(
        forecast_id=fid, game_id=fid, away=away, home=home,
        away_name=away, home_name=home,
        kickoff_utc="2026-10-21T17:00:00+00:00", season=2026, week=7,
        game_type="REG", neutral_site=False, venue="Test Field",
        available=True, references=refs)


def _board():
    kc_buf = _fc("g:1", "KC", "BUF", [
        _ref("moneyline", "away", None, 2.10, NO_VALUE, 0.52, -0.01),
        _ref("spread", "home", -4.5, 1.9091, PLAYABLE, 0.57, 0.05),
        _ref("total", "over", 47.5, None, UNPRICED, 0.55, None),
    ])
    dal_phi = _fc("g:2", "DAL", "PHI", [
        _ref("total", "over", 47.5, 1.9091, PLAYABLE, 0.78, 0.20),
    ])
    return [kc_buf, dal_phi]


GOLDEN_BOARD = """\
\U0001f4b0 POO DOLLA BETS \u2014 NFL
Wed Oct 21 \u00b7 2 games: KC @ BUF, DAL @ PHI

\u2b50 PLAYS
BUF -4.5 (-110) \u00b7 57%

\U0001f440 LEANS
Over 47.5 (-110) \u00b7 78%

\U0001f4ca Last night: n/a \u00b7 Last 7d: n/a
thank you Lawler"""


class ForecastBoardFormatTest(unittest.TestCase):
    def test_golden_page(self):
        text = render_forecast_board(_board(), {"games": 2}, now=NOW)
        self.assertEqual(text, GOLDEN_BOARD)

    def test_pick_grammar(self):
        text = render_forecast_board(_board(), {}, now=NOW)
        self.assertIn("BUF -4.5 (-110) \u00b7 57%", text)
        self.assertIn("Over 47.5 (-110) \u00b7 78%", text)

    def test_only_plays_and_leans_print(self):
        text = render_forecast_board(_board(), {}, now=NOW)
        for internal in ("NO_VALUE", "PLAYABLE", "UNPRICED", "status",
                         "edge", "EV", "mkt", "proj"):
            self.assertNotIn(internal, text)

    def test_price_shown_is_price_graded(self):
        """Every printed price round-trips to the decimal grading settles on."""
        import re
        forecasts = _board()
        text = render_forecast_board(forecasts, {}, now=NOW)
        graded = [r.decimal for fc in forecasts for r in fc.references
                  if r.decimal is not None]
        shown = re.findall(r"\((\+?-?\d+)\)", text)
        self.assertEqual(len(shown), 2)  # exactly the two playable rows
        for price_text in shown:
            american = int(price_text)
            self.assertTrue(
                any(abs(american_to_decimal(american) - d) < 1e-3
                    for d in graded), price_text)

    def test_footer_windows_and_sign_off(self):
        results = {"last_night": {"wins": 6, "losses": 3, "pushes": 0,
                                  "units": 2.4, "plays": 9},
                   "last_7d": {"wins": 12, "losses": 4, "pushes": 1,
                               "units": 8.1, "plays": 17},
                   "last_30d": {"wins": 30, "losses": 20, "units": 9.0,
                                "plays": 50}}
        text = render_forecast_board(_board(), {}, now=NOW, results=results)
        self.assertIn("\U0001f4ca Last night: 6-3, +2.4u \u00b7 "
                      "Last 7d: 12-4-1, +8.1u \u00b7 Last 30d: 30-20, +9.0u",
                      text)
        self.assertTrue(text.endswith("thank you Lawler"))

    def test_30d_omitted_unless_good(self):
        results = {"last_night": {"wins": 1, "losses": 2, "units": -1.1,
                                  "plays": 3},
                   "last_7d": {"wins": 4, "losses": 3, "units": 0.9,
                               "plays": 7},
                   "last_30d": {"wins": 9, "losses": 11, "units": -1.0,
                                "plays": 20}}
        text = render_forecast_board(_board(), {}, now=NOW, results=results)
        self.assertNotIn("Last 30d", text)
        self.assertIn("Last night: 1-2, -1.1u", text)

    def test_empty_slate_still_branded(self):
        text = render_forecast_board([], {"games": 0}, now=NOW)
        self.assertTrue(text.startswith("\U0001f4b0 POO DOLLA BETS \u2014 NFL"))
        self.assertIn("No plays today.", text)
        self.assertTrue(text.endswith("thank you Lawler"))


def _cand(tier, market, side, line, american, p_model):
    return Candidate(
        event_id="e1", away="KC", home="BUF", market=market, side=side,
        line=line, american=american,
        decimal=american_to_decimal(american), p_model=p_model,
        p_model_cal=p_model, p_market=0.5, p_push=0.0, ev=0.05, edge=0.05,
        mu_home=1.0, mu_away=-1.0, games_current_min=8,
        start_time_utc="2026-10-21T17:00:00+00:00", tier=tier)


class LegacyBoardFormatTest(unittest.TestCase):
    def test_core_plays_lean_leans_watch_dropped(self):
        candidates = [
            _cand("Core", "SPREAD", "BUF", -4.5, -110, 0.57),
            _cand("Lean", "GAME_TOTAL", "OVER", 47.5, -110, 0.58),
            _cand("Watch", "MONEYLINE", "KC", None, 150, 0.45),
        ]
        text = render_legacy_board(candidates, {}, now=NOW)
        self.assertTrue(text.startswith("\U0001f4b0 POO DOLLA BETS \u2014 NFL"))
        self.assertIn("\u2b50 PLAYS\nBUF -4.5 (-110) \u00b7 57%", text)
        self.assertIn("\U0001f440 LEANS\nOver 47.5 (-110) \u00b7 58%", text)
        self.assertNotIn("KC ML", text)
        self.assertNotIn("Watch", text)
        self.assertTrue(text.endswith("thank you Lawler"))


class RecapCardFormatTest(unittest.TestCase):
    def test_weekly_recap_card(self):
        import grade_forecast as gf
        stats = {"record": {"wins": 4, "losses": 2, "pushes": 0, "pct": 0.667},
                 "priced": {"units": 1.9, "plays": 6, "roi": 0.317},
                 "families": {}, "no_value": {}, "pending": [],
                 "ceiling_hits": 3, "label": "2026 Week 6"}
        results = {"last_night": {"wins": 1, "losses": 1, "units": 0.0,
                                  "plays": 2},
                   "last_7d": {"wins": 4, "losses": 2, "units": 1.9,
                               "plays": 6}}
        text = gf.render_weekly_recap(stats, results=results)
        self.assertEqual(text.split("\n")[0],
                         "\U0001f4b0 POO DOLLA BETS \u2014 NFL RESULTS")
        self.assertIn("\u2b50 Plays: 4-2, +1.9u", text)
        self.assertIn("\U0001f4ca Last night: 1-1, +0.0u \u00b7 "
                      "Last 7d: 4-2, +1.9u", text)
        for internal in ("NO_VALUE", "ceiling", "ROI"):
            self.assertNotIn(internal, text)
        self.assertTrue(text.endswith("thank you Lawler"))


if __name__ == "__main__":
    unittest.main()
