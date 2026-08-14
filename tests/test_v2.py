"""v2 shadow model tests: base==v1 parity, point-in-time isolation, QB lag,
and design/feature naming.

These protect the two invariants that matter most for a candidate model:
(1) the v2 "base" ratings are numerically identical to v1, and (2) replay is
leakage-safe — changing a game's outcome or realized passer never changes any
feature row that was snapped before that game.
"""
import unittest

import numpy as np
import pandas as pd

from nfl_props.ratings.v2 import (BASE_FEATURES, design, replay_v2,
                                  project_points_v2)


def _games():
    return pd.DataFrame([
        {"game_id": "g1", "season": 2020, "week": 1, "game_type": "REG",
         "gameday": "2020-09-13", "home_team": "B", "away_team": "A",
         "home_score": 21.0, "away_score": 17.0, "roof": "outdoors",
         "location": "Home", "home_rest": 7.0, "away_rest": 7.0},
        {"game_id": "g2", "season": 2020, "week": 1, "game_type": "REG",
         "gameday": "2020-09-13", "home_team": "D", "away_team": "C",
         "home_score": 24.0, "away_score": 24.0, "roof": "dome",
         "location": "Home", "home_rest": 7.0, "away_rest": 7.0},
        {"game_id": "g3", "season": 2020, "week": 2, "game_type": "REG",
         "gameday": "2020-09-20", "home_team": "B", "away_team": "A",
         "home_score": 28.0, "away_score": 10.0, "roof": "outdoors",
         "location": "Home", "home_rest": 7.0, "away_rest": 7.0},
    ])


def _team_games():
    return pd.DataFrame([
        {"game_id": "g1", "season": 2020, "week": 1, "posteam": "A",
         "defteam": "B", "off_epa_pp": 0.05, "off_plays": 60.0},
        {"game_id": "g1", "season": 2020, "week": 1, "posteam": "B",
         "defteam": "A", "off_epa_pp": 0.10, "off_plays": 62.0},
        {"game_id": "g2", "season": 2020, "week": 1, "posteam": "C",
         "defteam": "D", "off_epa_pp": -0.02, "off_plays": 59.0},
        {"game_id": "g2", "season": 2020, "week": 1, "posteam": "D",
         "defteam": "C", "off_epa_pp": 0.02, "off_plays": 61.0},
        {"game_id": "g3", "season": 2020, "week": 2, "posteam": "A",
         "defteam": "B", "off_epa_pp": -0.05, "off_plays": 55.0},
        {"game_id": "g3", "season": 2020, "week": 2, "posteam": "B",
         "defteam": "A", "off_epa_pp": 0.15, "off_plays": 66.0},
    ])


def _situational():
    return pd.DataFrame([
        {"game_id": "g1", "season": 2020, "week": 1, "posteam": "A",
         "defteam": "B", "pass_epa_pp": 0.05, "rush_epa_pp": -0.10},
        {"game_id": "g1", "season": 2020, "week": 1, "posteam": "B",
         "defteam": "A", "pass_epa_pp": 0.10, "rush_epa_pp": -0.05},
        {"game_id": "g2", "season": 2020, "week": 1, "posteam": "C",
         "defteam": "D", "pass_epa_pp": -0.02, "rush_epa_pp": -0.12},
        {"game_id": "g2", "season": 2020, "week": 1, "posteam": "D",
         "defteam": "C", "pass_epa_pp": 0.02, "rush_epa_pp": -0.08},
        {"game_id": "g3", "season": 2020, "week": 2, "posteam": "A",
         "defteam": "B", "pass_epa_pp": -0.05, "rush_epa_pp": -0.20},
        {"game_id": "g3", "season": 2020, "week": 2, "posteam": "B",
         "defteam": "A", "pass_epa_pp": 0.15, "rush_epa_pp": -0.03},
    ])


def _qb_games():
    return pd.DataFrame([
        {"game_id": "g1", "season": 2020, "week": 1, "posteam": "A",
         "defteam": "B", "qb_id": "QB1", "qb_name": "q1", "dropbacks": 35,
         "epa_per_db": 0.05, "cpoe": 0.0, "sack_rate": 0.06, "is_primary": True},
        {"game_id": "g1", "season": 2020, "week": 1, "posteam": "B",
         "defteam": "A", "qb_id": "QB2", "qb_name": "q2", "dropbacks": 38,
         "epa_per_db": 0.10, "cpoe": 1.0, "sack_rate": 0.05, "is_primary": True},
        {"game_id": "g2", "season": 2020, "week": 1, "posteam": "C",
         "defteam": "D", "qb_id": "QB3", "qb_name": "q3", "dropbacks": 36,
         "epa_per_db": -0.02, "cpoe": -2.0, "sack_rate": 0.07, "is_primary": True},
        {"game_id": "g2", "season": 2020, "week": 1, "posteam": "D",
         "defteam": "C", "qb_id": "QB4", "qb_name": "q4", "dropbacks": 37,
         "epa_per_db": 0.02, "cpoe": 0.5, "sack_rate": 0.06, "is_primary": True},
        {"game_id": "g3", "season": 2020, "week": 2, "posteam": "A",
         "defteam": "B", "qb_id": "QB1", "qb_name": "q1", "dropbacks": 30,
         "epa_per_db": -0.05, "cpoe": -1.0, "sack_rate": 0.08, "is_primary": True},
        {"game_id": "g3", "season": 2020, "week": 2, "posteam": "B",
         "defteam": "A", "qb_id": "QB5", "qb_name": "q5", "dropbacks": 33,
         "epa_per_db": 0.15, "cpoe": 2.0, "sack_rate": 0.04, "is_primary": True},
    ])


