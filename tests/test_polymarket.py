"""Polymarket NFL game-market parsing: nicknames, main line selection."""
from __future__ import annotations

import unittest

from nfl_props.sources.polymarket import _parse_event, _split_matchup
from nfl_props.teams import normalize_team


def _market(mtype, question, outcomes, prices, **extra):
    market = {
        "sportsMarketType": mtype, "question": question,
        "outcomes": outcomes, "outcomePrices": prices,
    }
    market.update(extra)
    return market


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


class TestSpreadLadderTail(unittest.TestCase):
    """Dead alternate-spread rungs must never be read as the main line.

    2026-10-20 WAS @ SF: the ladder tail held
    ``Spread: Commanders (-21.5)`` with ``bestBid`` null, ``liquidityNum``
    0.05 and prices parked at 0.495/0.505. Because outcomes are
    ["Commanders", "49ers"], the home (SF) price 0.505 gave the smallest
    gap-to-even-money and won, producing a positive +21.5 home line (the
    board showed SF +17.5 from a neighbouring dead rung).
    """

    def _dead_rung(self):
        return _market(
            "spreads", "Spread: Commanders (-21.5)",
            '["Commanders", "49ers"]', '["0.495", "0.505"]',
            line=-21.5, slug="nfl-was-sf-2026-10-20-spread-away-21pt5",
            bestBid=None, bestAsk=0.99, volumeNum=10, liquidityNum=0.05)

    def _live_rung(self):
        return _market(
            "spreads", "Spread: 49ers (-7.5)",
            '["49ers", "Commanders"]', '["0.465", "0.535"]',
            line=-7.5, slug="nfl-was-sf-2026-10-20-spread-home-7pt5",
            bestBid=0.46, bestAsk=0.47, volumeNum=388413.2,
            liquidityNum=3027110.42)

    def _event(self, *markets):
        return {"title": "Commanders vs. 49ers", "markets": list(markets)}

    def test_dead_ladder_tail_cannot_win(self):
        parsed = _parse_event(self._event(self._dead_rung(), self._live_rung()))
        self.assertEqual(parsed["spread"]["line"], -7.5)
        self.assertAlmostEqual(parsed["spread"]["home_decimal"], 2.1505,
                               places=3)
        self.assertAlmostEqual(parsed["spread"]["away_decimal"], 1.8692,
                               places=3)

    def test_rung_without_live_bid_is_skipped(self):
        # Liquidity is ample but there is no bid, so the book is dead.
        dead = _market(
            "spreads", "Spread: Commanders (-21.5)",
            '["Commanders", "49ers"]', '["0.495", "0.505"]',
            line=-21.5, bestBid=None, bestAsk=0.99,
            volumeNum=500000, liquidityNum=500000)
        parsed = _parse_event(self._event(dead, self._live_rung()))
        self.assertEqual(parsed["spread"]["line"], -7.5)

    def test_rung_below_liquidity_floor_is_skipped(self):
        # A bid exists but quoted liquidity is a dead-tail 71 dollars.
        dead = _market(
            "spreads", "Spread: Commanders (-17.5)",
            '["Commanders", "49ers"]', '["0.495", "0.505"]',
            line=-17.5, bestBid=0.02, bestAsk=0.05,
            volumeNum=0, liquidityNum=71.45)
        parsed = _parse_event(self._event(dead, self._live_rung()))
        self.assertEqual(parsed["spread"]["line"], -7.5)

    def test_dead_rung_alone_yields_no_spread(self):
        parsed = _parse_event(self._event(self._dead_rung()))
        # Only a moneyline-less, total-less, spread-less event.
        self.assertIsNone(parsed)

    def test_line_field_beats_question_regex(self):
        # Canonical `line` for outcomes[0] wins over the scraped question.
        market = _market(
            "spreads", "Spread: 49ers (-7.5)",
            '["49ers", "Commanders"]', '["0.465", "0.535"]',
            line=-6.5, bestBid=0.55, bestAsk=0.56,
            volumeNum=1000, liquidityNum=1500000)
        parsed = _parse_event(self._event(market))
        self.assertEqual(parsed["spread"]["line"], -6.5)

    def test_near_tie_breaks_toward_smallest_line(self):
        # Both rungs are effectively at even money; take the shorter line
        # instead of letting gap noise decide.
        short = _market(
            "spreads", "Spread: 49ers (-7.5)",
            '["49ers", "Commanders"]', '["0.465", "0.535"]',
            line=-7.5, bestBid=0.46, bestAsk=0.47,
            volumeNum=1000, liquidityNum=3000000)
        deep = _market(
            "spreads", "Spread: 49ers (-17.5)",
            '["49ers", "Commanders"]', '["0.47", "0.53"]',
            line=-17.5, bestBid=0.46, bestAsk=0.48,
            volumeNum=1000, liquidityNum=38289.21)
        parsed = _parse_event(self._event(short, deep))
        self.assertEqual(parsed["spread"]["line"], -7.5)


if __name__ == "__main__":
    unittest.main()
