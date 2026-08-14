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

import numpy as np
import pandas as pd

from ..config import (CURRENT_SEASON, DIAGNOSTICS_DIR, FIRST_SEASON,
                      NFLVERSE_GAMES_URL, NFLVERSE_PBP_URL, PROCESSED_DIR,
                      RAW_DIR)
from ..teams import normalize_team
from ..utils import download_if_missing, log

PBP_COLUMNS = [
    "game_id", "season", "week", "season_type", "posteam", "defteam",
    "home_team", "away_team", "pass", "rush", "epa",
    "play_type", "down", "success", "yards_gained",
    "qb_dropback", "qb_kneel", "qb_spike", "qb_scramble", "sack",
    "passer_id", "passer_player_id", "passer_player_name", "cpoe",
]

GAMES_KEEP_COLUMNS = [
    "game_id", "season", "game_type", "week", "gameday", "weekday",
    "gametime", "away_team", "away_score", "home_team", "home_score",
    "result", "total", "overtime",
    "away_moneyline", "home_moneyline",
    "spread_line", "away_spread_odds", "home_spread_odds",
    "total_line", "under_odds", "over_odds",
    "div_game", "roof", "surface",
    "location", "away_rest", "home_rest", "stadium_id", "stadium",
    "temp", "wind",
    "away_qb_id", "home_qb_id", "away_qb_name", "home_qb_name",
]

TEAM_GAMES_PATH = PROCESSED_DIR / "team_games.parquet"
GAMES_PATH = PROCESSED_DIR / "games.parquet"
SITUATIONAL_PATH = PROCESSED_DIR / "team_situational_games.parquet"
QB_GAMES_PATH = PROCESSED_DIR / "qb_games.parquet"


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


def _load_pbp_season(season: int, columns: Optional[List[str]] = None) -> pd.DataFrame:
    path = _pbp_raw_path(season)
    cols = list(columns) if columns is not None else PBP_COLUMNS
    if not path.exists():
        return pd.DataFrame(columns=cols)
    import pyarrow.parquet as pq
    available = pq.ParquetFile(path).schema_arrow.names
    cols = [c for c in cols if c in available]
    if not cols:
        return pd.DataFrame(columns=cols)
    return pd.read_parquet(path, columns=cols)


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


def build_situational_games(seasons: Optional[Iterable[int]] = None
                            ) -> pd.DataFrame:
    """One row per team-game: pass/rush EPA splits, success, sacks, explosives.

    Play populations are explicit (see the ARCHITECTURE data definitions):
    dropback = qb_dropback == 1 (sacks and scrambles included), rush excludes
    kneels/spikes, and every rate carries its raw denominator so downstream
    ratings can shrink low-sample metrics toward a league prior.
    """
    seasons = list(seasons) if seasons is not None else seasons_in_scope()
    frames = []
    for season in seasons:
        pbp = _load_pbp_season(season)
        if pbp.empty:
            continue
        df = pbp.copy()
        df["posteam"] = df["posteam"].map(normalize_team)
        df["defteam"] = df["defteam"].map(normalize_team)
        df = df[df["posteam"].notna() & df["epa"].notna()]

        is_dropback = (df["qb_dropback"] == 1).astype(float)
        is_kneel = (df.get("qb_kneel") == 1).astype(float)
        is_spike = (df.get("qb_spike") == 1).astype(float)
        is_rush = ((df["rush"] == 1)
                   & ~is_kneel.astype(bool) & ~is_spike.astype(bool)).astype(float)
        is_success = (df["success"] == 1).astype(float)
        is_sack = (df["sack"] == 1).astype(float)
        early_down = df["down"].isin([1, 2]).astype(float)
        yds = df["yards_gained"].fillna(0).astype(float)
        play = is_dropback + is_rush

        d = df[["game_id", "season", "week", "posteam", "defteam"]].copy()
        d["pass_dropbacks"] = is_dropback
        d["pass_epa_total"] = df["epa"] * is_dropback
        d["pass_success"] = is_success * is_dropback
        d["rush_att"] = is_rush
        d["rush_epa_total"] = df["epa"] * is_rush
        d["rush_success"] = is_success * is_rush
        d["sack_count"] = is_sack * is_dropback
        d["explosive_pass"] = (is_dropback * (yds >= 15)).astype(float)
        d["explosive_rush"] = (is_rush * (yds >= 10)).astype(float)
        d["early_epa_total"] = df["epa"] * early_down * play
        d["early_plays"] = early_down * play

        g = (d.groupby(["game_id", "season", "week", "posteam", "defteam"],
                       as_index=False).sum())
        g["pass_epa_pp"] = g["pass_epa_total"] / g["pass_dropbacks"].replace(0, np.nan)
        g["pass_success_rate"] = g["pass_success"] / g["pass_dropbacks"].replace(0, np.nan)
        g["rush_epa_pp"] = g["rush_epa_total"] / g["rush_att"].replace(0, np.nan)
        g["rush_success_rate"] = g["rush_success"] / g["rush_att"].replace(0, np.nan)
        g["sack_rate"] = g["sack_count"] / g["pass_dropbacks"].replace(0, np.nan)
        g["explosive_pass_rate"] = g["explosive_pass"] / g["pass_dropbacks"].replace(0, np.nan)
        g["explosive_rush_rate"] = g["explosive_rush"] / g["rush_att"].replace(0, np.nan)
        g["early_epa_pp"] = g["early_epa_total"] / g["early_plays"].replace(0, np.nan)
        frames.append(g)
        log(f"[nflverse] situational {season}: {len(g)} rows")
    if not frames:
        raise RuntimeError("no pbp data found for situational metrics")
    return pd.concat(frames, ignore_index=True)


