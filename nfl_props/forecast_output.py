"""Terminal rendering + history export for the forecast-first board."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .config import FORECAST_HISTORY_DIR
from .forecast import GameForecast
from .version import (FORECAST_HISTORY_SCHEMA_VERSION, FORECAST_MODEL_VERSION,
                      PRODUCT_POLICY_VERSION, VALUE_POLICY_VERSION)

SECTIONS = (
    ("moneyline", "Moneylines"),
    ("spread", "Spreads"),
    ("total", "Totals"),
)


def ref_for(fc: GameForecast, family: str):
    for ref in fc.references:
        if ref.family == family:
            return ref
    return None


def _side_label(fc: GameForecast, family: str, ref) -> str:
    if family == "moneyline":
        return fc.home if ref.side == "home" else fc.away
    if family == "spread":
        team = fc.home if ref.side == "home" else fc.away
        return f"{team} {ref.line:+g}"
    direction = "Over" if ref.side == "over" else "Under"
    return f"{direction} {ref.line:g}"


def _projection(fc: GameForecast, family: str) -> str:
    if family == "total":
        return f"proj total {fc.projected_total:.1f}"
    return f"proj margin {fc.projected_margin:+.1f}"


def render_board(forecasts: List[GameForecast], summary: dict,
                 now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%a %b %d")
    lines = [f"NFL Forecast Board — {day} (ET)",
             f"{summary.get('games', 0)} games | "
             f"{summary.get('available', 0)} rated | "
             f"{summary.get('priced', 0)} priced"]
    if summary.get("unavailable_games"):
        lines.append("UNRATED (no forecast): " +
                     ", ".join(summary["unavailable_games"]))
    for family, label in SECTIONS:
        rows = [fc for fc in forecasts if fc.available and ref_for(fc, family)]
        lines.append(f"\n== {label} ({len(rows)}) ==")
        for fc in rows:
            ref = ref_for(fc, family)
            if ref.side is None or ref.line is None and family != "moneyline":
                pick = "no line"
            else:
                pick = _side_label(fc, family, ref)
            price = f"@ {ref.american:+d}" if ref.american is not None else "@ -"
            p_model = (f"model {ref.p_model:.0%}" if ref.p_model is not None
                       else "model -")
            p_market = (f"mkt {ref.p_market:.0%} [{ref.n_sources}]"
                        if ref.p_market is not None else "mkt -")
            edge = (f"edge {ref.edge:+.1%}" if ref.edge is not None
                    else "edge -")
            ev = f"EV {ref.ev:+.1%}" if ref.ev is not None else "EV -"
            lines.append(
                f"- {fc.away} @ {fc.home} | {pick} {price} | {p_model} vs "
                f"{p_market} | {edge} | {ev} | {ref.status} | "
                f"{_projection(fc, family)}")
    return "\n".join(lines)


def export_history(forecasts: List[GameForecast], summary: dict,
                   diagnostics: Optional[dict] = None,
                   changes: Optional[List[dict]] = None) -> Path:
    FORECAST_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "forecast_history_schema_version": FORECAST_HISTORY_SCHEMA_VERSION,
        "model_version": FORECAST_MODEL_VERSION,
        "product_policy_version": PRODUCT_POLICY_VERSION,
        "value_policy_version": VALUE_POLICY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "changes": changes or [],
        "forecasts": [fc.as_dict() for fc in forecasts],
        "source_diagnostics": diagnostics or {},
    }
    path = FORECAST_HISTORY_DIR / f"nfl_forecast_{stamp}.json"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def latest_history(path: Optional[Path] = None) -> Optional[List[dict]]:
    import json as _json
    directory = path or FORECAST_HISTORY_DIR
    files = sorted(directory.glob("nfl_forecast_*.json"))
    if not files:
        return None
    try:
        payload = _json.loads(files[-1].read_text(encoding="utf-8"))
        return payload.get("forecasts")
    except (ValueError, OSError):
        return None


def append_ledger(forecasts: List[GameForecast], summary: dict) -> Path:
    from .config import LEDGER_DIR
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = LEDGER_DIR / "forecast_ledger.jsonl"
    stamp = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as fh:
        for fc in forecasts:
            fh.write(json.dumps({
                "generated_at_utc": stamp,
                "forecast": fc.as_dict(),
            }) + "\n")
    return path
