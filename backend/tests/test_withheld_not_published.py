"""A withheld agent must not appear on ANY public surface.

⛔ WHY THIS IS A WHOLE FILE.

`published: false` exists to hold back a capped safety result until the vendor
has been told. The board honoured it within minutes. Two other public surfaces
did not, and the worse of the two was far more damaging than the board row that
had been removed:

  /cohort       printed the composite (40.00), the critical-failure count, the
                platform version, AND a sentence naming the agent and exactly
                what it complied with. Public and indexable.
  /scorecards/  listed the withheld card, making it discoverable.

Neither was a bug in the flag. Each surface derived "is this public" for itself,
which is the shape CLAUDE.md already records for test-session exclusion: define
it ONCE, because two copies drift silently in both directions. Every public
renderer now calls store.load_published().

This test asserts the OUTPUT, not the call. A surface that reads the right
function and still prints the name - the container footprint table did exactly
that, keyed by platform name out of a static JSON file - passes a call-site check
and fails this one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.leaderboard.store import load, load_published

ROOT = Path(__file__).resolve().parents[2]
# Every file served to the public. A new one belongs in this list.
PUBLIC = ["frontend/leaderboard.html", "frontend/cohort.html", "frontend/index.html",
          "frontend/methodology.html", "frontend/certs.json",
          "frontend/scorecards/index.html", "frontend/sitemap.xml", "frontend/llms.txt"]


def _withheld() -> list[dict]:
    pub = {e["id"] for e in load_published()}
    return [e for e in load() if e["id"] not in pub]


@pytest.mark.parametrize("rel", PUBLIC)
def test_no_withheld_agent_name_on_a_public_surface(rel):
    held = _withheld()
    if not held:
        pytest.skip("nothing is withheld right now")
    p = ROOT / rel
    if not p.exists():
        pytest.skip(f"{rel} not generated")
    text = p.read_text(encoding="utf-8", errors="replace").lower()
    for e in held:
        assert e["name"].lower() not in text, (
            f"{rel} names withheld agent {e['name']!r}. It is withheld because "
            f"publishing a capped safety result about a reference build we "
            f"configured ourselves is a claim about a product the vendor never "
            f"shipped. The fact that we graded them at all is part of what is held.")


def test_the_withheld_card_itself_is_still_generated():
    """Withholding is not deletion. The card is the document handed to the vendor,
    so it must exist - it is simply not linked or listed anywhere."""
    held = _withheld()
    if not held:
        pytest.skip("nothing is withheld right now")
    for e in held:
        cards = list((ROOT / "frontend/scorecards").glob(f'{e["id"]}-*.html'))
        assert cards, f"no scorecard generated for withheld {e['name']}; it is what we send them"
        body = cards[-1].read_text(encoding="utf-8")
        assert "Withheld scorecard" in body, "the card must say it is not published"
        assert "no public certificate" in body


def test_withheld_agents_have_no_certificate_code():
    from app.leaderboard.certs import build
    held = _withheld()
    if not held:
        pytest.skip("nothing is withheld right now")
    certs = build(load())["certificates"]
    for e in held:
        assert not any(c["agent"] == e["name"] for c in certs.values()), (
            f"{e['name']} has a resolvable certificate; that republishes the "
            f"withheld number at a different URL")


def test_the_detector_can_actually_see_a_name():
    """POSITIVE CONTROL. If the files were empty or unreadable, every assertion
    above would pass while checking nothing."""
    pub = load_published()
    assert pub, "no published entries; this suite would pass vacuously"
    board = (ROOT / "frontend/leaderboard.html").read_text(errors="replace").lower()
    assert any(e["name"].lower() in board for e in pub), (
        "no PUBLISHED agent name found on the board either - the check is blind, "
        "so its silence about withheld agents proves nothing")
