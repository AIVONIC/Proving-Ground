"""An undisclosed omission is itself a claim, and this page made it explicitly.

Before 2026-09-19 the only occurrence of "withheld" in the published copy was
SPARK's "recused, NOT withheld" - a contrast telling a reader nothing was being held
back, while a grade was. Silence would have been an omission; that phrasing made it
an assertion.

The pattern without this: the operator's own agent is displayed at 88.68, the agent
at 40.00 is invisible, and the operator made both calls.
"""
from __future__ import annotations

import html
import re

from app.leaderboard.render import withheld_notice
from app.leaderboard.store import load


def _t(s: str) -> str:
    return " ".join(html.unescape(re.sub("<[^>]+>", " ", s)).split())


def test_a_withheld_grade_is_disclosed_with_its_reason():
    entries = load()
    held = [e for e in entries if not e.get("published", True)]
    if not held:
        return                      # nothing withheld: nothing to disclose
    t = _t(withheld_notice(entries))
    assert "Disclosed omission" in t
    for e in held:
        assert e.get("withheld_reason"), \
            f"{e['id']} is withheld with no recorded reason - the page will say so"
        assert e["withheld_reason"] in t, "the recorded reason must reach the page"


def test_no_withholding_means_no_notice():
    """NEGATIVE CONTROL. Without this, a notice that always renders would pass above."""
    assert withheld_notice([{"published": True}, {"published": True}]) == ""


def test_an_undocumented_withholding_says_so_rather_than_vanishing():
    """A grade held with no reason must be visibly a defect, not silently absent."""
    t = _t(withheld_notice([{"published": False}]))
    assert "no reason recorded" in t and "defect" in t


def test_the_count_follows_the_data():
    one = _t(withheld_notice([{"published": False, "withheld_reason": "x"}]))
    two = _t(withheld_notice([{"published": False, "withheld_reason": "x"},
                              {"published": False, "withheld_reason": "y"}]))
    assert "One grade" in one and "2 grades" in two
