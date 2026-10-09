"""Phase E gap-fill: integrated QB-out path through build_game_forecast.

Gap analysis (invariant -> covering test; all prior tests live in
tests/test_injury_integration.py):
- fail-open missing/malformed/unknown-team -> PersonnelFailOpenTest
  (missing + malformed), PersonnelSchemaTest.test_unknown_team_stays_neutral
- legacy-string normalization -> test_legacy_string_key_out_normalizes,
  test_normalize_key_out_helper
- healthy-slate no-regression -> ResolvePricedQbTest
  .test_missing_personnel_is_no_regression,
  InjuryDebitTest.test_no_outs_zero_debit,
  ForecastDebitWiringTest.test_no_outs_projections_unchanged,
  ObservabilityFieldsTest.test_healthy_slate_defaults
- substitution (out->backup, non-out->starter, never OUT starter) ->
  ResolvePricedQbTest (all six tests)
- clamps (OL/DEF caps, -4.0 floor) ->
  test_position_table_caps_and_factor, test_multi_out_clamps_at_floor
- questionable-only zero -> test_questionable_never_debits,
  test_questionable_keeps_starter, questionable variant of
  test_no_outs_projections_unchanged
- as_dict round-trip -> test_as_dict_round_trip
- personnel-source unification -> test_state_personnel_wins_over_disk,
  test_no_disk_file_needed

Genuinely missing (added HERE, this file):
- QB-out end to end: priced backup id + substituted flag + residual debit
  + margin/total shift + notes/trace, at build_game_forecast level.
  Unit tests stop one layer short on each side (resolver returns the id;
  team_injury_debit prices the residual; wiring tests only cover WR1/RB1).
- QB-out with no viable backup at forecast level: priced None, no
  residual, empty trace, no debit note.
- non-dict team entry in the personnel cache stays neutral (fail-open).
"""
from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "tests")

from test_forecast import _fake_state, _game
from nfl_props.forecast import build_game_forecast
from nfl_props.sources.personnel import personnel_context


def _qb_room():
    return {
        "HOM_QB_01": {"epa_c": 0.30, "cpoe": 0.0, "sack_rate": 0.06,
                      "dropbacks": 300, "games": 10, "last_team": "HOM"},
        "HOM_QB_02": {"epa_c": -0.10, "cpoe": 0.0, "sack_rate": 0.06,
                      "dropbacks": 60, "games": 3, "last_team": "HOM"},
        "AWY_QB_01": {"epa_c": 0.10, "cpoe": 0.0, "sack_rate": 0.06,
                      "dropbacks": 300, "games": 10, "last_team": "AWY"},
    }


def _qb_state(personnel, qbs=None):
    state = _fake_state()
    state["teams"]["HOM"]["last_qb_id"] = "HOM_QB_01"
    state["teams"]["AWY"]["last_qb_id"] = "AWY_QB_01"
    state["qbs"] = dict(_qb_room()) if qbs is None else qbs
    state["personnel"] = personnel
    state["personnel_available"] = bool(personnel)
    return state


def _out_entry(**over):
    entry = {"qb_status": "out", "key_out": [],
             "backup_qb_id": "HOM_QB_02", "updated_at": "t",
             "source": "manual"}
    entry.update(over)
    return entry


class QbOutEndToEndTest(unittest.TestCase):
    def test_qb_out_prices_backup_with_residual_debit(self):
        base = build_game_forecast(_game(), _qb_state({}))
        fc = build_game_forecast(_game(), _qb_state({"HOM": _out_entry()}))
        # Backup (never the OUT starter) is the priced QB.
        self.assertEqual(fc.priced_qb_home, "HOM_QB_02")
        self.assertEqual(fc.priced_qb_away, "AWY_QB_01")
        self.assertTrue(fc.qb_substituted_home)
        self.assertFalse(fc.qb_substituted_away)
        # Residual debit (-2.0 raw x 0.5) trims home scoring only.
        self.assertAlmostEqual(fc.injury_debit_pts_home, -1.0)
        self.assertEqual(fc.injury_debit_pts_away, 0.0)
        self.assertAlmostEqual(fc.projected_margin,
                               base.projected_margin - 1.0, places=1)
        self.assertAlmostEqual(fc.projected_total,
                               base.projected_total - 1.0, places=1)
        self.assertTrue(any("substituted=True" in n for n in fc.notes))
        self.assertEqual(len(fc.injury_trace), 1)
        self.assertEqual(fc.injury_trace[0]["priced_qb"], "HOM_QB_02")
        self.assertTrue(fc.injury_trace[0]["qb_substituted"])

    def test_qb_out_without_viable_backup_no_residual(self):
        qbs = {"HOM_QB_01": dict(_qb_room()["HOM_QB_01"]),
               "AWY_QB_01": dict(_qb_room()["AWY_QB_01"])}
        personnel = {"HOM": _out_entry(backup_qb_id="GHOST_QB")}
        base = build_game_forecast(_game(), _qb_state({}, qbs))
        fc = build_game_forecast(_game(), _qb_state(personnel, qbs))
        self.assertIsNone(fc.priced_qb_home)
        self.assertFalse(fc.qb_substituted_home)
        self.assertEqual(fc.injury_debit_pts_home, 0.0)
        self.assertEqual(fc.injury_trace, [])
        self.assertEqual(fc.projected_margin, base.projected_margin)
        self.assertEqual(fc.projected_total, base.projected_total)
        self.assertFalse(any("injury debit" in n for n in fc.notes))
        self.assertTrue(any("HOM QB out" in n for n in fc.notes))


class PersonnelMalformedEntryTest(unittest.TestCase):
    def test_non_dict_team_entry_stays_neutral(self):
        for bad in ("garbage", 42, ["KC"], None):
            ctx = personnel_context("KC", cache={"KC": bad})
            self.assertFalse(ctx["personnel_available"])
            self.assertEqual(ctx["qb_status"], "unknown")
            self.assertEqual(ctx["key_out"], [])
            self.assertIsNone(ctx["backup_qb_id"])
