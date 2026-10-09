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


class InjuryDebitTest(unittest.TestCase):
    """Phase C: conservative team-side injury debit (NFL only)."""

    def test_no_outs_zero_debit(self):
        from nfl_props.injury_debit import team_injury_debit
        for ctx in (None, {}, {"qb_status": "unknown", "key_out": []},
                    {"qb_status": "confirmed", "key_out": []},
                    {"qb_status": "confirmed",
                     "key_out": [{"position": "K", "player_id": None}]},
                    {"qb_status": "questionable",
                     "key_out": [{"position": "WR1",
                                  "player_id": None}]}):
            debit, attr = team_injury_debit(ctx, False)
            self.assertEqual(debit, 0.0)
            self.assertEqual(attr, [])

    def test_questionable_never_debits(self):
        from nfl_props.injury_debit import team_injury_debit
        ctx = {"qb_status": "questionable",
               "key_out": [{"position": "WR1", "player_id": None},
                           {"position": "RB1", "player_id": None}]}
        self.assertEqual(team_injury_debit(ctx, False), (0.0, []))
        self.assertEqual(team_injury_debit(ctx, True), (0.0, []))
        qctx = {"qb_status": "questionable", "key_out": []}
        self.assertEqual(team_injury_debit(qctx, True), (0.0, []))

    def test_one_out_wr1_small_debit(self):
        from nfl_props.injury_debit import team_injury_debit
        ctx = {"qb_status": "confirmed",
               "key_out": [{"position": "WR1", "player_id": None}]}
        debit, attr = team_injury_debit(ctx, False)
        self.assertAlmostEqual(debit, -0.4)
        self.assertEqual(len(attr), 1)
        self.assertEqual(attr[0]["position"], "WR1")
        self.assertAlmostEqual(attr[0]["raw"], -0.8)
        self.assertAlmostEqual(attr[0]["applied"], -0.4)

    def test_out_qb_residual_debit_within_cap(self):
        from nfl_props.injury_debit import team_injury_debit
        ctx = {"qb_status": "out", "key_out": []}
        # No substitution (starter still priced) -> no residual gap.
        self.assertEqual(team_injury_debit(ctx, False), (0.0, []))
        debit, attr = team_injury_debit(ctx, True)
        self.assertAlmostEqual(debit, -1.0)
        self.assertEqual(len(attr), 1)
        self.assertEqual(attr[0]["position"], "QB")
        self.assertGreaterEqual(debit, -3.0 * 0.5)
        # key_out QB entries never double-count the residual.
        dup = {"qb_status": "out",
               "key_out": [{"position": "QB", "player_id": None}]}
        debit2, attr2 = team_injury_debit(dup, True)
        self.assertAlmostEqual(debit2, -1.0)
        self.assertEqual(len(attr2), 1)

    def test_position_table_caps_and_factor(self):
        from nfl_props.injury_debit import team_injury_debit
        ctx = {"qb_status": "confirmed",
               "key_out": [{"position": "RB1", "player_id": None}]}
        debit, _ = team_injury_debit(ctx, False)
        self.assertAlmostEqual(debit, -0.3)
        # OL group caps at -1.0 raw -> -0.5 factored.
        ctx = {"qb_status": "confirmed",
               "key_out": [{"position": p, "player_id": None}
                           for p in ("LT", "RT", "C", "RG", "LG")]}
        debit, attr = team_injury_debit(ctx, False)
        self.assertAlmostEqual(debit, -0.5)
        self.assertEqual(len(attr), 5)
        # DEF group caps at -0.6 raw -> -0.3 factored.
        ctx = {"qb_status": "confirmed",
               "key_out": [{"position": p, "player_id": None}
                           for p in ("LB", "CB", "S", "DE", "DT")]}
        debit, _ = team_injury_debit(ctx, False)
        self.assertAlmostEqual(debit, -0.3)
        # Case-insensitive prefix match.
        ctx = {"qb_status": "confirmed",
               "key_out": [{"position": "wr1", "player_id": None}]}
        debit, _ = team_injury_debit(ctx, False)
        self.assertAlmostEqual(debit, -0.4)

    def test_multi_out_clamps_at_floor(self):
        from nfl_props.injury_debit import team_injury_debit
        key_out = ([{"position": "WR1", "player_id": None},
                    {"position": "WR2", "player_id": None},
                    {"position": "RB1", "player_id": None}]
                   + [{"position": p, "player_id": None}
                      for p in ("LT", "RT", "C", "LG")]
                   + [{"position": p, "player_id": None}
                      for p in ("LB", "CB", "S", "DE")])
        ctx = {"qb_status": "out", "key_out": key_out}
        debit, attr = team_injury_debit(ctx, True)
        # QB -1.0 + WR -0.8 + RB -0.3 + OL-capped -0.5 + DEF-capped -0.3.
        self.assertAlmostEqual(debit, -2.9)
        self.assertTrue(len(attr) > 1)
        # Extreme outs clamp at the -4.0 floor (conservative hard cap).
        flood = {"qb_status": "out",
                 "key_out": [{"position": f"WR{i}", "player_id": None}
                             for i in range(10)]}
        debit_flood, _ = team_injury_debit(flood, True)
        self.assertAlmostEqual(debit_flood, -4.0)


