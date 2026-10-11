"""QB resolution proper-fix tests (NFL): alias mapping, recency, override.

Covers the 2026-10-11 misprices:
- WAS priced fill-in Kaliakmanis over returning starter Daniels: the
  `starter_qb_id` override path must win for non-"out" statuses.
- BAL priced RG3 (last threw 2020) over Huntley: the backup fallback must
  exclude QBs stale for more than QB_RECENCY_WINDOW seasons.
- personnel cache uses ESPN spellings (WSH/LAR); the model uses WAS/LA.
"""
from __future__ import annotations

import unittest

from nfl_props.config import CURRENT_SEASON
from nfl_props.forecasting import (
    QB_RECENCY_WINDOW,
    _qb_is_recent,
    game_features,
    resolve_priced_qb_id,
)
from nfl_props.sources.personnel import personnel_context

# Real ids from the 2026-10-11 boards.
DANIELS = "00-0039910"
KALIAKMANIS = "00-0041117"
HUNTLEY = "00-0035993"
RG3 = "00-0029665"


def _qb(epa_c=0.0, dropbacks=100, last_team="BAL", last_season=None):
    qb = {"epa_c": epa_c, "cpoe": 0.0, "sack_rate": 0.06,
          "dropbacks": dropbacks, "games": 10, "last_team": last_team}
    if last_season is not None:
        qb["last_season"] = last_season
    return qb


def _state(qbs, personnel, home="BAL", away="DEN", home_starter="BAL_QB_01"):
    teams = {
        home: {"off": 0.0, "def": 0.0, "off_pass": 0.0,
               "def_pass": 0.0, "off_rush": 0.0, "def_rush": 0.0,
               "pace": 63.0, "last_qb_id": home_starter},
        away: {"off": 0.0, "def": 0.0, "off_pass": 0.0,
               "def_pass": 0.0, "off_rush": 0.0, "def_rush": 0.0,
               "pace": 63.0, "last_qb_id": "DEN_QB_01"},
    }
    qbs = dict(qbs)
    qbs.setdefault("DEN_QB_01", _qb(last_team="DEN"))
    return {"league": {"pace": 63.0}, "teams": teams,
            "qbs": qbs, "personnel": personnel}


class AliasMappingTest(unittest.TestCase):
    def test_was_resolves_wsh_cache_entry(self):
        cache = {"WSH": {"qb_status": "out", "backup_qb_id": HUNTLEY,
                         "updated_at": "t", "source": "manual"}}
        ctx = personnel_context("WAS", cache=cache)
        self.assertTrue(ctx["personnel_available"])
        self.assertEqual(ctx["qb_status"], "out")
        self.assertEqual(ctx["backup_qb_id"], HUNTLEY)

    def test_la_resolves_lar_cache_entry(self):
        cache = {"LAR": {"qb_status": "confirmed",
                         "updated_at": "t", "source": "manual"}}
        ctx = personnel_context("LA", cache=cache)
        self.assertTrue(ctx["personnel_available"])
        self.assertEqual(ctx["qb_status"], "confirmed")

    def test_canonical_keys_still_resolve(self):
        cache = {"KC": {"qb_status": "out", "updated_at": "t",
                        "source": "manual"}}
        ctx = personnel_context("KC", cache=cache)
        self.assertTrue(ctx["personnel_available"])


