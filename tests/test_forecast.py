"""Forecast-first lane tests: price independence, labels, ties, schedule."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from nfl_props.forecast import build_game_forecast
from nfl_props.forecast_board import build_board
from nfl_props.forecasting import FEATURE_SETS
from nfl_props.references import (NO_VALUE, PLAYABLE, UNPRICED, build_reference)
from nfl_props.schedule import current_day_games
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


def _bovada(ml=(1.9, 1.9), spread_line=-3.0, spread=(1.9, 1.9),
            total_line=45.5, total=(1.9, 1.9)):
    return LiveGame(
        event_id="e1", away="AWY", home="HOM", away_name="Away",
        home_name="Home", start_time_utc="2026-09-13T17:00:00+00:00",
        link="", league_path="NFL",
        moneyline=TwoWayPrice(None, 0, 0, ml[0], ml[1]),
        spread=TwoWayPrice(spread_line, 0, 0, spread[0], spread[1]),
        game_total=TwoWayPrice(total_line, 0, 0, total[0], total[1]))


class TestPriceIndependence(unittest.TestCase):
    def setUp(self):
        self.state = _fake_state()

    def test_odds_cannot_change_pick_or_projections(self):
        base = build_game_forecast(_game(), self.state)
        skewed = build_game_forecast(
            _game(), self.state,
            bovada_game=_bovada(ml=(1.01, 25.0), spread=(1.01, 25.0),
                                total=(1.01, 25.0)))
        self.assertEqual(base.winner_pick, skewed.winner_pick)
        self.assertEqual(base.projected_margin, skewed.projected_margin)
        self.assertEqual(base.projected_total, skewed.projected_total)

    def test_missing_odds_is_unpriced(self):
        fc = build_game_forecast(_game(), self.state)
        ref = {r.family: r for r in fc.references}
        self.assertEqual(ref["moneyline"].status, UNPRICED)
        self.assertEqual(ref["spread"].status, UNPRICED)
        self.assertEqual(ref["total"].status, UNPRICED)
        self.assertEqual(ref["moneyline"].side, fc.winner_pick)
        self.assertIsNone(ref["spread"].side)

    def test_line_change_only_affects_its_own_family(self):
        a = _bovada(spread_line=-3.0, total_line=45.5)
        b = _bovada(spread_line=-7.0, total_line=45.5)
        fa = build_game_forecast(_game(), self.state, bovada_game=a)
        fb = build_game_forecast(_game(), self.state, bovada_game=b)
        ra = {r.family: r for r in fa.references}
        rb = {r.family: r for r in fb.references}
        self.assertNotEqual(ra["spread"].line, rb["spread"].line)
        self.assertEqual(ra["moneyline"].side, rb["moneyline"].side)
        self.assertEqual(ra["total"].line, rb["total"].line)
        self.assertEqual(fa.projected_margin, fb.projected_margin)

    def test_bovada_primary_when_present(self):
        fc = build_game_forecast(_game(), self.state, bovada_game=_bovada())
        ref = {r.family: r for r in fc.references}
        self.assertEqual(ref["moneyline"].source, "bovada")
        self.assertEqual(ref["moneyline"].status, PLAYABLE)


class TestReferences(unittest.TestCase):
    def test_consensus_averages_sources(self):
        ref = build_reference(
            "moneyline", "home", None, 0.60, 0.0,
            [("bovada", 2.0, 2.0), ("polymarket", 1.8, 2.2)])
        self.assertEqual(ref.n_sources, 2)
        self.assertAlmostEqual(ref.p_market, (0.5 + 0.55) / 2, places=3)
        self.assertEqual(ref.source, "bovada")

    def test_status_labels(self):
        playable = build_reference("moneyline", "home", None, 0.60, 0.0,
                                   [("bovada", 2.0, 2.0)])
        no_value = build_reference("moneyline", "home", None, 0.40, 0.0,
                                   [("bovada", 2.0, 2.0)])
        self.assertEqual(playable.status, PLAYABLE)
        self.assertEqual(no_value.status, NO_VALUE)


class TestGrading(unittest.TestCase):
    def test_tie_is_push_never_loss(self):
        from grade_forecast import grade_reference
        ref = {"family": "moneyline", "side": "home"}
        self.assertEqual(grade_reference(ref, 20.0, 20.0), "push")

    def test_spread_and_total_push(self):
        from grade_forecast import grade_reference
        self.assertEqual(
            grade_reference({"family": "spread", "side": "away", "line": 3.0},
                            20.0, 17.0), "push")
        self.assertEqual(
            grade_reference({"family": "total", "side": "over", "line": 47.0},
                            27.0, 20.0), "push")

    def test_units_weight_wins_by_price(self):
        from grade_forecast import units
        self.assertAlmostEqual(units("win", 1.5), 0.5)
        self.assertAlmostEqual(units("win", 3.0), 2.0)
        self.assertAlmostEqual(units("loss", 3.0), -1.0)
        self.assertAlmostEqual(units("push", 3.0), 0.0)


class TestSelection(unittest.TestCase):
    def test_latest_pregame_and_cohort(self):
        from grade_forecast import select_latest_pregame
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            early = {
                "model_version": "nfl-forecast-v1",
                "generated_at_utc": "2026-09-13T10:00:00+00:00",
                "forecasts": [{
                    "forecast_id": "g:1", "available": True,
                    "kickoff_utc": "2026-09-13T17:00:00+00:00",
                    "winner_pick": "home", "p_home_win": 0.6,
                }],
            }
            late = {
                "model_version": "nfl-forecast-v1",
                "generated_at_utc": "2026-09-13T15:00:00+00:00",
                "forecasts": [{
                    "forecast_id": "g:1", "available": True,
                    "kickoff_utc": "2026-09-13T17:00:00+00:00",
                    "winner_pick": "home", "p_home_win": 0.7,
                }],
            }
            legacy = {
                "model_version": "nfl-epa-points-v1.1",
                "generated_at_utc": "2026-09-13T15:00:00+00:00",
                "forecasts": [{
                    "forecast_id": "g:1", "available": True,
                    "kickoff_utc": "2026-09-13T17:00:00+00:00",
                    "winner_pick": "away", "p_home_win": 0.1,
                }],
            }
            (d / "nfl_forecast_a.json").write_text(json.dumps(early))
            (d / "nfl_forecast_b.json").write_text(json.dumps(late))
            (d / "nfl_forecast_c.json").write_text(json.dumps(legacy))
            chosen = select_latest_pregame(sorted(d.glob("*.json")))
        self.assertEqual(len(chosen), 1)
        self.assertAlmostEqual(chosen["g:1"]["p_home_win"], 0.7)


class TestSchedule(unittest.TestCase):
    def _frame(self):
        import pandas as pd
        rows = [
            {"game_id": 1, "season": 2026, "week": 2, "game_type": "REG",
             "away_team": "AWY", "home_team": "HOM", "gameday": "2026-09-13",
             "gametime": "13:00", "location": "Home", "roof": "open",
             "div_game": 0, "home_rest": 7, "away_rest": 7,
             "stadium": "S", "temp": None, "wind": None},
            {"game_id": 2, "season": 2026, "week": 2, "game_type": "REG",
             "away_team": "AAA", "home_team": "BBB", "gameday": "2026-09-14",
             "gametime": "20:15", "location": "Home", "roof": "open",
             "div_game": 1, "home_rest": 6, "away_rest": 6,
             "stadium": "S", "temp": None, "wind": None},
        ]
        df = pd.DataFrame(rows)
        stamps = pd.to_datetime(df["gameday"] + " " + df["gametime"])
        stamps = (stamps.dt.tz_localize("America/New_York")
                  .dt.tz_convert("UTC"))
        df["kickoff_utc"] = stamps
        return df

    def test_current_day_only_and_not_started(self):
        df = self._frame()
        now = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)
        games = current_day_games(games=df, now=now)
        self.assertEqual([g["game_id"] for g in games], ["1"])
        now_after = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(current_day_games(games=df, now=now_after), [])

    def test_date_override(self):
        df = self._frame()
        now = datetime(2026, 9, 13, 15, 0, tzinfo=timezone.utc)
        games = current_day_games(games=df, now=now, date_override="2026-09-14")
        self.assertEqual([g["game_id"] for g in games], ["2"])


class TestBoard(unittest.TestCase):
    def test_board_publishes_unpriced_games(self):
        state = _fake_state()
        forecasts, summary = build_board(state, [_game()])
        self.assertEqual(summary["games"], 1)
        self.assertEqual(summary["available"], 1)
        self.assertEqual(summary["priced"], 0)
        self.assertEqual(forecasts[0].winner_pick, "home")

    def test_render_has_three_sections_and_unpriced(self):
        from nfl_props.forecast_output import render_board
        state = _fake_state()
        forecasts, summary = build_board(state, [_game()])
        text = render_board(forecasts, summary)
        for section in ("== Moneylines (1) ==", "== Spreads (1) ==",
                        "== Totals (1) =="):
            self.assertIn(section, text)
        self.assertIn("UNPRICED", text)

    def test_discord_chunking_preserves_every_line(self):
        from nfl_props.notifiers.forecast_discord import chunk_messages
        text = "\n".join(f"- line {i}" for i in range(500))
        chunks = chunk_messages(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)


if __name__ == "__main__":
    unittest.main()
