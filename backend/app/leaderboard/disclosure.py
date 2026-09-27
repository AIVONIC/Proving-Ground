"""The Inquio dimension co-authorship and conflict disclosure.

ONE definition, read by every surface that publishes it: the leaderboard page and
the head of the taxonomy document. The text was agreed verbatim with Martin Franc
and is published as agreed, so a second hand-written copy on a second page is not
a formatting detail -- it is a disclosure that can drift out of agreement with the
party it was agreed with, silently, on whichever page nobody re-read. Two copies
of a negotiated text is the same defect as two copies of a credential.

Do not edit the wording here to fit a layout. If it has to change, it changes with
Martin first, and it changes once.
"""

from __future__ import annotations

DISCLOSURE_TITLE = "Dimension co-authorship and conflict disclosure"

#: Verbatim, as agreed. Paragraphs, in order.
DISCLOSURE_PARAGRAPHS = [
    "Several dimensions in this benchmark were co-developed with Inquio (Martin Franc), drawing "
    "on failure patterns observed in their production deployments and in Aivonic Labs' own.",

    "While that co-authorship is active, Inquio does not appear on the Proving Ground leaderboard "
    "and is not scored by this benchmark. If that changes, this disclosure is updated and "
    "published before any Inquio score is shown.",

    "The taxonomy publishes named patterns and the probes that detect them. It does not publish "
    "frequency or prevalence rates. Frequencies observed in either party's deployments come from "
    "client environments and do not generalise, and presenting them as rates would misrepresent "
    "both the data and its scope.",

    "Proving Ground is measured by its own dimensions. Where a dimension applies to a scoring "
    "system, the benchmark's own result is published alongside it.",
]


def as_markdown() -> str:
    return "\n\n".join([f"## {DISCLOSURE_TITLE}", *DISCLOSURE_PARAGRAPHS])


def as_plain_text() -> str:
    return "\n\n".join([DISCLOSURE_TITLE, *DISCLOSURE_PARAGRAPHS])


def as_html(*, heading_level: str = "h2", section_class: str = "disclosure") -> str:
    """The disclosure as a standalone section.

    Rendered with NO link to the taxonomy page: that page is not published yet, and
    a disclosure whose first outbound reference 404s reads as carelessness about
    exactly the thing it is being careful about. The link is added when the taxonomy
    ships, not before.
    """
    import html as _h

    # No max-width on the paragraphs. The .lb-wrap CONTAINER carries the page
    # width, and a prose cap inside it is the defect the prose-width gate exists
    # for -- it ends a paragraph short of the boxes beside it for no reason a
    # reader can see. Raised four separate times before it was made structural.
    paras = "".join(
        f'<p class="lb-note" style="margin-top:14px;">{_h.escape(p)}</p>'
        for p in DISCLOSURE_PARAGRAPHS
    )
    return (
        f'<section class="{section_class}" style="border-top:1px solid var(--rule);padding-top:36px;">'
        '<div class="lb-wrap">'
        '<span class="eyebrow">Disclosure</span>'
        f'<{heading_level} style="font-size:clamp(1.4rem,2.4vw,1.9rem);margin:12px 0 0;font-weight:400;">'
        f'{_h.escape(DISCLOSURE_TITLE)}</{heading_level}>'
        f'{paras}'
        '</div></section>'
    )
