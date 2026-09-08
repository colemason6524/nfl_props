"""Live-state export must regress unplayed teams exactly like the replay.

`replay` applies SEASON_CARRYOVER the first time a team appears in a new
season; a team that has not yet played in the target season never hits that
branch, so a raw export carried full prior-season strength into live boards
while the backtest priced the boundary game off the regressed value.
"""
import unittest

import pandas as pd

from nfl_props.ratings.epa import (SEASON_CARRYOVER, carryover_view,
                                   export_teams, replay)


def _game(gid, season, day, home, away, hs, as_):
    return {"game_id": gid, "season": season, "week": 1, "game_type": "REG",
            "gameday": day, "home_team": home, "away_team": away,
            "home_score": hs, "away_score": as_}


def _tg(gid, team, epa, plays):
    return {"game_id": gid, "posteam": team, "off_epa_pp": epa,
            "off_plays": plays}


G1 = _game("g1", 2025, "2025-09-07", "DET", "GB", 34, 10)
TG1 = [_tg("g1", "DET", +0.40, 68.0), _tg("g1", "GB", -0.20, 55.0)]
G2 = _game("g2", 2026, "2026-09-13", "DET", "GB", 24, 20)
TG2 = [_tg("g2", "DET", 0.0, 62.0), _tg("g2", "GB", 0.0, 60.0)]


class LiveStateExportTests(unittest.TestCase):
    def _state_2025(self):
        return replay(pd.DataFrame([G1]), pd.DataFrame(TG1))

    def test_unplayed_team_exported_regressed(self):
        _, st = self._state_2025()
        raw = st["teams"]["DET"]
        self.assertGreater(raw["off"], 0.02)
        out = export_teams(st["teams"], target_season=2026)
        det = out["DET"]
        self.assertAlmostEqual(det["off"], raw["off"] * SEASON_CARRYOVER, 5)
        self.assertAlmostEqual(det["def"], raw["def"] * SEASON_CARRYOVER, 5)
        self.assertTrue(det["carryover_applied"])
        self.assertEqual(det["last_season"], 2025)
        self.assertEqual(det["games_current_season"], 0)
        self.assertAlmostEqual(det["pace"], round(raw["pace"], 2))
        # view only: replay state is not mutated
        self.assertEqual(st["teams"]["DET"]["off"], raw["off"])

    def test_same_season_team_not_regressed(self):
        _, st = self._state_2025()
        raw = st["teams"]["DET"]
        out = export_teams(st["teams"], target_season=2025)
        self.assertAlmostEqual(out["DET"]["off"], round(raw["off"], 5))
        self.assertFalse(out["DET"]["carryover_applied"])
        self.assertEqual(out["DET"]["games_current_season"], 1)

    def test_export_matches_replay_first_game_snapshot(self):
        # live == backtest: the exported 2026 preseason rating equals the
        # point-in-time feature the replay snaps for the first 2026 game.
        _, st = self._state_2025()
        exported = export_teams(st["teams"], target_season=2026)["DET"]
        feats, st2 = replay(pd.DataFrame([G1, G2]),
                            pd.DataFrame(TG1 + TG2))
        row = feats[(feats["game_id"] == "g2") & (feats["team"] == "DET")]
        self.assertAlmostEqual(exported["off"], row.iloc[0]["off_rating"], 4)
        # after the team has played in 2026 the replay already regressed
        # it; export must not regress a second time
        again = export_teams(st2["teams"], target_season=2026)["DET"]
        self.assertFalse(again["carryover_applied"])
        self.assertAlmostEqual(again["off"],
                               round(st2["teams"]["DET"]["off"], 5))

    def test_carryover_view_custom_fields(self):
        t = {"off": 1.0, "def": -1.0, "off_pass": 0.5, "pace": 60.0,
             "games_total": 5, "season": 2025, "games_season": 5}
        view, regressed = carryover_view(t, 2026, fields=("off", "off_pass"))
        self.assertTrue(regressed)
        self.assertAlmostEqual(view["off"], SEASON_CARRYOVER)
        self.assertAlmostEqual(view["off_pass"], 0.5 * SEASON_CARRYOVER)
        self.assertEqual(view["def"], -1.0)   # not in fields
        self.assertEqual(t["off"], 1.0)       # input untouched


if __name__ == "__main__":
    unittest.main()