def build_qb_games(seasons: Optional[Iterable[int]] = None) -> pd.DataFrame:
    """One row per passer-game: dropbacks, EPA/dropback, CPOE, sacks, scrambles.

    `passer_id` is preferred over `passer_player_id` (it covers scrambles);
    `passer_player_name` is kept as an audit label. `is_primary` marks the
    highest-volume passer per team-game, used for the lag-based starter proxy.
    """
    seasons = list(seasons) if seasons is not None else seasons_in_scope()
    frames = []
    for season in seasons:
        pbp = _load_pbp_season(season)
        if pbp.empty:
            continue
        df = pbp.copy()
        df["posteam"] = df["posteam"].map(normalize_team)
        df["defteam"] = df["defteam"].map(normalize_team)
        df = df[(df["qb_dropback"] == 1) & df["epa"].notna()
                & df["posteam"].notna()]

        qb_id = df["passer_id"].fillna(df["passer_player_id"])
        qb_name = df["passer_player_name"].fillna(df["passer_id"])
        d = df[["game_id", "season", "week", "posteam", "defteam"]].copy()
        d["qb_id"] = qb_id
        d["qb_name"] = qb_name
        d["dropbacks"] = 1.0
        d["epa_total"] = df["epa"]
        d["cpoe"] = df["cpoe"]
        d["sacks"] = (df["sack"] == 1).astype(float)
        d["scrambles"] = (df["qb_scramble"] == 1).astype(float)
        d = d[d["qb_id"].notna()]
        if d.empty:
            continue
        g = (d.groupby(["game_id", "season", "week", "posteam", "defteam",
                        "qb_id", "qb_name"], as_index=False)
             .agg(dropbacks=("dropbacks", "sum"),
                  epa_total=("epa_total", "sum"),
                  cpoe=("cpoe", "mean"),
                  sacks=("sacks", "sum"),
                  scrambles=("scrambles", "sum")))
        g["epa_per_db"] = g["epa_total"] / g["dropbacks"]
        g["sack_rate"] = g["sacks"] / g["dropbacks"]
        g = g.sort_values(["game_id", "posteam", "dropbacks"],
                          ascending=[True, True, False])
        g["is_primary"] = ~g.duplicated(["game_id", "posteam"])
        frames.append(g)
        log(f"[nflverse] qb-games {season}: {len(g)} rows")
    if not frames:
        raise RuntimeError("no pbp data found for qb games")
    return pd.concat(frames, ignore_index=True)


def build(seasons: Optional[Iterable[int]] = None) -> dict:
    """Write the canonical store and return merge diagnostics."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    games = load_raw_games()
    team_games = build_team_games(seasons)
    situational = build_situational_games(seasons)
    qb_games = build_qb_games(seasons)

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
    situational.to_parquet(SITUATIONAL_PATH, index=False)
    qb_games.to_parquet(QB_GAMES_PATH, index=False)

    diagnostics = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "seasons": sorted(games["season"].unique().tolist()),
        "games_rows": int(len(games)),
        "completed_games": int(len(completed_ids)),
        "team_game_rows": int(len(team_games)),
        "situational_rows": int(len(situational)),
        "qb_game_rows": int(len(qb_games)),
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


def load_processed_v2() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame,
                                  pd.DataFrame]:
    """(games, team_games, situational, qb_games) for the v2 shadow model."""
    games, team_games = load_processed()
    for path in (SITUATIONAL_PATH, QB_GAMES_PATH):
        if not path.exists():
            raise RuntimeError(f"{path.name} missing; run `cli build` first")
    return (games, team_games, pd.read_parquet(SITUATIONAL_PATH),
            pd.read_parquet(QB_GAMES_PATH))
