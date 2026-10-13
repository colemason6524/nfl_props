"""Tests for qb_rookie_road feature (margin model)."""
import pandas as pd
import numpy as np


def test_rookie_flag_helper():
    from nfl_props.ratings.v2 import _qb_rookie_flag
    # rookie: 0 < db < 150
    assert _qb_rookie_flag({"last_qb_id": "q1"}, {"q1": {"dropbacks": 100}}) == 1.0
    # veteran
    assert _qb_rookie_flag({"last_qb_id": "q1"}, {"q1": {"dropbacks": 500}}) == 0.0
    # exactly at boundary
    assert _qb_rookie_flag({"last_qb_id": "q1"}, {"q1": {"dropbacks": 150}}) == 0.0
    # unknown QB -> 0.0 (not rookie; that's qb_uncertain)
    assert _qb_rookie_flag({"last_qb_id": "qx"}, {"q1": {"dropbacks": 10}}) == 0.0
    assert _qb_rookie_flag({}, {}) == 0.0
    # zero dropbacks -> 0.0
    assert _qb_rookie_flag({"last_qb_id": "q1"}, {"q1": {"dropbacks": 0}}) == 0.0


def test_paired_has_rookie_road():
    from nfl_props.sources.nflverse import load_processed_v2
    from nfl_props.ratings.v2 import replay_v2
    from nfl_props.forecasting import paired_features
    games, team_games, situational, qb_games = load_processed_v2()
    features, _ = replay_v2(games, team_games, situational, qb_games)
    paired = paired_features(features, games)
    assert "qb_rookie_road" in paired.columns
    assert "qb_rookie_h" in paired.columns
    assert "qb_rookie_a" in paired.columns
    # road flag == away rookie flag
    assert (paired["qb_rookie_road"] == paired["qb_rookie_a"]).all()
    assert set(paired["qb_rookie_road"].dropna().unique()) <= {0.0, 1.0}


def test_margin_feature_set_includes_rookie_road():
    from nfl_props.forecasting import FEATURE_SETS
    assert "qb_rookie_road" in FEATURE_SETS["full"]["margin"]
    assert "qb_rookie_road" not in FEATURE_SETS["full"]["total"]
    assert "qb_rookie_road" not in FEATURE_SETS["full"]["winner"]


def test_live_game_features_rookie_road():
    from nfl_props.forecasting import game_features
    state = {
        "teams": {
            "HOME": {"off": 0.1, "def": 0.0, "off_pass": 0.05, "def_pass": 0.0,
                     "off_rush": 0.0, "def_rush": 0.0, "pace": 65.0,
                     "last_qb_id": "vet"},
            "AWAY": {"off": 0.0, "def": 0.1, "off_pass": 0.0, "def_pass": 0.05,
                     "off_rush": 0.0, "def_rush": 0.0, "pace": 65.0,
                     "last_qb_id": "rook"},
        },
        "qbs": {
            "vet": {"dropbacks": 2000, "epa_c": 0.05},
            "rook": {"dropbacks": 80, "epa_c": -0.05},
        },
        "league": {"pace": 65.0},
    }
    game = {"home_team": "HOME", "away_team": "AWAY"}
    feats = game_features(state, game)
    assert feats is not None
    assert feats["qb_rookie_road"] == 1.0
    # flip: veteran away
    state["teams"]["AWAY"]["last_qb_id"] = "vet"
    feats = game_features(state, game)
    assert feats["qb_rookie_road"] == 0.0
