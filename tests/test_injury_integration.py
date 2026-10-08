"""Injury/personnel integration tests (Phase A, NFL only).

Covers the fail-open personnel adapter and its extended schema:
- missing cache file -> load_personnel() == {} and personnel_available False
- malformed JSON -> same fail-open behaviour
- legacy plain-string key_out entries normalize to {position, player_id} dicts
- new dict key_out entries + backup_qb_id round-trip through the context
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nfl_props.sources import personnel as personnel_mod
from nfl_props.sources.personnel import normalize_key_out, personnel_context
from nfl_props.forecasting import (
    game_features,
    resolve_priced_qb_id,
)


def _patch_personnel_path(content):
    """Point PERSONNEL_PATH at a temp file (content str|None for missing)."""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "personnel.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    patcher = mock.patch.object(personnel_mod, "PERSONNEL_PATH", path)
    patcher._tmpdir = tmp  # keep alive for the patch duration
    return patcher


class PersonnelFailOpenTest(unittest.TestCase):
    def test_missing_file_returns_empty_and_unavailable(self):
        patcher = _patch_personnel_path(None)
        patcher.start()
        try:
            self.assertEqual(personnel_mod.load_personnel(), {})
        finally:
            patcher.stop()
            patcher._tmpdir.cleanup()
        ctx = personnel_context("KC", cache={})
        self.assertFalse(ctx["personnel_available"])
        self.assertEqual(ctx["qb_status"], "unknown")
        self.assertEqual(ctx["key_out"], [])
        self.assertIsNone(ctx["backup_qb_id"])

    def test_malformed_json_returns_empty_and_unavailable(self):
        patcher = _patch_personnel_path("{not valid json")
        patcher.start()
        try:
            self.assertEqual(personnel_mod.load_personnel(), {})
        finally:
            patcher.stop()
            patcher._tmpdir.cleanup()
        ctx = personnel_context("KC", cache={})
        self.assertFalse(ctx["personnel_available"])


class PersonnelSchemaTest(unittest.TestCase):
    def test_legacy_string_key_out_normalizes(self):
        cache = {"KC": {"qb_status": "confirmed", "key_out": ["WR1", "LT"],
                        "updated_at": "t", "source": "manual"}}
        ctx = personnel_context("KC", cache=cache)
        self.assertTrue(ctx["personnel_available"])
        self.assertEqual(ctx["key_out"],
                         [{"position": "WR1", "player_id": None},
                          {"position": "LT", "player_id": None}])

    def test_new_dict_key_out_and_backup_qb_id_round_trip(self):
        cache = {"BUF": {"qb_status": "questionable",
                         "key_out": [{"position": "WR1",
                                      "player_id": "BUF_WR_01"}],
                         "backup_qb_id": "BUF_QB_02",
                         "updated_at": "t", "source": "manual"}}
        ctx = personnel_context("BUF", cache=cache)
        self.assertTrue(ctx["personnel_available"])
        self.assertEqual(ctx["qb_status"], "questionable")
        self.assertEqual(ctx["key_out"],
                         [{"position": "WR1", "player_id": "BUF_WR_01"}])
        self.assertEqual(ctx["backup_qb_id"], "BUF_QB_02")

    def test_normalize_key_out_helper(self):
        self.assertEqual(normalize_key_out(None), [])
        self.assertEqual(normalize_key_out("WR1"),
                         [{"position": "WR1", "player_id": None}])
        self.assertEqual(
            normalize_key_out([{"position": "RB1", "player_id": "X"},
                               "WR2", 42, None]),
            [{"position": "RB1", "player_id": "X"},
             {"position": "WR2", "player_id": None}])

    def test_unknown_team_stays_neutral(self):
        ctx = personnel_context("ZZZ", cache={"KC": {"qb_status": "out"}})
        self.assertFalse(ctx["personnel_available"])
        self.assertEqual(ctx["qb_status"], "unknown")
        self.assertEqual(ctx["key_out"], [])
        self.assertIsNone(ctx["backup_qb_id"])


class _PhaseBState:
    """Minimal live-forecast state: one rated team (KC) + QB room."""

    @staticmethod
    def state(qbs, personnel):
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

    @staticmethod
    def qbs():
        return {
            # starter: strong centered EPA.
            "KC_QB_01": {"epa_c": 0.30, "cpoe": 0.0, "sack_rate": 0.06,
                         "dropbacks": 300, "games": 10, "last_team": "KC"},
            # backup: weak centered EPA.
            "KC_QB_02": {"epa_c": -0.10, "cpoe": 0.0, "sack_rate": 0.06,
                         "dropbacks": 60, "games": 3, "last_team": "KC"},
            # third-string: middling EPA, most dropbacks after starter.
            "KC_QB_03": {"epa_c": 0.05, "cpoe": 0.0, "sack_rate": 0.06,
                         "dropbacks": 90, "games": 2, "last_team": "KC"},
            "DEN_QB_01": {"epa_c": 0.10, "cpoe": 0.0, "sack_rate": 0.06,
                          "dropbacks": 300, "games": 10, "last_team": "DEN"},
        }

    @staticmethod
    def game():
        return {"home_team": "KC", "away_team": "DEN"}


class ResolvePricedQbTest(unittest.TestCase):
    def test_out_starter_prices_backup(self):
        qbs = _PhaseBState.qbs()
        personnel = {"KC": {"qb_status": "out",
                            "backup_qb_id": "KC_QB_02"}}
        state = _PhaseBState.state(qbs, personnel)
        feats = game_features(state, _PhaseBState.game())
        self.assertEqual(feats["priced_qb_h"], "KC_QB_02")
        self.assertEqual(feats["priced_qb_a"], "DEN_QB_01")
        # backup value (negative delta), not the strong starter.
        self.assertLess(feats["qb_epa_h"], 0.0)
        base = _PhaseBState.state(qbs, {})
        base_feats = game_features(base, _PhaseBState.game())
        self.assertGreater(base_feats["qb_epa_h"], 0.0)
        self.assertNotEqual(feats["qb_epa_h"], base_feats["qb_epa_h"])

    def test_out_without_backup_falls_back_to_next_most_dropbacks(self):
        qbs = _PhaseBState.qbs()
        personnel = {"KC": {"qb_status": "out", "backup_qb_id": None}}
        feats = game_features(_PhaseBState.state(qbs, personnel),
                              _PhaseBState.game())
        # KC_QB_03 has the most dropbacks among non-starters.
        self.assertEqual(feats["priced_qb_h"], "KC_QB_03")
        self.assertNotEqual(
            feats["qb_epa_h"],
            game_features(_PhaseBState.state(qbs, {}),
                          _PhaseBState.game())["qb_epa_h"])

    def test_out_unknown_backup_ids_fall_back_to_league_average(self):
        qbs = _PhaseBState.qbs()
        for personnel in ({"KC": {"qb_status": "out",
                                  "backup_qb_id": "GHOST_QB"}},
                          {"KC": {"qb_status": "out",
                                  "backup_qb_id": "KC_QB_01"}}):
            feats = game_features(_PhaseBState.state(qbs, personnel),
                                  _PhaseBState.game())
            # ghost backup -> next-most-dropbacks teammate prices instead.
            self.assertEqual(feats["priced_qb_h"], "KC_QB_03")
        only_starter = {"KC_QB_01": dict(qbs["KC_QB_01"])}
        feats = game_features(
            _PhaseBState.state(only_starter,
                               {"KC": {"qb_status": "out",
                                       "backup_qb_id": "GHOST_QB"}}),
            _PhaseBState.game())
        self.assertIsNone(feats["priced_qb_h"])
        self.assertEqual(feats["qb_epa_h"], 0.0)

    def test_questionable_keeps_starter(self):
        qbs = _PhaseBState.qbs()
        personnel = {"KC": {"qb_status": "questionable",
                            "backup_qb_id": "KC_QB_02"}}
        feats = game_features(_PhaseBState.state(qbs, personnel),
                              _PhaseBState.game())
        self.assertEqual(feats["priced_qb_h"], "KC_QB_01")
        self.assertEqual(
            feats["qb_epa_h"],
            game_features(_PhaseBState.state(qbs, {}),
                          _PhaseBState.game())["qb_epa_h"])

    def test_missing_personnel_is_no_regression(self):
        qbs = _PhaseBState.qbs()
        plain = game_features(_PhaseBState.state(qbs, {}),
                              _PhaseBState.game())
        for state in ({"league": {"pace": 63.0},
                       "teams": _PhaseBState.state(qbs, {})["teams"],
                       "qbs": qbs},
                      _PhaseBState.state(
                          qbs, {"KC": {"qb_status": "confirmed"}})):
            feats = game_features(state, _PhaseBState.game())
            self.assertEqual(feats["priced_qb_h"], "KC_QB_01")
            self.assertEqual(feats["priced_qb_a"], "DEN_QB_01")
            self.assertEqual(feats["qb_epa_h"], plain["qb_epa_h"])
            self.assertEqual(feats["qb_epa_a"], plain["qb_epa_a"])

    def test_resolver_unit_never_returns_out_starter(self):
        qbs = _PhaseBState.qbs()
        team_state = {"last_qb_id": "KC_QB_01", "off_pass": 0.12}
        ctx = {"qb_status": "out", "backup_qb_id": "KC_QB_01"}
        self.assertEqual(
            resolve_priced_qb_id("KC", team_state, qbs, ctx), "KC_QB_03")


if __name__ == "__main__":
    unittest.main()
