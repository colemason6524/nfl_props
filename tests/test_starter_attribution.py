"""Regression: starter-aware injury debits and QB substitution (2026-10-19).

The SF board charged a -2.7 injury debit and flagged SF QB out because a
lone backup QB (Kurtis Rourke, QB3) appeared on the injury report, then
priced the stale ex-starter Mac Jones over incumbent Brock Purdy. Depth
charts (nflverse) resolve starter vs backup; these tests pin the behavior:
a backup being out is immaterial for QB status, for the priced QB, and for
the non-QB debit.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from nfl_props.delta import GameDelta, _is_key_player_news, diff_injuries
from nfl_props.forecasting import (
    game_features,
    qb_starter_is_out,
    resolve_priced_qb_id,
)
from nfl_props.injury_debit import team_injury_debit
from nfl_props.sources.depth import is_starter, parse_depth_csv

PURDY = "00-0037834"
MAC_JONES = "00-0036972"
RAMS_QB = "00-0039999"


def _qbs(mac_season=2026):
    """SF room: Purdy (incumbent) + Mac Jones (backup). Rourke is not in
    the priced set -- he only ever appears in the injury feed."""
    return {
        PURDY: {"name": "B.Purdy", "last_team": "SF", "last_season": 2026,
                "dropbacks": 1929, "epa_c": 0.15, "cpoe": 2.0,
                "sack_rate": 0.05, "games": 14},
        MAC_JONES: {"name": "M.Jones", "last_team": "SF",
                    "last_season": mac_season, "dropbacks": 2095,
                    "epa_c": 0.02, "cpoe": 0.0, "sack_rate": 0.05,
                    "games": 15},
        RAMS_QB: {"name": "M.Stafford", "last_team": "LA", "last_season": 2026,
                  "dropbacks": 600, "epa_c": 0.10, "cpoe": 0.0,
                  "sack_rate": 0.05, "games": 10},
    }


def _state(personnel, qbs=None):
    return {
        "league": {"pace": 63.0},
        "teams": {
            "SF": {"off": 0.10, "def": 0.02, "off_pass": 0.12,
                   "def_pass": 0.03, "off_rush": 0.02, "def_rush": 0.01,
                   "pace": 63.0, "last_qb_id": PURDY},
            "LA": {"off": 0.05, "def": 0.00, "off_pass": 0.05,
                   "def_pass": 0.00, "off_rush": 0.00, "def_rush": 0.00,
                   "pace": 63.0, "last_qb_id": RAMS_QB},
        },
        "qbs": qbs if qbs is not None else _qbs(),
        "personnel": personnel,
        "personnel_available": True,
    }


def _game():
    return {"home_team": "SF", "away_team": "LA"}


# Nine OUT entries mirroring the live SF payload, with the depth-chart
# starter flags the annotation now supplies.
KEY_OUT = [
    {"position": "WR", "player_id": None, "name": "Ricky Pearsall",
     "starter": False},
    {"position": "LB", "player_id": None, "name": "Fred Warner",
     "starter": True},
    {"position": "S", "player_id": None, "name": "Talanoa Hufanga",
     "starter": False},
    {"position": "DT", "player_id": None, "name": "Javon Hargrave",
     "starter": True},
    {"position": "T", "player_id": None, "name": "Trent Williams",
     "starter": True},
    {"position": "WR", "player_id": None, "name": "Jacob Cowing",
     "starter": False},
]

BACKUP_QB_OUT = {"SF": {
    "qb_status": "out",
    "key_out": KEY_OUT,
    "backup_qb_id": None,
    "starter_qb_id": None,
    "qb_details": [{"name": "Kurtis Rourke", "status": "out",
                    "athlete_id": "4430431"}],
}}

STARTER_QB_OUT = {"SF": {
    "qb_status": "out",
    "key_out": KEY_OUT,
    "backup_qb_id": None,
    "starter_qb_id": None,
    "qb_details": [{"name": "Brock Purdy", "status": "out"},
                   {"name": "Kurtis Rourke", "status": "confirmed"}],
}}


def _sf_ctx(personnel):
    return {"qb_status": personnel["SF"]["qb_status"],
            "qb_details": personnel["SF"]["qb_details"],
            "key_out": personnel["SF"]["key_out"],
            "backup_qb_id": None, "starter_qb_id": None}


class StarterAttributionTest(unittest.TestCase):
    def test_backup_qb_out_does_not_flag_starter(self):
        personnel = BACKUP_QB_OUT
        state = _state(personnel)
        ctx = _sf_ctx(personnel)
        tstate = state["teams"]["SF"]
        self.assertFalse(qb_starter_is_out(tstate, state["qbs"], ctx))
        self.assertEqual(
            resolve_priced_qb_id("SF", tstate, state["qbs"], ctx), PURDY)
        feats = game_features(state, _game())
        self.assertEqual(feats["priced_qb_h"], PURDY)
        # forecast.py derives qb_substituted_* exactly this way.
        starter, priced = tstate.get("last_qb_id"), feats["priced_qb_h"]
        substituted = (qb_starter_is_out(tstate, state["qbs"], ctx)
                       and priced is not None and priced != starter)
        self.assertFalse(substituted)
        debit, attribution = team_injury_debit(
            ctx, priced_qb_substituted=substituted)
        # Only starters debit: LB (-0.2) + DT (-0.2) hit the DEF cap-free
        # group and T (-0.4) hits OL; no QB residual, no backup rows.
        self.assertAlmostEqual(debit, -0.4, places=6)
        self.assertEqual([a["position"] for a in attribution],
                         ["LB", "DT", "T"])
        self.assertNotIn("QB", [a["position"] for a in attribution])

    def test_starting_qb_out_flips(self):
        personnel = STARTER_QB_OUT
        state = _state(personnel)
        ctx = _sf_ctx(personnel)
        tstate = state["teams"]["SF"]
        self.assertTrue(qb_starter_is_out(tstate, state["qbs"], ctx))
        self.assertEqual(
            resolve_priced_qb_id("SF", tstate, state["qbs"], ctx), MAC_JONES)
        feats = game_features(state, _game())
        self.assertEqual(feats["priced_qb_h"], MAC_JONES)
        starter = tstate.get("last_qb_id")
        substituted = (qb_starter_is_out(tstate, state["qbs"], ctx)
                       and feats["priced_qb_h"] is not None
                       and feats["priced_qb_h"] != starter)
        self.assertTrue(substituted)
        debit, attribution = team_injury_debit(
            ctx, priced_qb_substituted=substituted)
        qb_rows = [a for a in attribution if a["position"] == "QB"]
        self.assertEqual(len(qb_rows), 1)
        self.assertAlmostEqual(qb_rows[0]["raw"], -2.0, places=6)
        self.assertAlmostEqual(qb_rows[0]["applied"], -1.0, places=6)
        self.assertAlmostEqual(debit, -1.4, places=6)

    def test_stale_season_qb_not_priced_as_last_resort(self):
        # Last-resort fallback skips QBs whose last season predates the
        # current one (Mac Jones 2025 must never out-rank the incumbent).
        personnel = STARTER_QB_OUT
        state = _state(personnel, qbs=_qbs(mac_season=2025))
        ctx = _sf_ctx(personnel)
        self.assertIsNone(
            resolve_priced_qb_id("SF", state["teams"]["SF"], state["qbs"],
                                 ctx))

    def test_depth_chart_parser_marks_starters(self):
        csv_text = (
            "dt,team,player_name,espn_id,gsis_id,pos_grp_id,pos_grp,pos_id,"
            "pos_name,pos_abb,pos_slot,pos_rank\n"
            "2026-10-19T14:35:37Z,SF,Brock Purdy,4361741,00-0037834,21,3WR "
            "1TE,8,Quarterback,QB,9,1\n"
            "2026-10-19T14:35:37Z,SF,Mac Jones,4241464,00-0036972,21,3WR 1TE,"
            "8,Quarterback,QB,9,2\n"
            "2026-10-19T14:35:37Z,SF,Kurtis Rourke,4430431,00-0040589,21,3WR "
            "1TE,8,Quarterback,QB,9,3\n"
            "2026-10-19T14:35:37Z,SF,Deebo Samuel,3126486,00-0035719,21,3WR "
            "1TE,1,Wide Receiver,WR,2,1\n"
            "2026-10-19T14:35:37Z,SF,Ricky Pearsall,4428209,00-0039916,21,3WR "
            "1TE,1,Wide Receiver,WR,8,2\n"
            "2026-10-19T14:35:37Z,SF,Jake Moody,4249083,00-0038888,26,Special "
            "Teams,4,Kicker,K,1,1\n"
            "2026-10-18T14:35:37Z,SF,Old Snapshot,111,00-0000001,21,3WR 1TE,"
            "8,Quarterback,QB,9,1\n"
        )
        index = parse_depth_csv(csv_text, 2026)
        self.assertEqual(index["as_of"], "2026-10-19T14:35:37Z")
        self.assertIs(is_starter("SF", "QB", "00-0037834", "Brock Purdy",
                                 index), True)
        self.assertIs(is_starter("SF", "QB", "00-0036972", "Mac Jones",
                                 index), False)
        self.assertIs(is_starter("SF", "QB", None, "Kurtis Rourke",
                                 index), False)
        # Resolvable by normalized name only (espn id path too).
        self.assertIs(is_starter("SF", "QB", "4361741", None, index), True)
        self.assertIs(is_starter("SF", "WR", None, "Deebo Samuel", index), True)
        self.assertIs(is_starter("SF", "WR", "00-0039916", None, index), False)
        # Special teams are unmapped -> None (never treated as starters).
        self.assertIsNone(is_starter("SF", "K", "00-0038888", "Jake Moody",
                                     index))
        # The older snapshot is dropped (only the latest dt is indexed).
        self.assertIsNone(is_starter("SF", "QB", "00-0000001", None, index))

    def test_backup_key_out_no_debit(self):
        ctx = {"qb_status": "confirmed",
               "key_out": [{"position": "WR", "player_id": None,
                            "name": "Ricky Pearsall", "starter": False}]}
        self.assertEqual(team_injury_debit(ctx), (0.0, []))
        ctx["key_out"][0]["starter"] = True
        debit, attribution = team_injury_debit(ctx)
        self.assertLess(debit, 0.0)
        self.assertEqual([a["position"] for a in attribution], ["WR"])
        # Unknown (None) keeps the historical behavior.
        ctx["key_out"][0]["starter"] = None
        self.assertLess(team_injury_debit(ctx)[0], 0.0)

    def test_delta_ignores_backup_inactive(self):
        new_fc = SimpleNamespace(
            priced_qb_home=PURDY, priced_qb_away=RAMS_QB,
            qb_substituted_home=False, qb_substituted_away=False,
            home="SF", away="LA",
            injury_trace=[{"team": "SF", "slot": "h", "debit_pts": -0.4,
                           "attribution": [{"position": "WR", "raw": -0.8,
                                            "applied": -0.4,
                                            "starter": False}],
                           "priced_qb": PURDY, "qb_substituted": False}],
        )
        old = {"priced_qb_home": PURDY, "priced_qb_away": RAMS_QB,
               "qb_substituted_home": False, "qb_substituted_away": False,
               "injury_trace": []}
        changes = diff_injuries(old, new_fc)
        self.assertEqual(len(changes), 1)
        self.assertIn("backup inactive", changes[0])
        self.assertIn("immaterial", changes[0])
        delta = GameDelta(forecast_id="x", away="LA", home="SF",
                          margin_old=-1.0, margin_new=-1.0,
                          total_old=44.0, total_new=44.0,
                          injury_changes=changes)
        self.assertFalse(_is_key_player_news(delta))

    def test_delta_reports_starter_inactive(self):
        new_fc = SimpleNamespace(
            priced_qb_home=PURDY, priced_qb_away=RAMS_QB,
            qb_substituted_home=False, qb_substituted_away=False,
            home="SF", away="LA",
            injury_trace=[{"team": "SF", "slot": "h", "debit_pts": -0.4,
                           "attribution": [{"position": "LB", "raw": -0.2,
                                            "applied": -0.1,
                                            "starter": True}],
                           "priced_qb": PURDY, "qb_substituted": False}],
        )
        old = {"priced_qb_home": PURDY, "priced_qb_away": RAMS_QB,
               "injury_trace": []}
        changes = diff_injuries(old, new_fc)
        self.assertEqual(changes, ["LB (SF) OUT — newly inactive"])
        delta = GameDelta(forecast_id="x", away="LA", home="SF",
                          margin_old=-1.0, margin_new=-1.0,
                          total_old=44.0, total_new=44.0,
                          debit_old_home=0.0, debit_new_home=-2.0,
                          injury_changes=changes)
        self.assertTrue(_is_key_player_news(delta))


if __name__ == "__main__":
    unittest.main()
