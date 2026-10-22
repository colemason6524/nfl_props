"""The legacy v1/v2 grader must refuse to run so it cannot pose as the record.

`outputs/history/` stopped receiving artifacts on 2026-09-12; `grade.py` grades
that frozen set only. Its `main()` is retired and exits non-zero, while the
grading helpers stay importable so the frozen artifacts remain reproducible.
See docs/INTEGRITY_AUDIT_2026-10-22.md.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import grade  # noqa: E402


class LegacyGradeRetiredTest(unittest.TestCase):
    def test_main_refuses_with_nonzero_exit(self):
        rc = grade.main()
        self.assertEqual(rc, 2)

    def test_refusal_names_the_live_grader(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            grade.main()
        msg = buf.getvalue()
        self.assertIn("RETIRED", msg)
        self.assertIn("grade_forecast.py", msg)
        self.assertIn("INTEGRITY_AUDIT_2026-10-22", msg)

    def test_helpers_stay_importable(self):
        # Reproducibility: the grading functions must survive the retirement.
        for name in ("load_plays", "grade_play", "match_game", "_legacy_main"):
            self.assertTrue(hasattr(grade, name), name)


if __name__ == "__main__":
    unittest.main()
