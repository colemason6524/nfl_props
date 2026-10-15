"""Regression test: backup QB on injury report must not flag starter as out.

2026-10-15 incident: ESPN listed Sam Ehlinger (DEN backup) and Jalen Milroe
(SEA backup) as "Out". The parser set team qb_status="out" for both teams,
triggering a false "both starting QBs OUT" delta alert to Discord.
Bo Nix and Sam Darnold were both Active and playing.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nfl_props.sources.personnel_feed import parse_espn_payload


def _espn_payload(entries):
    """Build a minimal ESPN injuries payload from (team, name, pos, status) tuples."""
    blocks = {}
    for team, name, pos, status in entries:
        blocks.setdefault(team, []).append({
            "athlete": {
                "displayName": name,
                "position": {"abbreviation": pos},
                "team": {"abbreviation": team},
            },
            "status": status,
            "id": hash(name) % 1000000,
        })
    return {"injuries": [{"injuries": v} for v in blocks.values()]}


def test_backup_qb_out_does_not_flag_starter():
    payload = _espn_payload([
        ("DEN", "Bo Nix", "QB", "Active"),
        ("DEN", "Sam Ehlinger", "QB", "Out"),
        ("SEA", "Sam Darnold", "QB", "Active"),
        ("SEA", "Drew Lock", "QB", "Active"),
        ("SEA", "Jalen Milroe", "QB", "Out"),
    ])
    teams = parse_espn_payload(payload, now=datetime.now(timezone.utc))
    assert teams["DEN"]["qb_status"] == "confirmed", \
        f"DEN should be confirmed (Nix active), got {teams['DEN']['qb_status']}"
    assert teams["SEA"]["qb_status"] == "confirmed", \
        f"SEA should be confirmed (Darnold active), got {teams['SEA']['qb_status']}"
    # Details are kept for downstream starter matching
    assert len(teams["DEN"]["qb_details"]) == 2
    assert len(teams["SEA"]["qb_details"]) == 3


def test_all_qbs_out_flags_team():
    payload = _espn_payload([
        ("CHI", "Caleb Williams", "QB", "Out"),
        ("CHI", "Tyson Bagent", "QB", "Doubtful"),
    ])
    teams = parse_espn_payload(payload, now=datetime.now(timezone.utc))
    assert teams["CHI"]["qb_status"] == "out", \
        f"CHI should be out (no healthy QB), got {teams['CHI']['qb_status']}"


def test_single_qb_out_flags_team():
    # Only one QB listed and he's out -> genuine problem
    payload = _espn_payload([
        ("LV", "Geno Smith", "QB", "Out"),
    ])
    teams = parse_espn_payload(payload, now=datetime.now(timezone.utc))
    assert teams["LV"]["qb_status"] == "out"
