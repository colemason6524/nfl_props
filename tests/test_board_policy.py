"""Single-side policy: only the max-EV side of each (game, market) family
stays Core/Lean-eligible; the other side is flagged OPPOSITE_SIDE + Watch."""
import unittest

from nfl_props.board import apply_single_side
from nfl_props.tiers import Candidate


def _c(away, home, market, side, ev, tier="Lean", flags=()):
    return Candidate(
        event_id="e1", away=away, home=home, market=market, side=side,
        line=45.0 if "TOTAL" in market else None,
        american=-110, decimal=1.91,
        p_model=0.55, p_model_cal=0.53, p_market=0.50, p_push=0.0,
        ev=ev, edge=0.05, mu_home=22.0, mu_away=21.0,
        games_current_min=0, start_time_utc=None,
        flags=list(flags), tier=tier,
    )


class SingleSideTests(unittest.TestCase):
    def test_game_total_loser_side_flagged(self):
        over = _c("A", "B", "GAME_TOTAL", "OVER", 0.05)
        under = _c("A", "B", "GAME_TOTAL", "UNDER", 0.03)
        apply_single_side([over, under])
        self.assertEqual(over.tier, "Lean")
        self.assertEqual(under.tier, "Watch")
        self.assertIn("OPPOSITE_SIDE", under.flags)
        self.assertNotIn("OPPOSITE_SIDE", over.flags)

    def test_moneyline_picks_one_side(self):
        home = _c("A", "B", "MONEYLINE", "B", 0.06)
        away = _c("A", "B", "MONEYLINE", "A", 0.04)
        apply_single_side([home, away])
        self.assertEqual(home.tier, "Lean")
        self.assertEqual(away.tier, "Watch")
        self.assertIn("OPPOSITE_SIDE", away.flags)

    def test_team_total_scoped_per_team(self):
        a_over = _c("A", "B", "TEAM_TOTAL", "A OVER", 0.05)
        b_over = _c("A", "B", "TEAM_TOTAL", "B OVER", 0.04)
        a_under = _c("A", "B", "TEAM_TOTAL", "A UNDER", 0.02)
        apply_single_side([a_over, b_over, a_under])
        self.assertEqual(a_over.tier, "Lean")   # best of A's line
        self.assertEqual(b_over.tier, "Lean")   # best of B's line
        self.assertEqual(a_under.tier, "Watch")
        self.assertIn("OPPOSITE_SIDE", a_under.flags)

    def test_other_games_untouched(self):
        mine = _c("A", "B", "SPREAD", "A", 0.05)
        other = _c("C", "D", "SPREAD", "C", 0.02)
        apply_single_side([mine, other])
        self.assertEqual(other.tier, "Lean")
        self.assertNotIn("OPPOSITE_SIDE", other.flags)

    def test_tie_is_deterministic_and_single(self):
        x = _c("A", "B", "GAME_TOTAL", "OVER", 0.05)
        y = _c("A", "B", "GAME_TOTAL", "UNDER", 0.05)
        apply_single_side([x, y])
        eligible = [c for c in (x, y) if "OPPOSITE_SIDE" not in c.flags]
        self.assertEqual(len(eligible), 1)

    def test_idempotent(self):
        over = _c("A", "B", "GAME_TOTAL", "OVER", 0.05)
        under = _c("A", "B", "GAME_TOTAL", "UNDER", 0.03)
        apply_single_side([over, under])
        apply_single_side([over, under])
        self.assertEqual(under.flags.count("OPPOSITE_SIDE"), 1)
        self.assertEqual(under.tier, "Watch")


if __name__ == "__main__":
    unittest.main()
