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
# ⛔ A WITHHELD GRADE NEVER TOUCHES THE TRACKED FILE. `entries.json` is committed to a
# PUBLIC repo. Until 2026-09-27 a withheld entry (`published: false`) sat in it in
# full - composite, critical-failure count, every subscore and the reason it was
# withheld - so a grade held back from the website pending the vendor's disclosure
# was readable by anyone on GitHub. Every public RENDERER honoured the flag; the
# data file itself was the leak. Withheld entries now live here, in the gitignored
# private directory, and save() routes them by the flag, so no caller can put one in
# the public file.
PRIVATE_STORE = BACKEND / "data" / "private" / "withheld_entries.json"


def _read(path: Path) -> list[dict]:
    return json.loads(path.read_text()).get("entries", []) if path.exists() else []


def load() -> list[dict]:
    return _read(STORE) + _read(PRIVATE_STORE)


def _rank_key(e: dict):
    # Rank by composite, then security floor, then name. Tiers are informational,
    # ranking is by the composite the same way every agent is measured.
    return (-e["composite"], -e.get("subscores", {}).get("security", 0.0), e["name"].lower())


def save(entries: list[dict]) -> None:
    public = sorted((e for e in entries if e.get("published", True)), key=_rank_key)
    withheld = sorted((e for e in entries if not e.get("published", True)), key=_rank_key)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({"entries": public}, indent=2) + "\n")
    if withheld or PRIVATE_STORE.exists():
        PRIVATE_STORE.parent.mkdir(parents=True, exist_ok=True)
        PRIVATE_STORE.write_text(json.dumps({"entries": withheld}, indent=2) + "\n")


def upsert(entry: dict) -> list[dict]:
    """Insert or replace by id, keep the board sorted, return the full board."""
    entries = [e for e in load() if e["id"] != entry["id"]]
    entries.append(entry)
    save(entries)
    return load()


def load_published() -> list[dict]:
    """Entries that may appear on ANY public surface.

    ⛔ ONE DEFINITION OF "PUBLIC", BECAUSE THREE COPIES DRIFTED WITHIN AN HOUR.

    `published: false` was added to withhold a capped safety result until the
    vendor had been told. The board honoured it immediately. Two other public
    surfaces did not, because each derived "is this public" for itself:

      /cohort            printed the composite, the critical-failure count, the
                         platform version AND a sentence naming the agent and
                         what it complied with. Fully public, indexable, and far
                         more damaging than the board row that had been removed.
      /scorecards/       listed the withheld card, making it discoverable.

    Neither was a bug in the flag. Both were surfaces answering the question
    themselves, and CLAUDE.md already records that shape: define the exclusion
    ONCE, because two copies of "what counts as public" drift silently in both
    directions. Every public renderer calls THIS, and a new surface that forgets
    to is the thing to look for in review.

    Defaults TRUE, so an entry missing the field is published exactly as before.
    """
    return [e for e in load() if e.get("published", True)]
