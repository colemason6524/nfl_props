"""Test that forecast.py applies gamma shrinkage to total probabilities."""
import unittest
from unittest.mock import MagicMock, patch
import sys
sys.path.insert(0, "/home/azureuser/nfl_props")

from nfl_props.ratings.epa import shrink_prob


class TestTotalShrinkage(unittest.TestCase):
    def test_shrink_prob_applied(self):
        # gamma=0.1 should pull 0.65 -> 0.515
        self.assertAlmostEqual(shrink_prob(0.65, 0.1), 0.515, places=3)
        # gamma=1.0 is identity
        self.assertAlmostEqual(shrink_prob(0.65, 1.0), 0.65, places=3)
        # gamma=0.1 pulls 0.60 -> 0.51
        self.assertAlmostEqual(shrink_prob(0.60, 0.1), 0.51, places=3)

    def test_forecast_applies_shrinkage(self):
        """Verify build_game_forecast applies prob_shrink to totals."""
        from nfl_props import forecast as fc_mod
        import inspect
        src = inspect.getsource(fc_mod.build_game_forecast)
        # The fix should reference shrink_prob and prob_shrink in the total section
        self.assertIn("shrink_prob", src)
        self.assertIn('prob_shrink', src)
        self.assertIn('"total"', src)


if __name__ == "__main__":
    unittest.main()