class ForecastDebitWiringTest(unittest.TestCase):
    """Phase C wiring: debits shift margin/total via build_game_forecast."""

    def _forecast_state(self, personnel):
        import sys
        sys.path.insert(0, "tests")
        from test_forecast import _fake_state
        state = _fake_state()
        state["personnel_available"] = bool(personnel)
        state["personnel"] = personnel or {}
        return state

    def _game(self):
        import sys
        sys.path.insert(0, "tests")
        from test_forecast import _game
        return _game()

    def test_no_outs_projections_unchanged(self):
        from nfl_props.forecast import build_game_forecast
        clean = self._forecast_state({})
        base = build_game_forecast(self._game(), clean)
        for personnel in (
                {},
                {"HOM": {"qb_status": "confirmed", "key_out": [],
                         "backup_qb_id": None, "updated_at": "t",
                         "source": "manual"}},
                {"HOM": {"qb_status": "questionable",
                         "key_out": [{"position": "WR1",
                                      "player_id": None}],
                         "backup_qb_id": None, "updated_at": "t",
                         "source": "manual"}}):
            fc = build_game_forecast(self._game(),
                                     self._forecast_state(personnel))
            self.assertEqual(fc.projected_margin, base.projected_margin)
            self.assertEqual(fc.projected_total, base.projected_total)
            self.assertFalse([n for n in fc.notes
                              if "injury debit" in n])

    def test_home_wr1_out_shifts_margin_and_total(self):
        from nfl_props.forecast import build_game_forecast
        base = build_game_forecast(self._game(), self._forecast_state({}))
        personnel = {"HOM": {"qb_status": "confirmed",
                             "key_out": [{"position": "WR1",
                                          "player_id": None}],
                             "backup_qb_id": None, "updated_at": "t",
                             "source": "manual"}}
        fc = build_game_forecast(self._game(),
                                 self._forecast_state(personnel))
        self.assertAlmostEqual(
            fc.projected_margin, base.projected_margin - 0.4, places=1)
        self.assertAlmostEqual(
            fc.projected_total, base.projected_total - 0.4, places=1)
        self.assertTrue(any("HOM injury debit -0.4" in n
                            for n in fc.notes))

    def test_away_out_improves_home_margin(self):
        from nfl_props.forecast import build_game_forecast
        base = build_game_forecast(self._game(), self._forecast_state({}))
        personnel = {"AWY": {"qb_status": "confirmed",
                             "key_out": [{"position": "RB1",
                                          "player_id": None}],
                             "backup_qb_id": None, "updated_at": "t",
                             "source": "manual"}}
        fc = build_game_forecast(self._game(),
                                 self._forecast_state(personnel))
        self.assertAlmostEqual(
            fc.projected_margin, base.projected_margin + 0.3, places=1)
        self.assertAlmostEqual(
            fc.projected_total, base.projected_total - 0.3, places=1)
        self.assertTrue(any("AWY injury debit -0.3" in n
                            for n in fc.notes))


