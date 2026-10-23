"""House format (Poo Dolla Bets): one skeleton for every board and recap.

Header -> sections -> footer, identical on every project; only the row
content changes per sport. Spec: HOUSE_FORMAT_SPEC_DRAFT.md.

Every page is assembled by `render_page` so a project cannot drift in shape:

    [brand header]
    [day + slate note]
    [star PLAYS rows]
    [eyes LEANS rows]
    [chart footer: last slate + last 7d (+30d only when worth showing)]
    thank you Lawler

Tier copy is locked (Cole): PLAYS = best value in the proven range;
LEANS = smaller value, or confidence too high to trust at face value.
Anything below those tiers (NO_VALUE, vetoed, gated out) is internal
vocabulary and never reaches the page.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from .pricing import decimal_to_american

SPORT = "NFL"

BRAND = "POO DOLLA BETS"
HEADER_EMOJI = "\U0001f4b0"          # money bag
PLAYS_EMOJI = "\u2b50"               # star
LEANS_EMOJI = "\U0001f440"           # eyes
STATS_EMOJI = "\U0001f4ca"           # bar chart
SIGN_OFF = "thank you Lawler"
DAY_FORMAT = "%a %b %-d"             # "Wed Oct 21"

TIER_COPY = {
    "plays": "best value in the proven range",
    "leans": "smaller value, or confidence too high to trust at face value",
}

# The 30-day figure is clutter unless it is genuinely good (spec, Cole).
SHOW_30D_MIN_UNITS = 2.0
SHOW_30D_MIN_ROI = 0.05


def header(sport: str = SPORT, results: bool = False) -> str:
    suffix = f" \u2014 {sport}" + (" RESULTS" if results else "")
    return f"{HEADER_EMOJI} {BRAND}{suffix}"


def day_line(now: datetime) -> str:
    """Unambiguous day label: 'Wed Oct 21' (never a bare numeric date)."""
    return now.strftime(DAY_FORMAT)


def slate_line(now: datetime, note: str) -> str:
    return f"{day_line(now)} \u00b7 {note}"


def slate_note(games: Sequence[tuple], extra: Optional[str] = None,
               name_limit: int = 6) -> str:
    """'11 games' -- or '3 games: KC @ BUF, DAL @ PHI' on a small slate."""
    n = len(games)
    note = f"{n} game" + ("" if n == 1 else "s")
    labels = [f"{away} @ {home}" for away, home in games if away and home]
    if labels and n <= name_limit:
        note += ": " + ", ".join(labels)
    if extra:
        note += f" \u00b7 {extra}"
    return note


def price(decimal_odds: float) -> str:
    """Displayed price, derived from the exact decimal the ledger grades.

    Price shown == price graded: both come from one number, so the board can
    never advertise a price that grading does not settle on.
    """
    return f"({decimal_to_american(decimal_odds):+d})"


def pick_line(action: str, decimal_odds: float, p_model: float) -> str:
    """'<who/what> <market> <line> (<price>) \u00b7 <model %>'."""
    return f"{action} {price(decimal_odds)} \u00b7 {p_model:.0%}"


def record_text(bundle: Optional[dict]) -> str:
    """'4-2, +1.9u' -- pushes fold into the record, n/a when nothing graded."""
    if not bundle or not bundle.get("plays"):
        return "n/a"
    parts = f"{int(bundle.get('wins') or 0)}-{int(bundle.get('losses') or 0)}"
    if bundle.get("pushes"):
        parts += f"-{int(bundle['pushes'])}"
    return f"{parts}, {float(bundle.get('units') or 0.0):+.1f}u"


def show_30d(bundle: Optional[dict]) -> bool:
    """A 30-day line prints only when it is really good (spec, Cole)."""
    if not bundle or not bundle.get("plays"):
        return False
    units = float(bundle.get("units") or 0.0)
    roi = bundle.get("roi")
    if roi is None:
        roi = units / float(bundle["plays"]) if bundle.get("plays") else 0.0
    return units >= SHOW_30D_MIN_UNITS and float(roi) >= SHOW_30D_MIN_ROI


def footer(results: Optional[dict], last_label: str = "Last night") -> str:
    """'Last night: 6-3, +2.4u \u00b7 Last 7d: +8.1u' (+30d when good)."""
    results = results or {}
    parts = [f"{last_label}: {record_text(results.get('last_night'))}",
             f"Last 7d: {record_text(results.get('last_7d'))}"]
    if show_30d(results.get("last_30d")):
        parts.append(f"Last 30d: {record_text(results['last_30d'])}")
    return f"{STATS_EMOJI} " + " \u00b7 ".join(parts)


def render_card(rows: List[tuple], results: Optional[dict],
                day: Optional[str] = None, sport: str = SPORT,
                last_label: str = "Last night",
                empty_note: str = "No picks graded.") -> str:
    """Recap card: emoji-prefixed tier summaries instead of section headings.

    `rows` are (emoji, label, value) tuples, e.g. ("\u2b50", "Plays",
    "4-2, +1.9u"). Same header, footer and sign-off as every board.
    """
    lines = [header(sport, results=True)]
    if day:
        lines.append(day)
    if rows:
        lines.append("")
        lines += [f"{emoji} {label}: {value}" for emoji, label, value in rows]
    else:
        lines += ["", empty_note]
    lines += ["", footer(results, last_label=last_label), SIGN_OFF]
    return "\n".join(lines)


def render_page(plays: List[str], leans: List[str], results: Optional[dict],
                slate: Optional[str] = None, sport: str = SPORT,
                results_card: bool = False, last_label: str = "Last night",
                empty_note: str = "No plays today.",
                notes: Optional[List[str]] = None) -> str:
    """The skeleton. Sections with no rows are omitted, never printed empty."""
    lines = [header(sport, results=results_card)]
    if slate:
        lines.append(slate)
    if notes:
        lines += list(notes)
    if plays:
        lines += ["", f"{PLAYS_EMOJI} PLAYS", *plays]
    if leans:
        lines += ["", f"{LEANS_EMOJI} LEANS", *leans]
    if not plays and not leans:
        lines += ["", empty_note]
    lines += ["", footer(results, last_label=last_label), SIGN_OFF]
    return "\n".join(lines)