class ReplayV2Tests(unittest.TestCase):
    def _replay(self):
        return replay_v2(_games(), _team_games(), _situational(), _qb_games())

    def test_rows_are_two_per_game(self):
        f, _ = self._replay()
        self.assertEqual(len(f), 6)
        self.assertEqual(set(f["game_id"]), {"g1", "g2", "g3"})

    def test_outcome_mutation_leaves_all_features_unchanged(self):
        f_before, _ = self._replay()
        games = _games()
        games.loc[games["game_id"] == "g3", "home_score"] = 99.0
        tg = _team_games()
        tg.loc[(tg["game_id"] == "g3") & (tg["posteam"] == "B"), "off_epa_pp"] = 9.0
        f_after, _ = replay_v2(games, tg, _situational(), _qb_games())

        # the outcome label ("points") may change; pregame features must not
        feature_cols = [c for c in f_before.columns if c != "points"]
        pd.testing.assert_frame_equal(
            f_before[feature_cols].sort_values(["game_id", "team"]).reset_index(drop=True),
            f_after[feature_cols].sort_values(["game_id", "team"]).reset_index(drop=True),
            check_dtype=False)

    def test_qb_primary_change_only_affects_later_games(self):
        f_before, _ = self._replay()
        qb = _qb_games()
        # swap the realized week-1 primary QB for team B to a clearly different
        # passer (QB9, much better EPA/dropback)
        qb.loc[(qb["game_id"] == "g1") & (qb["posteam"] == "B"),
               ["qb_id", "epa_per_db", "is_primary"]] = ["QB9", 0.30, True]
        f_after, _ = replay_v2(_games(), _team_games(), _situational(), qb)

        def row(f, game_id, team):
            return f[(f["game_id"] == game_id) & (f["team"] == team)].iloc[0]

        # week 1 rows unchanged
        for game_id, team in (("g1", "A"), ("g1", "B"), ("g2", "C"), ("g2", "D")):
            pd.testing.assert_series_equal(
                row(f_before, game_id, team), row(f_after, game_id, team),
                check_dtype=False)
        # week 2 team B's QB feature changed (new starter delta), team A not
        self.assertNotEqual(row(f_before, "g3", "B")["qb_epa"],
                            row(f_after, "g3", "B")["qb_epa"])
        pd.testing.assert_series_equal(
            row(f_before, "g3", "A"), row(f_after, "g3", "A"), check_dtype=False)

    def test_first_game_qb_is_uncertain(self):
        f, _ = self._replay()
        g1_rows = f[f["game_id"] == "g1"]
        self.assertTrue((g1_rows["qb_uncertain"] == 1.0).all())
        self.assertTrue((g1_rows["qb_epa"] == 0.0).all())

    def test_design_shape_and_naming(self):
        f, _ = self._replay()
        X = design(f, BASE_FEATURES)
        self.assertEqual(X.shape, (len(f), len(BASE_FEATURES) + 1))
        self.assertEqual(X[:, 0].tolist(), [1.0] * len(f))

    def test_project_points_matches_manual(self):
        coefs = {"intercept": 1.0, "a": 2.0, "b": -3.0}
        self.assertEqual(project_points_v2(coefs, {"a": 4.0, "b": 2.0}),
                         1.0 + 2.0 * 4.0 - 3.0 * 2.0)


class BaseParityTests(unittest.TestCase):
    """v2's base ratings must be numerically identical to v1's replay."""

    @unittest.skipUnless(
        __import__("pathlib").Path(
            "data/processed/team_situational_games.parquet").exists(),
        "canonical v2 store not built")
    def test_base_columns_equal_v1_replay(self):
        from nfl_props.ratings.epa import replay
        from nfl_props.sources.nflverse import load_processed_v2

        games, team_games, situational, qb_games = load_processed_v2()
        f1, _ = replay(games, team_games)
        f2, _ = replay_v2(games, team_games, situational, qb_games)

        cols = ["game_id", "team", "home", "points", "off_rating",
                "opp_def_rating", "pace_avg_c"]
        b1 = f1[cols].sort_values(["game_id", "team"]).reset_index(drop=True)
        b2 = f2[cols].sort_values(["game_id", "team"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(b1, b2, check_dtype=False)


if __name__ == "__main__":
    unittest.main()