class ObservabilityFieldsTest(unittest.TestCase):
    """Phase D: structured injury observability on GameForecast (NFL)."""

    def _forecast_state(self, personnel):
        import sys
        sys.path.insert(0, "tests")
        from test_forecast import _fake_state
        state = _fake_state()
        state["personnel_available"] = bool(personnel)
        state["personnel"] = personnel or {}
        return state

    def _game(self):
        import sys
        sys.path.insert(0, "tests")
        from test_forecast import _game
        return _game()

    def _hom_wr1_out(self):
        return {"HOM": {"qb_status": "confirmed",
                        "key_out": [{"position": "WR1",
                                     "player_id": None}],
                        "backup_qb_id": None, "updated_at": "t",
                        "source": "manual"}}

    def test_healthy_slate_defaults(self):
        from nfl_props.forecast import build_game_forecast
        fc = build_game_forecast(self._game(), self._forecast_state({}))
        self.assertEqual(fc.injury_debit_pts_home, 0.0)
        self.assertEqual(fc.injury_debit_pts_away, 0.0)
        self.assertEqual(fc.injury_trace, [])
        self.assertIsNone(fc.priced_qb_home)
        self.assertIsNone(fc.priced_qb_away)
        self.assertFalse(fc.qb_substituted_home)
        self.assertFalse(fc.qb_substituted_away)
        d = fc.as_dict()
        for key in ("injury_debit_pts_home", "injury_debit_pts_away",
                    "injury_trace", "priced_qb_home", "priced_qb_away",
                    "qb_substituted_home", "qb_substituted_away"):
            self.assertIn(key, d)

    def test_debit_populates_fields_and_notes(self):
        from nfl_props.forecast import build_game_forecast
        fc = build_game_forecast(self._game(),
                                 self._forecast_state(self._hom_wr1_out()))
        self.assertAlmostEqual(fc.injury_debit_pts_home, -0.4)
        self.assertEqual(fc.injury_debit_pts_away, 0.0)
        self.assertEqual(len(fc.injury_trace), 1)
        entry = fc.injury_trace[0]
        self.assertEqual(entry["team"], "HOM")
        self.assertAlmostEqual(entry["debit_pts"], -0.4)
        self.assertTrue(any("HOM injury debit -0.4" in n
                            for n in fc.notes))
        self.assertTrue(any("priced QB" in n and "substituted=False" in n
                            for n in fc.notes))

    def test_priced_qb_ids_recorded(self):
        from nfl_props.forecast import build_game_forecast
        state = self._forecast_state(self._hom_wr1_out())
        state["teams"]["HOM"]["last_qb_id"] = "HOM_QB_01"
        state["teams"]["AWY"]["last_qb_id"] = "AWY_QB_01"
        fc = build_game_forecast(self._game(), state)
        self.assertEqual(fc.priced_qb_home, "HOM_QB_01")
        self.assertEqual(fc.priced_qb_away, "AWY_QB_01")
        self.assertFalse(fc.qb_substituted_home)
        self.assertFalse(fc.qb_substituted_away)

    def test_as_dict_round_trip(self):
        from nfl_props.forecast import GameForecast, build_game_forecast
        from nfl_props.references import ForecastReference
        fc = build_game_forecast(self._game(),
                                 self._forecast_state(self._hom_wr1_out()))
        d = fc.as_dict()
        d["references"] = [ForecastReference(**r)
                           for r in d["references"]]
        fc2 = GameForecast(**d)
        self.assertEqual(fc2, fc)

    def test_state_personnel_wins_over_disk(self):
        import json
        from nfl_props.forecast import build_game_forecast
        disk = {"AWY": {"qb_status": "out",
                        "key_out": [{"position": "WR1",
                                     "player_id": None}],
                        "backup_qb_id": None, "updated_at": "t",
                        "source": "manual"}}
        patcher = _patch_personnel_path(json.dumps(disk))
        patcher.start()
        try:
            base = build_game_forecast(self._game(),
                                       self._forecast_state({}))
            fc = build_game_forecast(
                self._game(), self._forecast_state(self._hom_wr1_out()))
        finally:
            patcher.stop()
            patcher._tmpdir.cleanup()
        # State (HOM WR1 out) respected; disagreeing on-disk (AWY out)
        # cache ignored by both the debit block and the QB-status notes.
        self.assertAlmostEqual(
            fc.projected_margin, base.projected_margin - 0.4, places=1)
        self.assertAlmostEqual(fc.injury_debit_pts_home, -0.4)
        self.assertEqual(fc.injury_debit_pts_away, 0.0)
        self.assertFalse(any("AWY QB out" in n for n in fc.notes))
        self.assertFalse(any("AWY injury debit" in n for n in fc.notes))

    def test_no_disk_file_needed(self):
        from nfl_props.forecast import build_game_forecast
        patcher = _patch_personnel_path(None)  # PERSONNEL_PATH missing
        patcher.start()
        try:
            fc = build_game_forecast(
                self._game(), self._forecast_state(self._hom_wr1_out()))
            bare = self._forecast_state({})
            del bare["personnel"]
            fc0 = build_game_forecast(self._game(), bare)
        finally:
            patcher.stop()
            patcher._tmpdir.cleanup()
        self.assertAlmostEqual(fc.injury_debit_pts_home, -0.4)
        self.assertEqual(fc0.injury_debit_pts_home, 0.0)
        self.assertEqual(fc0.injury_trace, [])


