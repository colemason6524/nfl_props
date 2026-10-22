"""Season (cumulative) grade: PLAYABLE headline split from NO_VALUE.

The season report carries the same scoping rule as the weekly recap: the
headline record is PLAYABLE references only, NO_VALUE references are graded
and reported on their own line, and the old all-references number survives
only as an explicitly labelled `pooled` diagnostic.
"""
from __future__ import annotations

import unittest

from grade_forecast import _ref_bundle, _season_headline, render_report


def _row(family="moneyline", status="PLAYABLE", result="win", units=0.8):
    return {"family": family, "source": "bovada", "status": status,
            "edge_band": "3-6%", "result": result, "units": units}


def _stats(headline):
    return {
        "graded_at_utc": "2026-10-22T12:00:00+00:00",
        "snapshots": 37,
        "forecasts_considered": 263,
        "pending": 1,
        "winner": {"n": 100, "correct": 60, "accuracy": 0.6,
                   "brier": 0.2, "log_loss": 0.6},
        "projection": {"margin_mae": 10.0, "total_mae": 9.0},
        "headline": headline,
        "references": {},
    }


class RefBundleTest(unittest.TestCase):
    def test_record_units_roi(self):
        rows = [_row(result="win", units=0.8),
                _row(result="loss", units=-1.0),
                _row(result="push", units=0.0)]
        bundle = _ref_bundle(rows)
        self.assertEqual(bundle["record"]["wins"], 1)
        self.assertEqual(bundle["record"]["losses"], 1)
        self.assertEqual(bundle["record"]["pushes"], 1)
        self.assertEqual(bundle["record"]["pct"], 0.5)
        self.assertEqual(bundle["units"], -0.2)
        self.assertEqual(bundle["plays"], 3)
        self.assertEqual(bundle["roi"], -0.0667)

    def test_empty(self):
        bundle = _ref_bundle([])
        self.assertEqual(bundle["plays"], 0)
        self.assertEqual(bundle["units"], 0.0)
        self.assertIsNone(bundle["roi"])
        self.assertIsNone(bundle["record"]["pct"])


class SeasonHeadlineTest(unittest.TestCase):
    def test_no_value_rows_never_reach_the_headline(self):
        rows = [_row(status="PLAYABLE", result="win", units=0.9),
                _row(status="PLAYABLE", result="loss", units=-1.0),
                _row(status="NO_VALUE", result="win", units=0.5),
                _row(status="NO_VALUE", result="win", units=0.5)]
        hl = _season_headline(rows)
        self.assertEqual(hl["playable"]["record"]["wins"], 1)
        self.assertEqual(hl["playable"]["record"]["losses"], 1)
        self.assertEqual(hl["playable"]["plays"], 2)
        self.assertEqual(hl["playable"]["units"], -0.1)
        self.assertEqual(hl["no_value"]["plays"], 2)
        self.assertEqual(hl["no_value"]["units"], 1.0)

    def test_split_reconciles_with_pooled(self):
        rows = [_row(status="PLAYABLE", result="win", units=0.9),
                _row(status="NO_VALUE", result="loss", units=-1.0),
                _row(status="NO_VALUE", result="push", units=0.0)]
        hl = _season_headline(rows)
        self.assertEqual(hl["playable"]["plays"] + hl["no_value"]["plays"],
                         hl["pooled"]["plays"])
        self.assertAlmostEqual(
            hl["playable"]["units"] + hl["no_value"]["units"],
            hl["pooled"]["units"])

    def test_untagged_status_counts_as_not_playable(self):
        hl = _season_headline([_row(status=None)])
        self.assertEqual(hl["playable"]["plays"], 0)
        self.assertEqual(hl["no_value"]["plays"], 1)


class RenderReportTest(unittest.TestCase):
    def test_headline_leads_with_playable_and_labels_pooled(self):
        rows = [_row(status="PLAYABLE", result="win", units=0.9),
                _row(status="NO_VALUE", result="loss", units=-1.0)]
        text = render_report(_stats(_season_headline(rows)))
        self.assertIn("Season record (PLAYABLE references only", text)
        playable_line = next(line for line in text.splitlines()
                             if line.strip().startswith("PLAYABLE"))
        pooled_line = next(line for line in text.splitlines()
                           if line.strip().startswith("pooled"))
        self.assertIn("1-0", playable_line)
        self.assertIn("units=+0.90", playable_line)
        self.assertIn("units=-0.10", pooled_line)
        self.assertIn("diagnostic only", text)
        # the pooled number must never be the first record shown
        self.assertLess(text.index("PLAYABLE"), text.index("pooled"))

    def test_breakdown_section_is_labelled_pooled(self):
        text = render_report(_stats(_season_headline([_row()])))
        self.assertIn("pooled over all priced refs", text)


if __name__ == "__main__":
    unittest.main()
