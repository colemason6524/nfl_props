import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from grade import (grade_shadow_projection, load_shadow_projections,
                   summarize_shadow)


def _payload(generated, mu_home, mu_away, mu_home_v2, mu_away_v2):
    return {
        "generated_at_utc": generated,
        "summary": {
            "projections": [{
                "away": "BUF",
                "home": "MIA",
                "start_time_utc": "2026-09-13T17:00:00+00:00",
                "mu_home": mu_home,
                "mu_away": mu_away,
                "mu_home_v2": mu_home_v2,
                "mu_away_v2": mu_away_v2,
            }],
        },
    }


class ShadowProjectionLoaderTests(unittest.TestCase):
    def test_latest_pre_kickoff_snapshot_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            snapshots = [
                ("nfl_board_1.json", "2026-09-13T12:00:00+00:00", 21, 19, 22, 18),
                ("nfl_board_2.json", "2026-09-13T16:00:00+00:00", 24, 20, 26, 18),
                ("nfl_board_3.json", "2026-09-13T18:00:00+00:00", 99, 1, 99, 1),
            ]
            for name, generated, h1, a1, h2, a2 in snapshots:
                (history / name).write_text(json.dumps(
                    _payload(generated, h1, a1, h2, a2)))

            rows = load_shadow_projections(history, "")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mu_home"], 24)
        self.assertEqual(rows[0]["mu_home_v2"], 26)

    def test_history_without_v2_fields_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp)
            payload = _payload("2026-09-13T12:00:00+00:00", 21, 19, None, None)
            (history / "nfl_board_1.json").write_text(json.dumps(payload))
            self.assertEqual(load_shadow_projections(history, ""), [])


class ShadowProjectionGradeTests(unittest.TestCase):
    def test_grades_points_margin_and_total_errors(self):
        projection = _payload(
            "2026-09-13T16:00:00+00:00", 24, 20, 26, 18
        )["summary"]["projections"][0]
        game = pd.Series({"home_score": 27, "away_score": 17})

        row = grade_shadow_projection(projection, game)

        self.assertEqual(row["points_mae_v1"], 3.0)
        self.assertEqual(row["points_mae_v2"], 1.0)
        self.assertEqual(row["margin_mae_v1"], 6.0)
        self.assertEqual(row["margin_mae_v2"], 2.0)
        self.assertEqual(row["total_mae_v1"], 0.0)
        self.assertEqual(row["total_mae_v2"], 0.0)

        summary = "\n".join(summarize_shadow([row]))
        self.assertIn("v2-v1", summary)
        self.assertIn("-2.000", summary)
        self.assertIn("-4.000", summary)

    def test_pending_game_returns_none(self):
        projection = _payload(
            "2026-09-13T16:00:00+00:00", 24, 20, 26, 18
        )["summary"]["projections"][0]
        game = pd.Series({"home_score": None, "away_score": None})
        self.assertIsNone(grade_shadow_projection(projection, game))


if __name__ == "__main__":
    unittest.main()
