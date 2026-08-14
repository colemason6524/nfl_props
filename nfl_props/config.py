import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = PROJECT_ROOT / ".cache"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
HISTORY_DIR = OUTPUTS_DIR / "history"
BACKTESTS_DIR = OUTPUTS_DIR / "backtests"
DIAGNOSTICS_DIR = OUTPUTS_DIR / "diagnostics"
CONFIG_DIR = PROJECT_ROOT / "config"

# Historical coverage. pbp EPA is reliable from 1999+, but ratings only use
# the modern era to avoid rule-era drift.
FIRST_SEASON = int(os.environ.get("NFL_FIRST_SEASON", "2013"))
TUNE_SEASONS = tuple(range(2015, 2023))       # fit projection coefficients + residuals
HOLDOUT_SEASONS = tuple(range(2023, 2026))    # never tuned on; backtest report target
CURRENT_SEASON = int(os.environ.get("NFL_CURRENT_SEASON", "2026"))

# nflverse release assets (free, no key). Mirror note: if the GitHub release
# path dies, nflreadr documents the same assets; games.csv is also mirrored at
# http://www.habitatring.com/games.csv (Lee Sharpe).
NFLVERSE_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{season}.parquet"
)
NFLVERSE_GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

HTTP_TIMEOUT_SECONDS = 60
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for d in (RAW_DIR, PROCESSED_DIR, CACHE_DIR, HISTORY_DIR, BACKTESTS_DIR,
              DIAGNOSTICS_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
