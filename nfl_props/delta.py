"""Smart delta alerts — Phase 1 (NFL test case).

Compares a freshly recomputed forecast (latest injury data) against the
last printed board. Fires tiered alerts when late news materially changes
pick value. Silent when nothing material changed.

Tier 1 (delta alert): edge shift > 3%, play/no-play flip, or key player
news. Short format, original board remains valid.

Tier 2 (re-board): projected margin moves > 5 points. The game prediction
is fundamentally different; repost the game's full prediction block.

Design doc: docs/delta-alerts-spec.md
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Thresholds (from spec, Cole-approved 2026-10-13)
# ---------------------------------------------------------------------------

#: Tier 1 trigger: absolute edge shift on any reference.
TIER1_EDGE_DELTA = 0.03
#: Tier 2 trigger: absolute projected-margin move -> full re-board.
TIER2_MARGIN_DELTA = 5.0
#: Team-debit move marking "key player" impact for the transparency rule.
KEY_DEBIT_MOVE = 1.0

#: Board states older than this are too stale to diff against.
BOARD_MAX_AGE_HOURS = 12.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ReferenceDelta:
    family: str                 # spread | total | moneyline
    side: Optional[str]
    line_old: Optional[float]
    line_new: Optional[float]
    edge_old: Optional[float]
    edge_new: Optional[float]
    status_old: str
    status_new: str

    @property
    def edge_delta(self) -> Optional[float]:
        if self.edge_old is None or self.edge_new is None:
            return None
        return self.edge_new - self.edge_old

    #: Statuses that carry a real value judgment (vs UNPRICED = no data).
    _PRICED = frozenset({"PLAYABLE", "NO_VALUE"})

    @property
    def flipped(self) -> bool:
        if self.status_old == self.status_new:
            return False
        # UNPRICED transitions mean market data (un)availability, not a
        # value change — only PLAYABLE <-> NO_VALUE flips are actionable.
        return (self.status_old in self._PRICED
                and self.status_new in self._PRICED)


@dataclass
class GameDelta:
    forecast_id: str
    away: str
    home: str
    margin_old: Optional[float]
    margin_new: Optional[float]
    total_old: Optional[float]
    total_new: Optional[float]
    debit_old_home: float = 0.0
    debit_new_home: float = 0.0
    debit_old_away: float = 0.0
    debit_new_away: float = 0.0
    injury_changes: List[str] = field(default_factory=list)
    ref_deltas: List[ReferenceDelta] = field(default_factory=list)
    tier: int = 0               # 0 = none, 1 = delta, 2 = re-board
    reasons: List[str] = field(default_factory=list)

    @property
    def matchup(self) -> str:
        return f"{self.away} @ {self.home}"

    @property
    def margin_delta(self) -> Optional[float]:
        if self.margin_old is None or self.margin_new is None:
            return None
        return self.margin_new - self.margin_old


# ---------------------------------------------------------------------------
# Board state store
# ---------------------------------------------------------------------------

def load_board_state(max_age_hours: float = BOARD_MAX_AGE_HOURS,
                     history_dir: Optional[Path] = None
                     ) -> Optional[Tuple[List[dict], datetime]]:
    """Load the last printed board with its timestamp.

    Returns (forecasts, generated_at) or None when no board exists or the
    newest board is older than max_age_hours (too stale to diff against).
    """
    from .forecast_output import FORECAST_HISTORY_DIR
    directory = history_dir or FORECAST_HISTORY_DIR
    files = sorted(directory.glob("nfl_forecast_*.json"))
    if not files:
        return None
    try:
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    forecasts = payload.get("forecasts")
    if not forecasts:
        return None
    gen_raw = payload.get("generated_at_utc")
    try:
        generated_at = datetime.fromisoformat(gen_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    age_hours = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None
    return forecasts, generated_at


# ---------------------------------------------------------------------------
# Recompute
# ---------------------------------------------------------------------------

def game_dict_from_forecast(fc: dict) -> dict:
    """Reconstruct the minimal game dict build_game_forecast needs."""
    return {
        "home_team": fc.get("home"),
        "away_team": fc.get("away"),
        "kickoff_utc": fc.get("kickoff_utc"),
        "season": fc.get("season"),
        "week": fc.get("week"),
        "game_type": fc.get("game_type") or "REG",
        "neutral_site": bool(fc.get("neutral_site")),
        "venue": fc.get("venue"),
        "game_id": fc.get("game_id"),
    }


def refresh_state_personnel(state: dict, now: Optional[datetime] = None):
    """Pull fresh injury data and splice it into the forecast state.

    Returns the fresh personnel dict. Raises PersonnelFeedError on pull
    failure — callers treat that as "no alert" (fail-open, never alert on
    stale data).
    """
    from .sources.personnel_feed import fetch_and_store
    fresh = fetch_and_store(now=now)
    state["personnel"] = fresh
    state["personnel_available"] = bool(fresh)
    return fresh


def recompute_game(fc_dict: dict, state: dict,
                   bovada_game=None,
                   poly: Optional[dict] = None,
                   games_df=None):
    """Rebuild one game's forecast with the state's (fresh) personnel."""
    from .forecast import build_game_forecast
    game = game_dict_from_forecast(fc_dict)
    return build_game_forecast(game, state, bovada_game=bovada_game,
                               poly=poly, games_df=games_df)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _ref_key(ref: dict) -> tuple:
    return (ref.get("family"), ref.get("side"))


def diff_references(old_refs: List[dict],
                    new_refs: List[dict]) -> List[ReferenceDelta]:
    """Diff market references by (family, side)."""
    old_map = {_ref_key(r): r for r in old_refs}
    new_map = {_ref_key(r): r for r in new_refs}
    deltas: List[ReferenceDelta] = []
    for key in old_map.keys() | new_map.keys():
        o, n = old_map.get(key), new_map.get(key)
        if o is None or n is None:
            continue  # added/removed refs are not injury deltas
        deltas.append(ReferenceDelta(
            family=key[0], side=key[1],
            line_old=o.get("line"), line_new=n.get("line"),
            edge_old=o.get("edge"), edge_new=n.get("edge"),
            status_old=o.get("status", "UNPRICED"),
            status_new=n.get("status", "UNPRICED"),
        ))
    return deltas


def _trace_players(trace: List[dict]) -> Dict[tuple, dict]:
    """Index injury trace entries by (team, position-label)."""
    out: Dict[tuple, dict] = {}
    for entry in trace or []:
        team = entry.get("team")
        for attr in entry.get("attribution") or []:
            pos = attr.get("position")
            if team and pos:
                out[(team, str(pos).upper())] = attr
    return out


def diff_injuries(old_fc, new_fc) -> List[str]:
    """Human-readable list of confirmed injury-status changes.

    Compares QB pricing/substitution plus trace attributions. Only
    reports entries present in the NEW trace but absent (or different)
    in the OLD trace, and vice versa — i.e. final confirmed status,
    not intermediate questionable->doubtful movement.
    """
    changes: List[str] = []
    old = old_fc if isinstance(old_fc, dict) else {}
    # QB changes (both directions matter equally)
    for slot, team_key in (("home", "home"), ("away", "away")):
        o_qb = old.get(f"priced_qb_{slot}")
        n_qb = getattr(new_fc, f"priced_qb_{slot}", None)
        o_sub = bool(old.get(f"qb_substituted_{slot}"))
        n_sub = bool(getattr(new_fc, f"qb_substituted_{slot}", False))
        team = getattr(new_fc, team_key, "?")
        if o_sub and not n_sub and o_qb:
            changes.append(f"{o_qb} (QB, {team}) ACTIVE — was OUT")
        elif n_sub and not o_sub and n_qb:
            changes.append(f"{n_qb} (QB, {team}) OUT — late inactive")
        elif o_qb and n_qb and o_qb != n_qb:
            changes.append(f"{team} QB change: {o_qb} -> {n_qb}")
    # Non-QB trace changes
    old_players = _trace_players(old.get("injury_trace"))
    new_players = _trace_players(getattr(new_fc, "injury_trace", None) or [])
    for key in sorted(set(new_players) - set(old_players)):
        team, pos = key
        changes.append(f"{pos} ({team}) OUT — newly inactive")
    for key in sorted(set(old_players) - set(new_players)):
        team, pos = key
        changes.append(f"{pos} ({team}) ACTIVE — was OUT")
    return changes


def diff_game(old: dict, new) -> GameDelta:
    """Full per-game diff between stored board forecast and recompute."""
    delta = GameDelta(
        forecast_id=old.get("forecast_id", ""),
        away=old.get("away", "?"),
        home=old.get("home", "?"),
        margin_old=old.get("projected_margin"),
        margin_new=getattr(new, "projected_margin", None),
        total_old=old.get("projected_total"),
        total_new=getattr(new, "projected_total", None),
        debit_old_home=old.get("injury_debit_pts_home") or 0.0,
        debit_new_home=getattr(new, "injury_debit_pts_home", 0.0) or 0.0,
        debit_old_away=old.get("injury_debit_pts_away") or 0.0,
        debit_new_away=getattr(new, "injury_debit_pts_away", 0.0) or 0.0,
        injury_changes=diff_injuries(old, new),
    )
    new_refs = []
    for r in getattr(new, "references", []) or []:
        new_refs.append(r.as_dict() if hasattr(r, "as_dict") else r)
    delta.ref_deltas = diff_references(old.get("references") or [], new_refs)
    delta.tier, delta.reasons = evaluate_tier(delta)
    return delta


# ---------------------------------------------------------------------------
# Trigger evaluator
# ---------------------------------------------------------------------------

def _is_key_player_news(delta: GameDelta) -> bool:
    """Transparency rule: star/high-impact news alerts regardless of delta.

    QB in/out is always key. Other positions count as key when the team
    debit moves by >= KEY_DEBIT_MOVE (meaningful on-field impact).
    """
    for change in delta.injury_changes:
        if "(QB" in change:
            return True
    home_move = abs(delta.debit_new_home - delta.debit_old_home)
    away_move = abs(delta.debit_new_away - delta.debit_old_away)
    return max(home_move, away_move) >= KEY_DEBIT_MOVE


def evaluate_tier(delta: GameDelta) -> Tuple[int, List[str]]:
    """Apply the trigger rules. Returns (tier, reasons)."""
    reasons: List[str] = []

    # Tier 2 first: margin move > 5 -> full re-board.
    md = delta.margin_delta
    if md is not None and abs(md) >= TIER2_MARGIN_DELTA:
        reasons.append(f"projected margin {delta.margin_old:+.1f} -> "
                       f"{delta.margin_new:+.1f} (Tier 2 re-board)")
        return 2, reasons

    # Tier 1: edge shift, flip, or key player news.
    for rd in delta.ref_deltas:
        ed = rd.edge_delta
        if ed is not None and abs(ed) >= TIER1_EDGE_DELTA:
            reasons.append(
                f"{rd.family} {rd.side or ''}: edge "
                f"{(rd.edge_old or 0):+.1%} -> {(rd.edge_new or 0):+.1%}")
        if rd.flipped:
            reasons.append(
                f"{rd.family} {rd.side or ''}: "
                f"{rd.status_old} -> {rd.status_new}")
    if _is_key_player_news(delta):
        for change in delta.injury_changes:
            reasons.append(f"key player: {change}")

    # Dedupe while preserving order.
    seen, uniq = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    tier = 1 if uniq else 0
    return tier, uniq


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

def _fmt_edge(edge: Optional[float]) -> str:
    return f"{edge:+.1%}" if edge is not None else "n/a"


def format_game_delta(delta: GameDelta, now_et: datetime) -> str:
    """Format one game's delta block per the spec."""
    ts = now_et.strftime("%-I:%M%p ET")
    lines = [f"\U0001F504 UPDATE {ts} — {delta.matchup}", ""]
    for change in delta.injury_changes:
        lines.append(change)
    if delta.injury_changes:
        lines.append("")
    for rd in delta.ref_deltas:
        ed = rd.edge_delta
        # Only show refs that moved materially or flipped.
        if not rd.flipped and (ed is None or abs(ed) < TIER1_EDGE_DELTA):
            continue
        line_old = f"{rd.line_old:+.1f}" if rd.line_old is not None else "n/a"
        line_new = f"{rd.line_new:+.1f}" if rd.line_new is not None else "n/a"
        fam = rd.family.upper()
        side = f" {rd.side}" if rd.side else ""
        lines.append(
            f"{fam}{side}: {line_old} → {line_new} | "
            f"Edge {_fmt_edge(rd.edge_old)} → {_fmt_edge(rd.edge_new)}")
        lines.append(f"Was: {rd.status_old} → Now: {rd.status_new}")
    if delta.tier == 2:
        lines.append("")
        lines.append(f"Projected margin: {delta.margin_old:+.1f} → "
                     f"{delta.margin_new:+.1f}")
        lines.append(f"Projected total: {delta.total_old:.1f} → "
                     f"{delta.total_new:.1f}")
    return "\n".join(lines)


