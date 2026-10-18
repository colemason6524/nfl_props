"""Regression: starter-out detection via qb_details (NFL Phase B).

The ESPN feed lists healthy backups on the injury report, so
_resolve_qb_status reports qb_status="confirmed" even when the actual
starter is out (e.g. Hurts on IR, McKee confirmed). The priced-QB
resolver must detect the starter-out case by matching the incumbent's
name against the qb_details "out" entries and price the confirmed
backup instead of the injured starter.
"""
from __future__ import annotations

import unittest

from nfl_props.forecasting import (
    _norm_qb_name,
    game_features,
    qb_starter_is_out,
    resolve_priced_qb_id,
)


def _qbs(with_names=True):
    room = {
        "KC_QB_01": {"epa_c": 0.30, "cpoe": 0.0, "sack_rate": 0.06,
                     "dropbacks": 300, "games": 10, "last_team": "KC"},
        "KC_QB_02": {"epa_c": -0.10, "cpoe": 0.0, "sack_rate": 0.06,
                     "dropbacks": 60, "games": 3, "last_team": "KC"},
        "KC_QB_03": {"epa_c": 0.05, "cpoe": 0.0, "sack_rate": 0.06,
                     "dropbacks": 90, "games": 2, "last_team": "KC"},
        "DEN_QB_01": {"epa_c": 0.10, "cpoe": 0.0, "sack_rate": 0.06,
                      "dropbacks": 300, "games": 10, "last_team": "DEN"},
    }
    if with_names:
        room["KC_QB_01"]["name"] = "P.Mahomes"
        room["KC_QB_02"]["name"] = "G.Minshew"
        room["KC_QB_03"]["name"] = "C.Oladokun"
        room["DEN_QB_01"]["name"] = "B.Nix"
    return room


def _state(qbs, personnel):
    return {
        "league": {"pace": 63.0},
        "teams": {
            "KC": {"off": 0.10, "def": 0.05, "off_pass": 0.12,
                   "def_pass": 0.03, "off_rush": 0.02, "def_rush": 0.01,
                   "pace": 63.0, "last_qb_id": "KC_QB_01"},
            "DEN": {"off": 0.00, "def": 0.00, "off_pass": 0.00,
                    "def_pass": 0.00, "off_rush": 0.00, "def_rush": 0.00,
                    "pace": 63.0, "last_qb_id": "DEN_QB_01"},
        },
        "qbs": qbs,
        "personnel": personnel,
    }


def _game():
    return {"home_team": "KC", "away_team": "DEN"}


class QbDetailsStarterOutTest(unittest.TestCase):
    def test_confirmed_status_with_starter_out_prices_backup(self):
        # Feed shape: starter out, healthy backup listed -> "confirmed".
        personnel = {"KC": {
            "qb_status": "confirmed",
            "backup_qb_id": None,
            "qb_details": [
                {"name": "Patrick Mahomes", "status": "out"},
                {"name": "Gardner Minshew", "status": "confirmed"},
            ],
        }}
        feats = game_features(_state(_qbs(), personnel), _game())
        self.assertEqual(feats["priced_qb_h"], "KC_QB_02")

    def test_suffix_names_normalize(self):
        self.assertEqual(_norm_qb_name("Joe Milton III"), "j.milton")
        self.assertEqual(_norm_qb_name("J.Milton"), "j.milton")
        self.assertEqual(_norm_qb_name("Jalen Hurts"), "j.hurts")
        self.assertEqual(_norm_qb_name("J.Hurts"), "j.hurts")

    def test_missing_names_fail_open_to_starter(self):
        personnel = {"KC": {
            "qb_status": "confirmed",
            "qb_details": [
                {"name": "Patrick Mahomes", "status": "out"},
                {"name": "Gardner Minshew", "status": "confirmed"},
            ],
        }}
        feats = game_features(_state(_qbs(with_names=False), personnel),
                              _game())
        self.assertEqual(feats["priced_qb_h"], "KC_QB_01")

    def test_questionable_starter_stays_annotation_only(self):
        personnel = {"KC": {
            "qb_status": "questionable",
            "qb_details": [
                {"name": "Patrick Mahomes", "status": "questionable"},
                {"name": "Gardner Minshew", "status": "confirmed"},
            ],
        }}
        feats = game_features(_state(_qbs(), personnel), _game())
        self.assertEqual(feats["priced_qb_h"], "KC_QB_01")

    def test_out_backup_does_not_flag_healthy_starter(self):
        personnel = {"KC": {
            "qb_status": "confirmed",
            "qb_details": [
                {"name": "Patrick Mahomes", "status": "confirmed"},
                {"name": "Gardner Minshew", "status": "out"},
            ],
        }}
        state = _state(_qbs(), personnel)
        ctx = {"qb_status": "confirmed",
               "qb_details": personnel["KC"]["qb_details"]}
        self.assertFalse(
            qb_starter_is_out(state["teams"]["KC"], state["qbs"], ctx))
        self.assertEqual(
            resolve_priced_qb_id("KC", state["teams"]["KC"],
                                 state["qbs"], ctx), "KC_QB_01")


if __name__ == "__main__":
    unittest.main()
