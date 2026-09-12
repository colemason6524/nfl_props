"""Polymarket NFL game-market parsing: nicknames, main line selection."""
from __future__ import annotations

import unittest

from nfl_props.sources.polymarket import _parse_event, _split_matchup
from nfl_props.teams import normalize_team


def _market(mtype, question, outcomes, prices):
    return {
        "sportsMarketType": mtype, "question": question,
        "outcomes": outcomes, "outcomePrices": prices,
    }


def _event():
    return {
        "title": "Cowboys vs. Giants",
        "markets": [
            _market("moneyline", "Cowboys vs. Giants",
                    '["Cowboys", "Giants"]', '["0.595", "0.405"]'),
            _market("spreads", "Spread: Cowboys (-1.5)",
                    '["Cowboys", "Giants"]', '["0.575", "0.425"]'),
            _market("spreads", "Spread: Cowboys (-2.5)",
                    '["Cowboys", "Giants"]', '["0.545", "0.455"]'),
            _market("spreads", "Spread: Cowboys (-3.5)",
                    '["Cowboys", "Giants"]', '["0.445", "0.555"]'),
            _market("totals", "Cowboys vs. Giants: O/U 45.5",
                    '["Over", "Under"]', '["0.575", "0.425"]'),
            _market("totals", "Cowboys vs. Giants: O/U 43.5",
                    '["Over", "Under"]', '["0.515", "0.485"]'),
            _market("first_half_totals", "Cowboys vs. Giants: 1H O/U 24.5",
                    '["Over", "Under"]', '["0.5", "0.5"]'),
            _market("q1_spreads", "1Q Spread: Cowboys (-3.5)",
                    '["Cowboys", "Giants"]', '["0.5", "0.5"]'),
        ],
    }


class TestTeamNicknames(unittest.TestCase):
    def test_nicknames_normalize(self):
        self.assertEqual(normalize_team("Cowboys"), "DAL")
        self.assertEqual(normalize_team("49ers"), "SF")
        self.assertEqual(normalize_team("Buccaneers"), "TB")

    def test_matchup_split_uses_nicknames(self):
        self.assertEqual(_split_matchup("Cowboys vs. Giants"), ("DAL", "NYG"))


class TestParseEvent(unittest.TestCase):
    def test_parses_game_markets(self):
        parsed = _parse_event(_event())
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed["away"], parsed["home"]), ("DAL", "NYG"))
        self.assertEqual(len(parsed["moneyline"]), 2)
        # Main spread is the line closest to even money (-2.5), named on the
        # away side, so the home line is +2.5.
        self.assertEqual(parsed["spread"]["line"], 2.5)
        # Main total is the one closest to even money.
        self.assertEqual(parsed["total"]["line"], 43.5)
        self.assertAlmostEqual(parsed["total"]["over_p"], 0.515, places=3)

    def test_home_handicap_is_negative(self):
        event = {
            "title": "Cowboys vs. Giants",
            "markets": [_market("spreads", "Spread: Giants (-2.5)",
                                '["Giants", "Cowboys"]',
                                '["0.52", "0.48"]')],
        }
        parsed = _parse_event(event)
        self.assertEqual(parsed["spread"]["line"], -2.5)

    def test_futures_event_is_ignored(self):
        event = {
            "title": "Cardinals vs. Rams Season Series Winner",
            "markets": [_market("moneyline", "Cardinals vs. Rams",
                                '["Cardinals", "Rams"]', '["0.5", "0.5"]')],
        }
        self.assertIsNone(_parse_event(event))


if __name__ == "__main__":
    unittest.main()
