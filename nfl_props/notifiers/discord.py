"""Discord Core digest — default OFF until a channel exists.

Requires both SEND_DISCORD=true (or --discord) and NFL_DISCORD_WEBHOOK_URL.
Sport-specific webhook env on purpose: never reuse another project's webhook
(playbook lesson: shared webhooks post to the wrong channel).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import HTTP_TIMEOUT_SECONDS
from ..tiers import Candidate


@dataclass
class SendResult:
    ok: bool
    status_code: Optional[int] = None
    error: Optional[str] = None


def core_embeds(candidates: List[Candidate]) -> List[dict]:
    cores = [c for c in candidates if c.tier == "Core"]
    if not cores:
        return []
    fields = []
    for c in cores[:10]:
        line = f" {c.line:+.1f}" if c.market == "SPREAD" else (
            f" {c.line:.1f}" if c.line is not None else "")
        fields.append({
            "name": f"{c.away} @ {c.home} — {c.market}",
            "value": (f"**{c.side}{line}** ({c.american:+d})\n"
                      f"model {c.p_model:.1%} vs market {c.p_market:.1%} | "
                      f"EV {c.ev:+.1%}"),
            "inline": False,
        })
    return [{
        "title": f"NFL Core plays ({len(cores)})",
        "description": "Flat 1u research plays. Prospective — see grading.",
        "fields": fields,
    }]


def send_discord_embeds(webhook_url: str, embeds: List[dict]) -> SendResult:
    if not webhook_url:
        return SendResult(ok=False, error="no webhook url configured")
    if not embeds:
        return SendResult(ok=True)  # nothing to send is success, not failure
    body = json.dumps({"embeds": embeds}).encode("utf-8")
    req = Request(webhook_url, data=body,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return SendResult(ok=200 <= resp.status < 300,
                              status_code=resp.status)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return SendResult(ok=False, error=str(exc))
