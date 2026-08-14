"""Parsing + filtering tests for Bovada payloads.

Covers: main market extraction (spread/ML/game total), team-total description
patterns, and the kickoff-safety filter that drops stale / missing-kickoff
events from `fetch_live_games`.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from nfl_props.sources import bovada
from nfl_props.sources.bovada import LiveGame, TwoWayPrice


def _game_desc(away="Buffalo Bills", home="Miami Dolphins"):
    return f"{away} @ {home}"


def _market(desc, outcomes):
    return {"status": "O", "period": {"description": "Game"},
            "description": desc, "outcomes": outcomes}


def _outcome(desc, am="-110", dec="1.91", handicap=None):
    price = {"american": am, "decimal": dec}
    if handicap is not None:
        price["handicap"] = handicap
    return {"description": desc, "price": price}


def _event(desc, start_ms, markets=(), link=""):
    return {"description": desc, "id": "ev1", "link": link,
            "startTime": start_ms,
            "displayGroups": [{"markets": list(markets)}]}


def _coupon(events):
    return [{"path": [{"description": "Football"}, {"description": "NFL"}],
             "events": events}]


def _ms(dt):
    return int(dt.timestamp() * 1000)


class ParseMainMarketsTests(unittest.TestCase):
    def test_extracts_moneyline_spread_total(self):
        ml = _market("Moneyline", [
            _outcome("Miami Dolphins", "-120", "1.83"),
            _outcome("Buffalo Bills", "+100", "2.00"),
        ])
        sp = _market("Point Spread", [
            _outcome("Miami Dolphins", "-110", "1.91", "-3.5"),
            _outcome("Buffalo Bills", "-110", "1.91", "3.5"),
        ])
        gt = _market("Total", [
            _outcome("Over", "-110", "1.91", "41.5"),
            _outcome("Under", "-110", "1.91"),
        ])
        payload = _coupon([_event(_game_desc(), _ms(datetime.now(timezone.utc)
                                                    + timedelta(days=1)),
                                  markets=[ml, sp, gt])])
        games, diags = bovada.parse_games(payload)
        self.assertEqual(len(games), 1)
        self.assertEqual(diags["groups"], 1)
        g = games[0]
        self.assertEqual((g.away, g.home), ("BUF", "MIA"))
        self.assertIsNotNone(g.moneyline)
        self.assertIsNotNone(g.spread)
        self.assertAlmostEqual(g.spread.line, -3.5)
        self.assertIsNotNone(g.game_total)
        self.assertAlmostEqual(g.game_total.line, 41.5)


class TeamTotalPatternTests(unittest.TestCase):
    def test_patterns_match_known_variants(self):
        cases = [
            ("Total Points Scored by Buffalo Bills", "BUF"),
            ("Total Points By Miami Dolphins", "MIA"),
            ("Team Total - Buffalo Bills", "BUF"),
            ("Buffalo Bills Team Total", "BUF"),
        ]
        for desc, expected in cases:
            self.assertEqual(bovada._team_total_team(desc), expected, desc)

    def test_extracts_team_total_market(self):
        game = LiveGame(event_id="e", away="BUF", home="MIA",
                        away_name="Buffalo Bills", home_name="Miami Dolphins",
                        start_time_utc=None, link="", league_path="NFL")
        tt = _market("Total Points Scored by Buffalo Bills", [
            _outcome("Over", "-110", "1.91", "20.5"),
            _outcome("Under", "-110", "1.91"),
        ])
        payload = _coupon([_event(_game_desc(), 0, markets=[tt])])
        diag = bovada._extract_team_totals(payload, game)
        self.assertEqual(diag["team_matched"], 1)
        self.assertIn("BUF", game.team_totals)
        self.assertAlmostEqual(game.team_totals["BUF"].line, 20.5)


class KickoffFilterTests(unittest.TestCase):
    def _fetch(self, events, now, fetch_team_totals=False):
        coupon = bovada.FetchResult(_coupon(events), "fresh", 1, 200, 10,
                                    "2026-01-01T00:00:00+00:00", 0.0, None)
        with mock.patch.object(bovada, "fetch_nfl_payload", return_value=coupon):
            return bovada.fetch_live_games(refresh=True,
                                           fetch_team_totals=fetch_team_totals,
                                           now=now)

    def test_stale_and_missing_kickoff_filtered(self):
        now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        past = _event(_game_desc(), _ms(now - timedelta(hours=1)))
        future = _event(_game_desc("Dallas Cowboys", "New York Giants"),
                        _ms(now + timedelta(days=1)))
        no_kickoff = _event(_game_desc("Green Bay Packers", "Chicago Bears"),
                            None)
        games, diags = self._fetch([past, future, no_kickoff], now)
        self.assertEqual(len(games), 1)
        self.assertEqual((games[0].away, games[0].home), ("DAL", "NYG"))
        self.assertEqual(diags["stale_games_filtered"], 1)
        self.assertEqual(diags["stale_games"], ["BUF @ MIA"])
        self.assertEqual(diags["missing_kickoffs_filtered"], 1)
        self.assertEqual(diags["missing_kickoffs"], ["GB @ CHI"])
        self.assertEqual(diags["games_parsed"], 3)
        self.assertEqual(diags["games_kept"], 1)


if __name__ == "__main__":
    unittest.main()