def format_alert(deltas: List[GameDelta], now_et: datetime) -> str:
    """One combined post per check window (spec: batching)."""
    firing = [d for d in deltas if d.tier > 0]
    if not firing:
        return ""
    # Tier 2 (re-board) games first, then Tier 1.
    firing.sort(key=lambda d: -d.tier)
    blocks = [format_game_delta(d, now_et) for d in firing]
    return "\n\n---\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class DeltaResult:
    posted: bool = False
    message: str = ""
    games_checked: int = 0
    games_firing: int = 0
    error: Optional[str] = None
    # Recomputed forecasts for firing games, for pregame snapshot writes
    # (scripts/delta_check.py). GameForecast objects.
    firing_forecasts: List = field(default_factory=list)


def run_delta_check(now: Optional[datetime] = None,
                    discord: bool = False,
                    webhook_url: str = "",
                    max_age_hours: float = BOARD_MAX_AGE_HOURS,
                    refresh_odds: bool = True) -> DeltaResult:
    """Run one delta check: pull injuries, recompute, diff, alert-or-silent.

    Fail-open contract: any failure (stale board, pull failure, recompute
    error) -> no alert, error logged in result. Never alert on stale data.
    """
    from datetime import timezone
    now = now or datetime.now(timezone.utc)
    result = DeltaResult()

    board = load_board_state(max_age_hours=max_age_hours)
    if not board:
        result.error = "no fresh board state to diff against"
        return result
    forecasts, _ = board

    from .forecasting import load_forecast_state
    try:
        state = load_forecast_state()
    except Exception as exc:
        result.error = f"forecast state load failed: {exc}"
        return result

    try:
        refresh_state_personnel(state, now=now)
    except Exception as exc:
        result.error = f"injury pull failed: {exc}"
        return result

    # Fresh market lines (cheap; keeps edge deltas honest).
    bovada_games: List = []
    if refresh_odds:
        try:
            from .sources.bovada import fetch_live_games
            bovada_games, _ = fetch_live_games(refresh=True,
                                               fetch_team_totals=False,
                                               now=now)
        except Exception:
            bovada_games = []

    def _bovada_for(away: str, home: str):
        for bg in bovada_games:
            if (getattr(bg, "away", None), getattr(bg, "home", None)) == (away, home):
                return bg
        return None

    pairs: List[Tuple[GameDelta, object]] = []
    for fc_dict in forecasts:
        if not fc_dict.get("available", True):
            continue
        try:
            new_fc = recompute_game(
                fc_dict, state,
                bovada_game=_bovada_for(fc_dict.get("away"),
                                        fc_dict.get("home")))
        except Exception:
            continue  # one bad game must not kill the check
        pairs.append((diff_game(fc_dict, new_fc), new_fc))

    deltas = [d for d, _ in pairs]
    result.games_checked = len(deltas)
    firing = [d for d in deltas if d.tier > 0]
    result.games_firing = len(firing)
    result.firing_forecasts = [fc for d, fc in pairs if d.tier > 0]
    if not firing:
        return result  # silent: nothing material changed

    try:
        from zoneinfo import ZoneInfo
        now_et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        now_et = now
    message = format_alert(deltas, now_et)
    result.message = message
    if discord and webhook_url and message:
        from .notifiers.forecast_discord import post_webhook
        send = post_webhook(webhook_url, message)
        result.posted = send.ok
        if not send.ok:
            result.error = f"discord post failed: {send.error}"
    return result
