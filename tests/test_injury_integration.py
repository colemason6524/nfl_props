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


if __name__ == "__main__":
    unittest.main()
