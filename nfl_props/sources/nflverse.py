"""nflverse free data: play-by-play parquet releases + Lee Sharpe games.csv.

No package dependency and no API key: the same release assets that
nfl_data_py / nflreadr read are fetched directly, so a broken package pin can
never block a game-day run. games.csv carries schedules, final scores, and
closing spread/total/moneyline for every game since 1999 — the historical
odds corpus for backtests.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from ..config import (CURRENT_SEASON, DIAGNOSTICS_DIR, FIRST_SEASON,
                      NFLVERSE_GAMES_URL, NFLVERSE_PBP_URL, PROCESSED_DIR,
                      RAW_DIR)
from ..teams import normalize_team
from ..utils import download_if_missing, log

PBP_COLUMNS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam",
    "home_team", "away_team", "pass", "rush", "epa",
]

GAMES_KEEP_COLUMNS = [
    "game_id", "season", "game_type", "week", "gameday", "weekday",
    "gametime", "away_team", "away_score", "home_team", "home_score",
    "result", "total", "overtime",
    "away_moneyline", "home_moneyline",
    "spread_line", "away_spread_odds", "home_spread_odds",
    "total_line", "under_odds", "over_odds",
    "div_game", "roof", "surface",
]

TEAM_GAMES_PATH = PROCESSED_DIR / "team_games.parquet"
GAMES_PATH = PROCESSED_DIR / "games.parquet"


def _pbp_raw_path(season: int) -> Path:
    return RAW_DIR / f"play_by_play_{season}.parquet"


def _games_raw_path() -> Path:
    return RAW_DIR / "games.csv"


def seasons_in_scope() -> List[int]:
    return list(range(FIRST_SEASON, CURRENT_SEASON + 1))


def refresh_data(seasons: Optional[Iterable[int]] = None,
                 refresh_all: bool = False) -> None:
    """Download pbp parquet per season plus games.csv.

    Completed-season pbp files are immutable, so they are only fetched once;
    the current season and games.csv are always re-fetched.
    """
    seasons = list(seasons) if seasons is not None else seasons_in_scope()
    for season in seasons:
        dest = _pbp_raw_path(season)
        refresh = refresh_all or season >= CURRENT_SEASON
        try:
            fetched = download_if_missing(NFLVERSE_PBP_URL.format(season=season),
                                          dest, refresh=refresh)
            log(f"[nflverse] pbp {season}: {'downloaded' if fetched else 'cached'}")
        except Exception as exc:  # noqa: BLE001 - current-season file may not exist yet
            if season >= CURRENT_SEASON:
                log(f"[nflverse] pbp {season} unavailable yet ({exc}); skipping")
            else:
                raise
    download_if_missing(NFLVERSE_GAMES_URL, _games_raw_path(), refresh=True)
    log("[nflverse] games.csv: downloaded")


def load_raw_games() -> pd.DataFrame:
    df = pd.read_csv(_games_raw_path())
    keep = [c for c in GAMES_KEEP_COLUMNS if c in df.columns]
    df = df[keep].copy()
    df["home_team"] = df["home_team"].map(normalize_team)
    df["away_team"] = df["away_team"].map(normalize_team)
    df = df[df["season"] >= FIRST_SEASON]
    return df


def _load_pbp_season(season: int) -> pd.DataFrame:
    path = _pbp_raw_path(season)
    if not path.exists():
        return pd.DataFrame(columns=PBP_COLUMNS)
    df = pd.read_parquet(path, columns=PBP_COLUMNS)
    return df


def build_team_games(seasons: Optional[Iterable[int]] = None) -> pd.DataFrame:
    """One row per team-game: offensive EPA/play + play volume."""
    seasons = list(seasons) if seasons is not None else seasons_in_scope()
    frames = []
    for season in seasons:
        pbp = _load_pbp_season(season)
        if pbp.empty:
            continue
        plays = pbp[(pbp["epa"].notna())
                    & (pbp["posteam"].notna())
                    & ((pbp["pass"] == 1) | (pbp["rush"] == 1))].copy()
        if plays.empty:
            continue
        plays["posteam"] = plays["posteam"].map(normalize_team)
        plays["defteam"] = plays["defteam"].map(normalize_team)
        grouped = (plays.groupby(["game_id", "season", "week", "posteam", "defteam"],
                                 as_index=False)
                   .agg(off_epa_total=("epa", "sum"),
                        off_plays=("epa", "size")))
        grouped["off_epa_pp"] = grouped["off_epa_total"] / grouped["off_plays"]
        frames.append(grouped)
        log(f"[nflverse] team-games {season}: {len(grouped)} rows")
    if not frames:
        raise RuntimeError("no pbp data found; run refresh-data first")
    return pd.concat(frames, ignore_index=True)


def build(seasons: Optional[Iterable[int]] = None) -> dict:
    """Write the canonical store and return merge diagnostics."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    games = load_raw_games()
    team_games = build_team_games(seasons)

    unmatched_teams = sorted(
        set(games.loc[games["home_team"].isna(), "game_id"].tolist()
            + games.loc[games["away_team"].isna(), "game_id"].tolist()))

    completed = games[games["home_score"].notna() & games["away_score"].notna()]
    pbp_game_ids = set(team_games["game_id"].unique())
    completed_ids = set(completed["game_id"].unique())
    missing_pbp = sorted(completed_ids - pbp_game_ids)
    match_rate = (1.0 - len(missing_pbp) / max(1, len(completed_ids)))

    games.to_parquet(GAMES_PATH, index=False)
    team_games.to_parquet(TEAM_GAMES_PATH, index=False)

    diagnostics = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "seasons": sorted(games["season"].unique().tolist()),
        "games_rows": int(len(games)),
        "completed_games": int(len(completed_ids)),
        "team_game_rows": int(len(team_games)),
        "pbp_match_rate": round(match_rate, 4),
        "completed_games_missing_pbp": missing_pbp[:50],
        "games_with_unmatched_team_abbr": unmatched_teams[:50],
    }
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    diag_path = DIAGNOSTICS_DIR / "build_diagnostics.json"
    diag_path.write_text(json.dumps(diagnostics, indent=2))
    log(f"[nflverse] build ok: pbp_match_rate={match_rate:.4f} "
        f"({len(missing_pbp)} completed games missing pbp) -> {diag_path}")
    if unmatched_teams:
        log(f"[nflverse] WARNING: {len(unmatched_teams)} games with unmatched "
            "team abbreviations (never force-match; fix teams.py aliases)")
    return diagnostics


def load_processed() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(games, team_games) from the canonical store."""
    if not GAMES_PATH.exists() or not TEAM_GAMES_PATH.exists():
        raise RuntimeError("canonical store missing; run `cli build` first")
    return pd.read_parquet(GAMES_PATH), pd.read_parquet(TEAM_GAMES_PATH)
