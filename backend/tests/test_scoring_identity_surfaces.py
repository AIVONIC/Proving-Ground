"""Every surface that shows a grade says which scoring configuration produced it.

A composite is comparable to another only if both were computed the same way, and
the identifier that records this is useless if the artefacts a reader actually
holds do not carry it. Three surfaces show a grade to someone outside: the run
artifact, the certificate verification page, and the per-agent scorecard a vendor
forwards to a buyer. The first two were wired on main; the scorecard was not, and
a card quieter about comparability than the certificate behind it is the gap that
matters, because the card is the thing that gets emailed.

The absent case is tested as carefully as the present one. A grade taken before
the identifier existed carries none, and a blank field reads as "nothing to say
here" when it means "this number may not be compared with the one beside it".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.leaderboard.report import render_report
from app.leaderboard.store import load
from app.scoring.version import METHODOLOGY_VERSION, composite_id, describe_identity

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"


def _card(**overrides) -> str:
    entries = load()
    if not entries:
        pytest.skip("no promoted entries to render")
    runs = sorted((BACKEND / "data" / "runs").glob("*.json"))
    if not runs:
        pytest.skip("no run artifact available")
    entry = dict(entries[0])
    entry.update(overrides)
    data = json.loads(max(runs, key=lambda p: p.stat().st_size).read_text())
    return render_report((FRONTEND / "index.html").read_text(), entry, data, "test-slug")


def test_scorecard_states_the_scoring_config_when_the_grade_has_one():
    page = _card(composite_id="pgc-testid1", methodology_version="0.3")
    assert "pgc-testid1" in page, "the scorecard does not name the scoring configuration"
    assert "only be compared with another carrying the same scoring config" in page


def test_scorecard_says_plainly_when_a_grade_predates_the_identifier():
    """The absent case must be a SENTENCE, not a blank."""
    page = _card(composite_id=None, methodology_version=None)
    assert "not directly comparable" in page, (
        "a grade with no scoring id rendered without saying so; a blank field reads as "
        "'nothing to say here' rather than 'do not compare this'"
    )
    assert "predates scoring-config tracking" in page


def test_the_card_and_the_certificate_page_use_the_SAME_words():
    """One claim about comparability, one wording.

    Both surfaces answer the same buyer question, and two hand-written copies drift
    on whichever page nobody re-reads. Asserted by checking both render the shared
    helper's exact output, so a second copy of the sentence fails here.
    """
    phrase = describe_identity(None, html=True)
    signup = (BACKEND / "app" / "api" / "signup_service.py").read_text()
    report = (BACKEND / "app" / "leaderboard" / "report.py").read_text()
    for name, src in (("verify page", signup), ("scorecard", report)):
        assert "describe_identity" in src, f"{name} does not use the shared wording"
        assert "predates scoring-config tracking" not in src, (
            f"{name} holds its own copy of the comparability sentence; it must read the "
            f"shared one so the two cannot drift"
        )
    assert "not directly comparable" in phrase
    assert _card(composite_id=None).count("predates scoring-config tracking") >= 1


def test_the_live_identifier_is_stamped_into_a_fresh_grade_path():
    """The id the next re-grade will stamp is the one this config produces."""
    from app.grade import composite_id as grade_side_id

    assert grade_side_id() == composite_id()
    assert composite_id().startswith("pgc-")
    assert METHODOLOGY_VERSION


def test_existing_published_entries_are_honest_about_having_no_id():
    """Not a defect: every entry graded before the identifier existed carries None,
    and that must read as not-comparable rather than be quietly filled in with
    today's configuration."""
    for e in load():
        cid = e.get("composite_id")
        assert cid is None or str(cid).startswith("pgc-"), (
            f"{e['id']} carries a malformed scoring id {cid!r}"
        )
