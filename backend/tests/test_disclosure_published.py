"""The Inquio disclosure is published, verbatim, and structurally apart from scoring.

It is a text agreed with a third party. Three things can go wrong quietly and this
gates all three: it silently stops rendering on a re-render; its wording drifts
from what was agreed; or it drifts between the two surfaces that publish it.

The expected text here is an INDEPENDENT transcription, not an import of the
module under test. Asserting a module equals itself would pass whatever the module
said, which is the whole failure mode -- a control has to come from outside the
thing it is checking.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.leaderboard.disclosure import (
    DISCLOSURE_PARAGRAPHS, DISCLOSURE_TITLE, as_html, as_markdown,
)

ROOT = Path(__file__).resolve().parents[2]

AGREED_TITLE = "Dimension co-authorship and conflict disclosure"
AGREED = [
    "Several dimensions in this benchmark were co-developed with Inquio (Martin Franc), drawing on failure patterns observed in their production deployments and in Aivonic Labs' own.",
    "While that co-authorship is active, Inquio does not appear on the Proving Ground leaderboard and is not scored by this benchmark. If that changes, this disclosure is updated and published before any Inquio score is shown.",
    "The taxonomy publishes named patterns and the probes that detect them. It does not publish frequency or prevalence rates. Frequencies observed in either party's deployments come from client environments and do not generalise, and presenting them as rates would misrepresent both the data and its scope.",
    "Proving Ground is measured by its own dimensions. Where a dimension applies to a scoring system, the benchmark's own result is published alongside it.",
]


def test_wording_is_exactly_what_was_agreed():
    assert DISCLOSURE_TITLE == AGREED_TITLE
    assert DISCLOSURE_PARAGRAPHS == AGREED, (
        "the disclosure no longer matches the text agreed with Martin Franc. This is not a "
        "wording preference: it is a published statement agreed with another party, and it "
        "changes with them or not at all."
    )


def test_the_published_leaderboard_carries_it():
    """Asserts on the PUBLISHED FILE, not on the renderer. A generator that emits
    the right string into a page nobody re-rendered publishes nothing."""
    page = (ROOT / "frontend" / "leaderboard.html").read_text()
    text = re.sub(r"<[^>]+>", " ", page)
    text = text.replace("&mdash;", "-").replace("&rsquo;", "'").replace("&#x27;", "'")
    text = " ".join(text.split())
    assert AGREED_TITLE in text, "the disclosure is not on the published leaderboard page"
    for para in AGREED:
        needle = " ".join(para.replace("'", "'").split())
        assert needle in text or needle.replace("'", "&#x27;") in text, (
            f"missing from the published leaderboard: {para[:70]}..."
        )


def test_it_renders_after_every_scoring_surface():
    """Credit and scoring stay visually and structurally separate. The disclosure
    names a party deliberately absent from the board; rendering it among the cards
    would imply a relationship to the ranking it exists to deny."""
    page = (ROOT / "frontend" / "leaderboard.html").read_text()
    disclosure_at = page.find('class="disclosure"')
    assert disclosure_at > 0
    last_card = page.rfind("lb-rank")
    assert last_card > 0, "no scorecards on the page; this assertion would be vacuous"
    assert disclosure_at > last_card, "the disclosure renders among the scoring cards"


def test_no_prose_cap_sneaks_back_into_the_disclosure():
    assert "max-width" not in as_html(), (
        "a max-width on disclosure prose. The container is the measure; this exact cap "
        "was raised four times before it was made structural."
    )


def test_markdown_and_html_carry_the_same_paragraphs():
    """Two surfaces publish this. They must not drift."""
    md = as_markdown()
    html = re.sub(r"<[^>]+>", " ", as_html())
    import html as _h
    html = " ".join(_h.unescape(html).split())
    for para in AGREED:
        assert para in md, f"missing from the markdown surface: {para[:60]}"
        assert " ".join(para.split()) in html, f"missing from the html surface: {para[:60]}"


def test_no_link_to_the_unpublished_taxonomy_page():
    """The disclosure mentions the taxonomy, which is staged for Martin's review and
    not live. A disclosure whose only outbound link 404s is careless about exactly
    the thing it is being careful about. Remove this test when /taxonomy ships."""
    assert "/taxonomy" not in as_html()
