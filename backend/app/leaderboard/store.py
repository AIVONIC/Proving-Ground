"""Leaderboard store: a flat JSON file of graded-agent entries.

Static-first by design. A grade run is *promoted* into an entry here, and the
public leaderboard page is rendered from these entries with no live service to
fail. The certification flow (Phase 4) layers a database + API over the exact
same entry shape, so nothing here is throwaway.
"""
from __future__ import annotations

import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
STORE = BACKEND / "data" / "leaderboard" / "entries.json"


def load() -> list[dict]:
    if not STORE.exists():
        return []
    return json.loads(STORE.read_text()).get("entries", [])


def _rank_key(e: dict):
    # Rank by composite, then security floor, then name. Tiers are informational,
    # ranking is by the composite the same way every agent is measured.
    return (-e["composite"], -e.get("subscores", {}).get("security", 0.0), e["name"].lower())


def save(entries: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(entries, key=_rank_key)
    STORE.write_text(json.dumps({"entries": ordered}, indent=2) + "\n")


def upsert(entry: dict) -> list[dict]:
    """Insert or replace by id, keep the board sorted, return the full board."""
    entries = [e for e in load() if e["id"] != entry["id"]]
    entries.append(entry)
    save(entries)
    return load()
