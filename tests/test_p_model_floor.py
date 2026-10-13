"""Selection audit 2026-10-13: spread p_model floor tests."""
from nfl_props.tiers import Candidate, assign_tier
from nfl_props.references import build_reference, PLAYABLE, NO_VALUE


def _cand(market="SPREAD", p_model=0.55):
    return Candidate(
        event_id="t", away="A", home="H", market=market, side="H", line=-3.0,
        american=-110, decimal=1.91, p_model=p_model, p_model_cal=p_model,
        p_market=0.50, p_push=0.0, ev=0.05, edge=0.05,
        mu_home=24.0, mu_away=20.0, games_current_min=5,
        start_time_utc=None, flags=[],
    )


def test_spread_below_floor_goes_watch():
    c = _cand("SPREAD", 0.55)
    assert assign_tier(c) == "Watch"
    assert "P_MODEL_BELOW_FLOOR" in c.flags


def test_spread_at_floor_stays_core():
    c = _cand("SPREAD", 0.60)
    assert assign_tier(c) == "Core"


def test_spread_above_floor_stays_core():
    c = _cand("SPREAD", 0.65)
    assert assign_tier(c) == "Core"


def test_moneyline_below_floor_unaffected():
    c = _cand("MONEYLINE", 0.55)
    assert assign_tier(c) == "Core"
    assert "P_MODEL_BELOW_FLOOR" not in c.flags


def test_total_below_floor_unaffected():
    c = _cand("GAME_TOTAL", 0.55)
    c.side = "OVER"
    assert assign_tier(c) == "Core"


def test_reference_spread_below_floor_no_value():
    r = build_reference(family="spread", side="home", line=-3.0,
                        p_model=0.55, p_push=0.0,
                        prices=[("bovada", 1.91, 1.91)])
    assert r.status == NO_VALUE
    assert r.note and "P_MODEL_BELOW_FLOOR" in r.note


def test_reference_spread_at_floor_playable():
    r = build_reference(family="spread", side="home", line=-3.0,
                        p_model=0.65, p_push=0.0,
                        prices=[("bovada", 1.91, 1.91)])
    assert r.status == PLAYABLE


def test_reference_moneyline_below_floor_unaffected():
    r = build_reference(family="moneyline", side="home", line=None,
                        p_model=0.55, p_push=0.0,
                        prices=[("bovada", 1.91, 1.91)])
    assert r.status == PLAYABLE
