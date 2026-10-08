"""Conservative team-side injury debit (NFL, Phase C).

Backup-QB substitution (Phase B) already re-prices the QB slot when the
starter is out, so this module only debits the *residual* backup-vs-starter
gap plus confirmed non-QB outs. Everything is conservative by construction:

- raw position values are ~0.5x naive, then scaled again by
  CONSERVATIVE_FACTOR (0.5);
- group caps bound OL / DEF accumulation;
- the team total is clamped to TEAM_DEBIT_FLOOR (-4.0 pts).

`team_injury_debit` is fail-open: missing/neutral context, unknown
positions, and questionable designations all yield (0.0, []). Questionable
is annotation-only and NEVER debits. QB entries inside `key_out` are
ignored (the QB is covered by the qb_status residual only, no
double-count).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .sources.personnel import normalize_key_out

CONSERVATIVE_FACTOR = 0.5

# Manager-approved raw values (already ~0.5x naive).
QB_RESIDUAL_RAW = -2.0
QB_RESIDUAL_CAP_RAW = -3.0
WR_RAW = -0.8
RB_RAW = -0.6
OL_RAW = -0.4
OL_GROUP_CAP_RAW = -1.0
DEF_RAW = -0.2
DEF_GROUP_CAP_RAW = -0.6

# Hard cap on the total factored team debit (points).
TEAM_DEBIT_FLOOR = -4.0

# Case-insensitive prefix conventions for key_out position strings.
_OL_PREFIXES = ("OL", "LT", "RT", "LG", "RG", "G")
_OL_EXACT = ("C", "T")
_DEF_PREFIXES = ("DEF", "DL", "LB", "CB", "DB", "DE", "DT",
                 "FS", "SS", "S", "NT", "EDGE")


def _classify(position) -> Optional[Tuple[str, float]]:
    """Map a key_out position string to (group, raw debit), or None."""
    if not isinstance(position, str):
        return None
    pos = position.strip().upper()
    if not pos:
        return None
    if pos == "QB" or pos.startswith("QB"):
        # Covered by the qb_status residual only; never double-count.
        return None
    if pos.startswith("WR"):
        return ("WR", WR_RAW)
    if pos.startswith("RB"):
        return ("RB", RB_RAW)
    if pos in _OL_EXACT or pos.startswith(_OL_PREFIXES):
        return ("OL", OL_RAW)
    if pos.startswith(_DEF_PREFIXES):
        return ("DEF", DEF_RAW)
    return None


def team_injury_debit(personnel_ctx,
                      priced_qb_substituted: bool = False
                      ) -> Tuple[float, List[dict]]:
    """Factored point debit for one team's personnel context.

    Returns (debit_pts, attribution) with debit_pts <= 0.0 and attribution
    a list of {position, raw, applied} dicts. The QB residual (-2.0 raw,
    factored like everything else) applies ONLY when qb_status == "out"
    AND the priced QB was actually substituted (Phase B); otherwise the
    projection still prices the starter and there is no residual gap.
    """
    if not isinstance(personnel_ctx, dict):
        return 0.0, []
    status_raw = personnel_ctx.get("qb_status")
    status = (status_raw.strip().lower() if isinstance(status_raw, str)
              else "")
    if status == "questionable":
        # Annotation-only: questionable NEVER debits, including key_out.
        return 0.0, []
    entries = normalize_key_out(personnel_ctx.get("key_out"))
    groups: dict = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hit = _classify(entry.get("position"))
        if hit is None:
            continue
        groups.setdefault(hit[0], []).append((entry.get("position"), hit[1]))

    attribution: List[dict] = []
    total = 0.0

    if status == "out" and priced_qb_substituted:
        raw = max(QB_RESIDUAL_RAW, QB_RESIDUAL_CAP_RAW)
        applied = raw * CONSERVATIVE_FACTOR
        attribution.append({"position": "QB", "raw": raw,
                            "applied": applied})
        total += applied

    caps = {"OL": OL_GROUP_CAP_RAW, "DEF": DEF_GROUP_CAP_RAW}
    for group, items in groups.items():
        raw_sum = sum(raw for _, raw in items)
        cap = caps.get(group)
        if cap is not None:
            raw_sum = max(raw_sum, cap)
        applied_sum = raw_sum * CONSERVATIVE_FACTOR
        # Cap binds at group level; split the factored share evenly so
        # attribution stays proportional within the group.
        share = applied_sum / len(items)
        for position, raw in items:
            attribution.append({"position": position, "raw": raw,
                                "applied": share})
        total += applied_sum

    if not attribution:
        return 0.0, []
    return max(total, TEAM_DEBIT_FLOOR), attribution
