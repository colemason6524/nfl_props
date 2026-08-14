"""Odds math: implied probabilities, devig, EV, American/decimal conversion."""
from typing import Optional, Tuple


def decimal_to_implied(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def american_to_decimal(american: float) -> float:
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    return int(round(-100.0 / (decimal_odds - 1.0)))


def devig_proportional(dec_a: float, dec_b: float) -> Tuple[float, float]:
    """Two-sided proportional devig -> fair probabilities summing to 1."""
    ia, ib = decimal_to_implied(dec_a), decimal_to_implied(dec_b)
    total = ia + ib
    return ia / total, ib / total


def expected_value(p_win: float, decimal_odds: float,
                   p_push: float = 0.0) -> float:
    """Flat 1-unit EV at the offered price. Pushes return the stake."""
    p_lose = max(0.0, 1.0 - p_win - p_push)
    return p_win * (decimal_odds - 1.0) - p_lose


def fair_decimal(p_win: float) -> Optional[float]:
    if p_win <= 0.0 or p_win >= 1.0:
        return None
    return 1.0 / p_win
