"""Core / Lean / Watch gates for live team-market plays.

Fair probability = EPA points projection + empirical score distributions
(no market inside fair). A play clears when EV at the offered Bovada price
sits in [EV_MIN, EV_MAX] — oversized EV is forced to Watch (stale/outlier
filter validated by the backtest: the 15%+ band was heavily negative).
Core additionally requires both teams to have a real current-season sample;
early-season plays cap at Lean because ratings lean on the prior-season
prior.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

EV_MIN = float(os.environ.get("NFL_EV_MIN", "0.02"))
EV_MAX = float(os.environ.get("NFL_EV_MAX", "0.08"))
EDGE_MIN = float(os.environ.get("NFL_EDGE_MIN", "0.02"))
CORE_EV_MIN = float(os.environ.get("NFL_CORE_EV_MIN", "0.02"))
MIN_TEAM_GAMES = int(os.environ.get("NFL_MIN_TEAM_GAMES", "3"))


@dataclass
class Candidate:
    event_id: str
    away: str
    home: str
    market: str            # MONEYLINE | SPREAD | TEAM_TOTAL | GAME_TOTAL
    side: str              # team abbr, or OVER/UNDER descriptor
    line: Optional[float]
    american: int
    decimal: float
    p_model: float         # raw model probability (selection semantics)
    p_model_cal: float     # tune-calibrated probability (research field)
    p_market: float        # de-vigged book probability
    p_push: float
    ev: float
    edge: float
    mu_home: float
    mu_away: float
    games_current_min: int
    start_time_utc: Optional[str]
    flags: List[str] = field(default_factory=list)
    tier: str = "Watch"
    source: str = "bovada"


def assign_tier(c: Candidate) -> str:
    if "UNMATCHED_TEAM" in c.flags:
        return "Watch"
    if not (EV_MIN <= c.ev <= EV_MAX):
        return "Watch"
    if c.edge < EDGE_MIN:
        return "Watch"
    if c.games_current_min < MIN_TEAM_GAMES:
        return "Lean"
    if c.ev >= CORE_EV_MIN:
        return "Core"
    return "Lean"
