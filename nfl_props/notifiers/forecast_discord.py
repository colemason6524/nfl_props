"""Forecast-first Discord board: three sections, every game, no truncation.

Moneylines / Spreads / Totals, one line per game, current day only. Messages
are chunked only to respect Discord's size limit; no section is truncated and
no game is dropped. Bovada is the primary displayed source; Polymarket shows
when Bovada is unavailable. The forecast is never changed by a price.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import HTTP_TIMEOUT_SECONDS
from ..forecast import GameForecast
from ..forecast_output import render_board

DISCORD_CHUNK_LIMIT = 1900


@dataclass
class SendResult:
    ok: bool
    status_code: Optional[int] = None
    error: Optional[str] = None


def chunk_messages(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> List[str]:
    chunks: List[str] = []
    current: List[str] = []
    used = 0
    for line in text.splitlines(keepends=True):
        if used + len(line) > limit and current:
            chunks.append("".join(current))
            current, used = [], 0
        current.append(line)
        used += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def post_webhook(webhook_url: str, content: str) -> SendResult:
    if not webhook_url:
        return SendResult(ok=False, error="no webhook url configured")
    body = json.dumps({"content": content}).encode("utf-8")
    req = Request(webhook_url, data=body,
                  headers={"Content-Type": "application/json",
                           "User-Agent": "nfl_props/1.0"})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return SendResult(ok=200 <= resp.status < 300,
                              status_code=resp.status)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return SendResult(ok=False, error=str(exc))


def send_forecast(webhook_url: str, forecasts: List[GameForecast],
                  summary: dict, now: Optional[datetime] = None) -> SendResult:
    """Post the three-section board. Nothing to post is success."""
    if not any(fc.available for fc in forecasts):
        return SendResult(ok=True)
    text = render_board(forecasts, summary, now=now)
    for chunk in chunk_messages(text):
        result = post_webhook(webhook_url, chunk)
        if not result.ok:
            return result
    return SendResult(ok=True)


def webhook_from_env() -> str:
    return os.environ.get("NFL_DISCORD_WEBHOOK_URL", "")
