"""Downstream market references + descriptive action labels (forecast-first).

A reference is attached AFTER the forecast exists. It can never change a pick,
a projection, or a probability. Sources are captured separately, de-vigged
independently, and combined into a **consensus** market probability for the
model's chosen side. Per-source prices are retained; the model's pick is fixed.

Action labels (descriptive, not advice; see docs/PRODUCT_CONTRACT.md):

    PLAYABLE  model EV at the reference price >= NFL_VALUE_PLAYABLE (default 0)
    NO_VALUE  a price exists but EV is below the playable threshold
    UNPRICED  no usable reference price captured

Expected value is flat-1-unit and push-aware (a push returns the stake).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

from .pricing import (decimal_to_american, decimal_to_implied,
                      devig_proportional, expected_value)

PLAYABLE = "PLAYABLE"
NO_VALUE = "NO_VALUE"
UNPRICED = "UNPRICED"

BOVADA = "bovada"
POLYMARKET = "polymarket"

PLAYABLE_EV = float(os.environ.get("NFL_VALUE_PLAYABLE", "0.0"))


def status_for(ev: Optional[float]) -> str:
    if ev is None:
        return UNPRICED
    return PLAYABLE if ev >= PLAYABLE_EV else NO_VALUE


@dataclass
class ForecastReference:
    family: str                 # moneyline | spread | total
    source: str                 # primary displayed source (bovada/polymarket/none)
    sources: List[str]          # all sources present in the consensus
    side: Optional[str]         # home | away | over | under (None if no line)
    line: Optional[float]
    american: Optional[int]
    decimal: Optional[float]
    p_model: Optional[float]
    p_market: Optional[float]   # consensus de-vigged market probability
    edge: Optional[float]
    ev: Optional[float]
    status: str = UNPRICED
    n_sources: int = 0
    captured_at: Optional[str] = None
    note: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


def _source_prob(dec_side: float, dec_opp: Optional[float]) -> float:
    if dec_opp:
        p_side, _ = devig_proportional(dec_side, dec_opp)
        return p_side
    return decimal_to_implied(dec_side)


def build_reference(
        family: str, side: Optional[str], line: Optional[float],
        p_model: Optional[float], p_push: float,
        prices: List[Tuple[str, Optional[float], Optional[float]]],
        captured_at: Optional[str] = None,
        note: Optional[str] = None) -> ForecastReference:
    """Build one family reference from all available source prices.

    `prices` entries are (source_name, side_decimal, opp_decimal). Entries with
    no usable side price are ignored. The pick (`side`) and `p_model` are
    supplied by the caller and are never derived from the market.
    """
    entries = []
    for name, dec_side, dec_opp in prices:
        if dec_side is None or dec_side <= 1.0:
            continue
        entries.append((name, float(dec_side), _source_prob(dec_side, dec_opp)))

    if not entries:
        return ForecastReference(
            family=family, source="none", sources=[], side=side, line=line,
            american=None, decimal=None,
            p_model=round(p_model, 4) if p_model is not None else None,
            p_market=None, edge=None, ev=None, status=UNPRICED,
            n_sources=0, captured_at=captured_at,
            note=note or ("no reference price captured" if side is not None
                          else "no posted line; projection only"))

    primary = next((e for e in entries if e[0] == BOVADA), entries[0])
    consensus = sum(e[2] for e in entries) / len(entries)
    dec = primary[1]
    p_model_c = float(p_model) if p_model is not None else None
    ev = (expected_value(p_model_c, dec, p_push)
          if p_model_c is not None else None)
    edge = (p_model_c - consensus) if p_model_c is not None else None
    return ForecastReference(
        family=family, source=primary[0], sources=[e[0] for e in entries],
        side=side, line=line,
        american=decimal_to_american(dec), decimal=round(dec, 4),
        p_model=round(p_model_c, 4) if p_model_c is not None else None,
        p_market=round(consensus, 4),
        edge=round(edge, 4) if edge is not None else None,
        ev=round(ev, 4) if ev is not None else None,
        status=status_for(ev), n_sources=len(entries),
        captured_at=captured_at)