class BackupRecencyTest(unittest.TestCase):
    def _bal_room(self):
        # RG3: most career dropbacks, but last threw 6 seasons ago.
        # Huntley: fewer dropbacks, active this season.
        return {
            "BAL_QB_01": _qb(epa_c=0.20, dropbacks=1500, last_team="BAL",
                             last_season=CURRENT_SEASON),
            RG3: _qb(epa_c=-0.28, dropbacks=1045, last_team="BAL",
                     last_season=CURRENT_SEASON - 6),
            HUNTLEY: _qb(epa_c=0.38, dropbacks=720, last_team="BAL",
                         last_season=CURRENT_SEASON),
        }

    def test_stale_high_dropbacks_qb_excluded(self):
        qbs = self._bal_room()
        personnel = {"BAL": {"qb_status": "out", "backup_qb_id": None}}
        feats = game_features(
            _state(qbs, personnel), {"home_team": "BAL", "away_team": "DEN"})
        self.assertEqual(feats["priced_qb_h"], HUNTLEY)

    def test_all_stale_falls_back_to_league_average(self):
        qbs = {
            "BAL_QB_01": _qb(epa_c=0.20, dropbacks=1500, last_team="BAL",
                             last_season=CURRENT_SEASON),
            RG3: _qb(epa_c=-0.28, dropbacks=1045, last_team="BAL",
                     last_season=CURRENT_SEASON - 6),
        }
        personnel = {"BAL": {"qb_status": "out", "backup_qb_id": None}}
        feats = game_features(
            _state(qbs, personnel), {"home_team": "BAL", "away_team": "DEN"})
        self.assertIsNone(feats["priced_qb_h"])
        self.assertEqual(feats["qb_epa_h"], 0.0)

    def test_missing_last_season_stays_eligible(self):
        # Legacy states / fixtures carry no last_season: fail-open.
        qbs = {
            "BAL_QB_01": _qb(epa_c=0.20, dropbacks=1500, last_team="BAL"),
            HUNTLEY: _qb(epa_c=0.38, dropbacks=720, last_team="BAL"),
        }
        personnel = {"BAL": {"qb_status": "out", "backup_qb_id": None}}
        feats = game_features(
            _state(qbs, personnel), {"home_team": "BAL", "away_team": "DEN"})
        self.assertEqual(feats["priced_qb_h"], HUNTLEY)

    def test_is_recent_boundary(self):
        self.assertTrue(_qb_is_recent({}, CURRENT_SEASON))
        self.assertTrue(_qb_is_recent({"last_season": CURRENT_SEASON},
                                      CURRENT_SEASON))
        self.assertTrue(_qb_is_recent(
            {"last_season": CURRENT_SEASON - QB_RECENCY_WINDOW},
            CURRENT_SEASON))
        self.assertFalse(_qb_is_recent(
            {"last_season": CURRENT_SEASON - QB_RECENCY_WINDOW - 1},
            CURRENT_SEASON))


class StarterOverrideTest(unittest.TestCase):
    def _was_room(self):
        return {
            DANIELS: _qb(epa_c=0.18, dropbacks=1039, last_team="WAS",
                         last_season=CURRENT_SEASON),
            KALIAKMANIS: _qb(epa_c=-0.05, dropbacks=36, last_team="WAS",
                             last_season=CURRENT_SEASON),
        }

    def test_confirmed_with_override_prices_starter(self):
        qbs = self._was_room()
        personnel = {"WSH": {"qb_status": "confirmed",
                             "starter_qb_id": DANIELS}}
        feats = game_features(
            _state(qbs, personnel, home="WAS",
                   home_starter=KALIAKMANIS),
            {"home_team": "WAS", "away_team": "DEN"})
        self.assertEqual(feats["priced_qb_h"], DANIELS)

    def test_out_with_override_still_prices_backup(self):
        # The override names the injured starter; "out" must never
        # return him -- the backup path wins.
        qbs = self._was_room()
        personnel = {"WSH": {"qb_status": "out",
                             "backup_qb_id": KALIAKMANIS,
                             "starter_qb_id": DANIELS}}
        team_state = {"last_qb_id": DANIELS, "off_pass": 0.0}
        ctx = personnel_context("WAS", cache=personnel)
        self.assertEqual(
            resolve_priced_qb_id("WAS", team_state, qbs, ctx), KALIAKMANIS)

    def test_absent_override_id_ignored(self):
        qbs = self._was_room()
        personnel = {"WSH": {"qb_status": "confirmed",
                             "starter_qb_id": "GHOST_QB"}}
        team_state = {"last_qb_id": KALIAKMANIS, "off_pass": 0.0}
        ctx = personnel_context("WAS", cache=personnel)
        self.assertEqual(
            resolve_priced_qb_id("WAS", team_state, qbs, ctx), KALIAKMANIS)

    def test_no_override_no_regression(self):
        qbs = self._was_room()
        personnel = {"WSH": {"qb_status": "confirmed"}}
        team_state = {"last_qb_id": KALIAKMANIS, "off_pass": 0.0}
        ctx = personnel_context("WAS", cache=personnel)
        self.assertEqual(
            resolve_priced_qb_id("WAS", team_state, qbs, ctx), KALIAKMANIS)


if __name__ == "__main__":
    unittest.main()
