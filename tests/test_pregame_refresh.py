"""Tests for the late-pregame injury refresh (NFL).

personnel_feed: ESPN/nflverse parsers map Out->out, Questionable->QB-only
annotation, position aliases (OT->T, OG->G), per-entry fail-open, and
zero-team / unparseable payloads raise PersonnelFeedError. fetch_and_store
uses the nflverse fallback when ESPN fails and writes atomically.

scripts/pregame_refresh: pull failure -> exit 2 with NO board write;
stale cache after pull -> exit 2; happy path wires pull ->
rebuild_forecast_state -> board regen in ONE process (mocked at the
function seam, verified ordered with one shared snapshot).
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props.sources import personnel_feed as feed
from nfl_props.sources.personnel_feed import (
    PersonnelFeedError,
    parse_espn_payload,
    parse_nflverse_csv,
)


def _espn(abbr, pos, status, pid="1"):
    return {"id": pid, "status": status,
            "athlete": {"displayName": "P",
                        "position": {"abbreviation": pos},
                        "team": {"abbreviation": abbr}}}


def _espn_payload(*entries):
    return {"injuries": [{"id": "1", "displayName": "T",
                          "injuries": list(entries)}]}


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


class MapStatusTest(unittest.TestCase):
    def test_out_family(self):
        for raw in ("Out", "Inactive", "Injured Reserve", "IR", "PUP",
                    "Doubtful", "out - ankle"):
            self.assertEqual(feed._map_status(raw), "out", raw)

    def test_questionable_and_confirmed(self):
        self.assertEqual(feed._map_status("Questionable"), "questionable")
        self.assertEqual(feed._map_status("Probable"), "confirmed")
        self.assertEqual(feed._map_status("Active"), "confirmed")
        self.assertEqual(feed._map_status(""), "unknown")
        self.assertEqual(feed._map_status("Weird String"), "unknown")


class ParseEspnTest(unittest.TestCase):
    def test_qb_out_plus_skill_out(self):
        teams = parse_espn_payload(_espn_payload(
            _espn("BAL", "QB", "Out"), _espn("BAL", "WR", "Out"),
            _espn("BAL", "OT", "Out"), _espn("BAL", "TE", "Out"),
        ), now=NOW)
        bal = teams["BAL"]
        self.assertEqual(bal["qb_status"], "out")
        poss = [e["position"] for e in bal["key_out"]]
        self.assertIn("WR", poss)
        self.assertIn("T", poss)  # OT aliased to the debit classifier code
        self.assertIn("TE", poss)  # passes through; debits nothing
        self.assertIsNone(bal["backup_qb_id"])

    def test_questionable_qb_annotation_only(self):
        teams = parse_espn_payload(_espn_payload(
            _espn("ATL", "QB", "Questionable"),
            _espn("ATL", "WR", "Questionable"),  # NOT added to key_out
        ), now=NOW)
        atl = teams["ATL"]
        self.assertEqual(atl["qb_status"], "questionable")
        self.assertEqual(atl["key_out"], [])

    def test_missing_abbr_skipped_not_fatal(self):
        teams = parse_espn_payload(_espn_payload(
            {"id": "9", "status": "Out",
             "athlete": {"position": {"abbreviation": "WR"}}},
            _espn("KC", "RB", "Out"),
        ), now=NOW)
        self.assertEqual(set(teams), {"KC"})

    def test_degenerate_payloads_raise(self):
        for bad in ({}, {"injuries": []}, {"injuries": "x"}, [], None):
            with self.assertRaises(PersonnelFeedError):
                parse_espn_payload(bad, now=NOW)


class ParseNflverseTest(unittest.TestCase):
    CSV = ("season,season_type,game_type,team,week,gsis_id,position,"
           "full_name,first_name,last_name,report_primary_injury,"
           "report_secondary_injury,report_status,practice_primary_injury,"
           "practice_secondary_injury,practice_status\n"
           "2026,REG,REG,KC,5,00-1,QB,Starter S,Starter,S,Ankle,,Out,,,\n"
           "2026,REG,REG,KC,4,00-2,WR,Old W,Old,W,Knee,,Out,,,\n"
           "2026,REG,REG,DEN,5,00-3,WR,Wide W,Wide,W,Knee,,Out,,,\n"
           "2026,PRE,PRE,KC,5,00-4,RB,Pre P,Pre,P,Knee,,Out,,,\n")

    def test_latest_reg_week_only(self):
        teams = parse_nflverse_csv(self.CSV, now=NOW)
        self.assertEqual(teams["KC"]["qb_status"], "out")
        self.assertEqual(teams["KC"]["key_out"], [])  # week-4 WR excluded
        self.assertEqual(
            teams["DEN"]["key_out"],
            [{"position": "WR", "player_id": "00-3"}])
        self.assertNotIn("PRE", str(teams))

    def test_empty_csv_raises(self):
        with self.assertRaises(PersonnelFeedError):
            parse_nflverse_csv("season,team\n", now=NOW)


class FetchAndStoreTest(unittest.TestCase):
    def _patch_paths(self, tmp):
        proc = Path(tmp) / "processed"
        raw = Path(tmp) / "raw" / "personnel"
        p_feed = mock.patch.object(feed, "PERSONNEL_PATH",
                                   proc / "personnel.json")
        p_raw = mock.patch.object(feed, "PERSONNEL_RAW_DIR", raw)
        return p_feed, p_raw

    def test_espn_primary_wins_atomically(self):
        import tempfile
        payload = _espn_payload(_espn("KC", "WR", "Out"))
        with tempfile.TemporaryDirectory() as tmp:
            p_feed, p_raw = self._patch_paths(tmp)
            p_feed.start(); p_raw.start()
            try:
                with mock.patch.object(
                        feed, "fetch_espn_teams",
                        return_value=parse_espn_payload(payload, now=NOW)):
                    with mock.patch.object(feed, "fetch_nflverse_teams",
                                           side_effect=AssertionError(
                                               "fallback must not run")):
                        teams = feed.fetch_and_store(now=NOW,
                                                     archive_raw=True)
                self.assertEqual(set(teams), {"KC"})
                on_disk = json.loads(
                    (Path(tmp) / "processed" / "personnel.json"
                     ).read_text(encoding="utf-8"))
                self.assertEqual(on_disk["KC"]["source"],
                                 feed.SOURCE_ESPN)
                self.assertFalse(list(Path(tmp).glob(
                    "processed/personnel.json.*.tmp")))
                self.assertEqual(
                    len(list((Path(tmp) / "raw" / "personnel").glob(
                        "injuries_espn-site-api_*.json"))), 1)
            finally:
                p_feed.stop(); p_raw.stop()

    def test_fallback_when_espn_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p_feed, p_raw = self._patch_paths(tmp)
            p_feed.start(); p_raw.start()
            try:
                with mock.patch.object(
                        feed, "fetch_espn_teams",
                        side_effect=PersonnelFeedError("down")):
                    with mock.patch.object(feed, "fetch_nflverse_teams",
                                           return_value={"DEN": {
                                               "qb_status": "confirmed",
                                               "key_out": [],
                                               "backup_qb_id": None,
                                               "updated_at": NOW.isoformat(),
                                               "source": "x"}}):
                        teams = feed.fetch_and_store(now=NOW,
                                                     archive_raw=False)
                self.assertEqual(teams["DEN"]["source"],
                                 feed.SOURCE_NFLVERSE)
            finally:
                p_feed.stop(); p_raw.stop()

    def test_total_failure_raises_cache_untouched(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "processed"
            proc.mkdir(parents=True)
            cache = proc / "personnel.json"
            cache.write_text('{"KC": {"qb_status": "confirmed"}}',
                             encoding="utf-8")
            p_feed = mock.patch.object(feed, "PERSONNEL_PATH", cache)
            p_feed.start()
            try:
                with mock.patch.object(
                        feed, "fetch_espn_teams",
                        side_effect=PersonnelFeedError("down")):
                    with mock.patch.object(
                            feed, "fetch_nflverse_teams",
                            side_effect=PersonnelFeedError("down")):
                        with self.assertRaises(PersonnelFeedError):
                            feed.fetch_and_store(now=NOW)
                self.assertEqual(json.loads(cache.read_text(
                    encoding="utf-8")),
                    {"KC": {"qb_status": "confirmed"}})
            finally:
                p_feed.stop()

    def test_freshness_guard(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "personnel.json"
            p = mock.patch.object(feed, "PERSONNEL_PATH", cache)
            p.start()
            try:
                self.assertFalse(feed.personnel_is_fresh(now=NOW))
                cache.write_text(json.dumps(
                    {"KC": {"qb_status": "confirmed",
                            "key_out": [],
                            "updated_at": NOW.isoformat()}}),
                    encoding="utf-8")
                self.assertTrue(feed.personnel_is_fresh(now=NOW))
                stale = (NOW - timedelta(hours=25)).isoformat()
                cache.write_text(json.dumps(
                    {"KC": {"qb_status": "confirmed",
                            "key_out": [],
                            "updated_at": stale}}),
                    encoding="utf-8")
                self.assertFalse(feed.personnel_is_fresh(now=NOW))
            finally:
                p.stop()


class PregameRefreshTest(unittest.TestCase):
    def _script(self):
        import importlib.util
        path = (Path(__file__).resolve().parent.parent / "scripts"
                / "pregame_refresh.py")
        spec = importlib.util.spec_from_file_location("pregame_refresh",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_pull_failure_no_board_write(self):
        mod = self._script()
        with mock.patch.object(mod, "fetch_and_store",
                               side_effect=feed.PersonnelFeedError("down")
                               ) as pull:
            with mock.patch.object(mod, "rebuild_forecast_state",
                                   side_effect=AssertionError(
                                       "must not rebuild")):
                with mock.patch.object(mod, "build_board",
                                       side_effect=AssertionError(
                                           "must not build")):
                    rc = mod.main(["--now", "2026-10-11T12:00:00+00:00"])
        self.assertEqual(rc, 2)
        pull.assert_called_once()

    def test_stale_cache_no_board_write(self):
        mod = self._script()
        with mock.patch.object(mod, "fetch_and_store",
                               return_value={"KC": {}}):
            with mock.patch.object(mod, "personnel_is_fresh",
                                   return_value=False):
                with mock.patch.object(mod, "rebuild_forecast_state",
                                       side_effect=AssertionError(
                                           "must not rebuild")):
                    rc = mod.main(["--now",
                                   "2026-10-11T12:00:00+00:00"])
        self.assertEqual(rc, 2)

    def test_happy_path_one_process_order(self):
        mod = self._script()
        order = []
        state = {"personnel_available": True,
                 "personnel": {"KC": {"qb_status": "out"}}}
        fresh = mock.patch.object(mod, "personnel_is_fresh",
                                  return_value=True)
        pull = mock.patch.object(
            mod, "fetch_and_store",
            side_effect=lambda now=None: order.append("pull") or {
                "KC": {"qb_status": "out"}})
        rebuild_p = mock.patch.object(
            mod, "rebuild_forecast_state",
            side_effect=lambda: order.append("rebuild") or state)
        sched = mock.patch.object(
            mod, "current_day_games",
            return_value=[{"home_team": "KC", "away_team": "DEN"}])
        board = mock.patch.object(
            mod, "build_board",
            side_effect=lambda *a, **k: (
                order.append("board"),
                ([], {"games": 1, "available": 1,
                       "priced": 0}))[1])
        hist = mock.patch.object(mod, "latest_history",
                                   return_value=None)
        diff = mock.patch.object(mod, "detect_changes",
                                   return_value=[])
        rend = mock.patch.object(mod, "render_board",
                                   return_value="BOARD")
        exp = mock.patch.object(mod, "export_history",
                                  return_value="p")
        led = mock.patch.object(mod, "append_ledger")
        with fresh, pull, sched, board, hist, diff, rend, exp, led:
            with rebuild_p as rebuild:
                rc = mod.main(["--now", "2026-10-11T15:00:00+00:00",
                               "--no-export"])
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["pull", "rebuild", "board"])
        rebuild.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