if __name__ == "__main__":
    unittest.main()


class PregameRegressionTest(unittest.TestCase):
    """Late-pregame guarantees: questionable-IN, QB-out backup re-price."""

    def _forecast_state(self, personnel):
        import sys
        sys.path.insert(0, "tests")
        from test_forecast import _fake_state
        state = _fake_state()
        state["personnel_available"] = bool(personnel)
        state["personnel"] = personnel or {}
        return state

    def _game(self):
        import sys
        sys.path.insert(0, "tests")
        from test_forecast import _game
        return _game()

    def _qb_state(self, personnel):
        state = self._forecast_state(personnel)
        state["teams"]["HOM"]["last_qb_id"] = "HOM_QB_01"
        state["teams"]["AWY"]["last_qb_id"] = "AWY_QB_01"
        state["qbs"] = {
            "HOM_QB_01": {"epa_c": 0.30, "cpoe": 0.0, "sack_rate": 0.06,
                          "dropbacks": 300, "games": 10,
                          "last_team": "HOM"},
            "HOM_QB_02": {"epa_c": -0.10, "cpoe": 0.0, "sack_rate": 0.06,
                          "dropbacks": 60, "games": 3, "last_team": "HOM"},
            "AWY_QB_01": {"epa_c": 0.10, "cpoe": 0.0, "sack_rate": 0.06,
                          "dropbacks": 300, "games": 10,
                          "last_team": "AWY"},
        }
        return state

    def test_questionable_qb_is_in_zero_debit(self):
        """Questionable QB: starter still priced, zero debit, note only."""
        from nfl_props.forecast import build_game_forecast
        from nfl_props.injury_debit import team_injury_debit
        base = build_game_forecast(self._game(),
                                   self._qb_state({}))
        personnel = {"HOM": {"qb_status": "questionable",
                             "key_out": [{"position": "WR1",
                                          "player_id": None}],
                             "backup_qb_id": "HOM_QB_02"}}
        fc = build_game_forecast(self._game(), self._qb_state(personnel))
        self.assertEqual(fc.priced_qb_home, "HOM_QB_01")
        self.assertFalse(fc.qb_substituted_home)
        self.assertEqual(fc.injury_debit_pts_home, 0.0)
        self.assertEqual(fc.projected_margin, base.projected_margin)
        self.assertEqual(fc.projected_total, base.projected_total)
        self.assertTrue(any("HOM QB questionable" in n for n in fc.notes))
        # Unit level: questionable never debits, even flagged substituted.
        ctx = {"qb_status": "questionable",
               "key_out": [{"position": "WR1", "player_id": None}]}
        self.assertEqual(team_injury_debit(ctx, False), (0.0, []))
        self.assertEqual(team_injury_debit(ctx, True), (0.0, []))

    def test_qb_out_backup_repriced(self):
        """QB-out: backup priced, substitution flagged, residual debited."""
        from nfl_props.forecast import build_game_forecast
        base = build_game_forecast(self._game(), self._qb_state({}))
        personnel = {"HOM": {"qb_status": "out",
                             "backup_qb_id": "HOM_QB_02"}}
        fc = build_game_forecast(self._game(), self._qb_state(personnel))
        self.assertEqual(fc.priced_qb_home, "HOM_QB_02")
        self.assertTrue(fc.qb_substituted_home)
        self.assertAlmostEqual(fc.injury_debit_pts_home, -1.0)
        # Backup is worse than the starter: margin and total both fall.
        self.assertLess(fc.projected_margin, base.projected_margin)
        self.assertLess(fc.projected_total, base.projected_total)
        self.assertTrue(any("HOM QB out" in n for n in fc.notes))
        self.assertTrue(any("HOM injury debit -1.0" in n
                            for n in fc.notes))


