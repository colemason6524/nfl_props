"""Tests for the pre-build injury pull in run_forecast_board.py (NFL)."""

# The Thursday (TNF) board rebuilds state without the Sunday pregame
# refresh, so the board itself must pull injuries first -- atomically, with
# the pregame_refresh fail-open contract:
# - --rebuild-state pulls injuries BEFORE rebuild_forecast_state (ordering).
# - Pull failure (PersonnelFeedError or anything else) -> return 2, NO
#   rebuild, NO board write.
# - Cache still stale after pull -> return 2.
# - --no-injury-pull skips the pull entirely (offline/test escape hatch).
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_forecast_board as board_mod
from nfl_props.sources.personnel_feed import PersonnelFeedError


def _seams(pull=None, fresh=True, order=None):
    state = {"teams": {}, "personnel": {"KC": {}}, "personnel_available": True}
    rebuild_side = (lambda: order.append("rebuild")) if order is not None else None
    pull_side = pull
    if order is not None and callable(pull):
        def pull_side(now=None):
            order.append("pull")
            return pull(now=now)
    return [
        mock.patch.object(board_mod, "fetch_and_store", side_effect=pull_side),
        mock.patch.object(board_mod, "personnel_is_fresh", return_value=fresh),
        mock.patch.object(board_mod, "rebuild_forecast_state", side_effect=rebuild_side),
        mock.patch.object(board_mod, "load_forecast_state", return_value=state),
        mock.patch.object(board_mod, "current_day_games", return_value=[]),
        mock.patch.object(board_mod, "build_board", return_value=([], {"games": 0, "available": 0, "priced": 0})),
        mock.patch.object(board_mod, "latest_history", return_value=None),
        mock.patch.object(board_mod, "detect_changes", return_value=[]),
        mock.patch.object(board_mod, "render_board", return_value=""),
        mock.patch.object(board_mod, "export_history", return_value="/tmp/x.json"),
        mock.patch.object(board_mod, "append_ledger"),
        mock.patch.object(board_mod.config, "ensure_dirs"),
    ]


def _run(argv, **kw):
    pull = kw.get("pull")
    if pull is not None and not callable(pull) and not isinstance(pull, BaseException):
        kw["pull"] = (lambda _v=pull: (lambda now=None: _v))()
    seams = _seams(**kw)
    entered = [s.__enter__() for s in seams]
    try:
        with mock.patch.object(sys, "argv", ["run_forecast_board.py"] + argv):
            rc = board_mod.main()
    finally:
        for s in seams:
            s.__exit__(None, None, None)
    return rc, entered


class BoardInjuryPullTest(unittest.TestCase):
    def test_pull_before_rebuild_ordering(self):
        order = []
        rc, _ = _run(["--rebuild-state", "--no-export"], order=order,
                     pull=lambda now=None: {"KC": {}})
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["pull", "rebuild"])

    def test_pull_failure_returns_2_no_rebuild(self):
        for exc in (PersonnelFeedError("boom"), RuntimeError("boom")):
            rc, mocks = _run(["--rebuild-state", "--no-export"], pull=exc)
            self.assertEqual(rc, 2, repr(exc))
            mocks[0].assert_called_once()
            mocks[2].assert_not_called()

    def test_stale_after_pull_returns_2(self):
        rc, mocks = _run(["--rebuild-state", "--no-export"],
                         pull={"KC": {}}, fresh=False)
        self.assertEqual(rc, 2)
        mocks[2].assert_not_called()

    def test_no_injury_pull_flag_skips_pull(self):
        rc, mocks = _run(["--rebuild-state", "--no-export", "--no-injury-pull"],
                         pull={"KC": {}})
        self.assertEqual(rc, 0)
        mocks[0].assert_not_called()
        mocks[2].assert_called_once()

    def test_no_rebuild_skips_pull(self):
        rc, mocks = _run(["--no-export"], pull={"KC": {}})
        self.assertEqual(rc, 0)
        mocks[0].assert_not_called()
        mocks[2].assert_not_called()


if __name__ == "__main__":
    unittest.main()
