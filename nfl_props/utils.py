import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .config import HTTP_TIMEOUT_SECONDS, HTTP_USER_AGENT


def fetch_bytes(url: str, retries: int = 3, backoff: float = 2.0) -> bytes:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": HTTP_USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            last_err = err
            if isinstance(err, urllib.error.HTTPError) and err.code == 404:
                raise
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def download_if_missing(url: str, dest: Path, refresh: bool = False,
                        polite_delay: float = 0.0) -> bool:
    """Download url to dest. Returns True if a network fetch happened."""
    if dest.exists() and not refresh:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = fetch_bytes(url)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    if polite_delay:
        time.sleep(polite_delay)
    return True


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
