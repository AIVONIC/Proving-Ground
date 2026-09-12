"""No prose element carries its own max-width. The CONTAINER is the measure.

⛔ THIS EXISTS BECAUSE THE SAME COMPLAINT WAS RAISED FOUR TIMES.

Each earlier fix widened one element, so it kept coming back: the defect is
structural, not cosmetic. Prose was capped per element while the box around it
was sized independently, so a paragraph ended hundreds of pixels short of its
own container and the gap read as a mistake - which it was. The worst case was
`p { max-width: var(--measure) }` on methodology.html: 72ch = 612px of text
inside a 1104px container, 45% of every line empty, next to callout boxes and
lists running full width. Three paragraphs on that page had already been
patched with inline `style="max-width:none"` - a workaround for a rule nobody
had gone back to fix.

The rule this file enforces: a prose CONTAINER may carry a max-width; the prose
inside it may not. Then a paragraph, a list and a callout all end on the same
right edge and there is nothing to notice.

Containers are allowlisted below by name, with the width each one is for. A new
container width is a deliberate layout decision and belongs in that list; a new
cap on a paragraph is the bug.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PAGES = ["frontend/index.html", "frontend/methodology.html", "frontend/cohort.html",
         "frontend/standalone.html", "frontend/404.html"]
GENERATORS = ["backend/app/leaderboard/render.py", "backend/app/leaderboard/report.py",
              "backend/app/leaderboard/cohort.py", "backend/app/api/signup_service.py"]

# Layout containers. A max-width here is a page-frame decision, not a prose cap.
CONTAINERS = {
    ".wrap", ".lb-wrap", ".co-wrap", ".rp-wrap", ".doc", ".box", ".sec-head",
    ".final .card", ".sc-figure", ".ea-form", ".card", ".panel",
}
# Selectors that name prose. A max-width on any of these is the defect.
PROSE_TOKENS = ("p", "li", "lead", "sub", "note", "cap", "desc", "disclaimer",
                "omitted", "panelnote", "measure", "foot", "blockquote")

RULE = re.compile(r'([^{};]{1,140}?)\s*\{([^{}]*?max-width\s*:\s*([^;}]+?)\s*[;}])', re.S)


def _rules(text: str):
    for m in RULE.finditer(text):
        sel = " ".join(m.group(1).split()).split("*/")[-1].strip()
        if not sel or "@" in sel or sel.startswith("/*"):
            continue
        yield sel, m.group(3).strip()


def _is_prose(sel: str) -> bool:
    last = sel.split()[-1].split(":")[0].split(",")[0]
    if last in CONTAINERS or sel in CONTAINERS:
        return False
    if any(c in CONTAINERS for c in (last, "." + last.lstrip("."))):
        return False
    name = last.lstrip(".#").lower()
    return name in ("p", "li", "ul", "ol", "dd", "blockquote") or any(
        t in name for t in PROSE_TOKENS if len(t) > 2)


@pytest.mark.parametrize("rel", PAGES + GENERATORS)
def test_no_prose_element_caps_its_own_width(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    offenders = [(s, v) for s, v in _rules(text)
                 if _is_prose(s) and v not in ("none", "100%", "fit-content")]
    assert not offenders, (
        f"{rel}: prose elements must fill their container, not cap themselves. "
        f"Offenders: {offenders}. If this is genuinely a layout container, add it "
        f"to CONTAINERS with the width it is for.")


def test_the_global_p_cap_on_methodology_stays_gone():
    """The single worst instance, called out by name so a re-add is loud."""
    text = (ROOT / "frontend/methodology.html").read_text(encoding="utf-8")
    assert not re.search(r'\bp\s*\{[^}]*max-width', text), \
        "a global p{max-width} caps EVERY paragraph on the page"


def test_no_paragraph_needs_an_inline_override():
    """`style="max-width:none"` on a paragraph means a rule above it is wrong.
    Three of these existed on methodology.html as workarounds for the global cap."""
    for rel in PAGES:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert "max-width:none" not in text.replace(" ", ""), (
            f"{rel}: an inline max-width:none override means a prose cap is still "
            f"in the stylesheet; remove the cap instead of overriding it per element")


@pytest.mark.parametrize("rel", PAGES + GENERATORS)
def test_the_audit_can_actually_see_rules(rel):
    """POSITIVE CONTROL. Every file above must yield at least one parsed rule, or
    an empty offender list proves only that the regex found nothing to read -
    which is how a clean pass gets reported on an unexamined file."""
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert list(_rules(text)), f"{rel}: parsed zero max-width rules; the check is blind here"


# --- head-to-head disclosure -------------------------------------------------
# Not a width test, but the same failure mode: the summary band at the top is
# what a visitor reads first and screenshots, and it was making a stronger claim
# than the data supports. Christian read the radar as "these agents are elite"
# and was right to - every outline sits in the outer quarter of a 0-10 axis.

def test_head_to_head_discloses_zero_tools_and_the_tie():
    from app.leaderboard.render import _radar_cap
    from app.leaderboard.store import load
    e = [x for x in load() if x.get("ranked", True)]
    cap = _radar_cap(e)
    assert "0 to 10" in cap, "the axis range must be stated; outlines sit in the outer quarter"
    assert "not how they compare" in cap.replace("&mdash;", "").replace("  ", " "), \
        "the radar must say it shows level, not comparison"
    assert "that close" in cap, "the clustered field must still be stated in words"
    if all(len(x.get("tools_verified") or x.get("tools") or []) == 0 for x in e):
        assert "zero executing tools" in cap
        assert "HANDLED" in cap, "Task must not read as 'completes tasks' for a tool-less agent"
        assert "Elite" in cap, "state that none of them reaches Elite, since the shape implies it"


def test_a_chart_answers_the_comparison_question_not_just_a_caption():
    """A caption explaining why a chart misleads is a workaround. The spread panel
    is the fix, and it must exist and be ordered by spread."""
    from app.leaderboard.render import DIM_SHORT, spread_svg
    from app.leaderboard.store import load
    import re
    import xml.etree.ElementTree as ET
    e = [x for x in load() if x.get("ranked", True)]
    svg = spread_svg(e)
    assert svg, "no spread chart rendered"
    ET.fromstring(svg.replace("var(--hair-strong)", "#333"))     # a broken SVG is an invisible one
    rows = re.findall(r'class="cmp-rowlabel">([^<]+)<', svg)
    assert len(rows) == len(DIM_SHORT), f"expected every dimension, got {rows}"
    spreads = []
    for d, short in DIM_SHORT.items():
        vals = [float(x["subscores"][d]) for x in e]
        spreads.append((short, max(vals) - min(vals)))
    expected = [r for r, _ in sorted(spreads, key=lambda t: -t[1])]
    assert rows == expected, (
        "rows must be ordered by spread, widest first - otherwise the chart buries "
        f"where the platforms diverge. got {rows}")
    # one dot per agent per row
    assert svg.count("<circle") == len(e) * len(DIM_SHORT)


def test_head_to_head_says_the_cohort_is_operator_built():
    from app.leaderboard.render import _cohort_band
    from app.leaderboard.store import load
    e = [x for x in load() if x.get("ranked", True)]
    band = _cohort_band(e)
    if all(x.get("reference") for x in e):
        assert "reference builds" in band
        assert "not those vendors" in band, (
            "presenting operator-built agents as a vendor ranking is a claim about "
            "other companies' products made by omission")
    # and it must RETRACT itself the moment a real third-party agent is entered
    mixed = [dict(e[0], reference=False)] + list(e[1:])
    assert _cohort_band(mixed) == "", "the band must disappear once a genuine entry exists"


def test_panels_sharing_a_grid_row_have_equal_figure_heights():
    """Captions must start on the same line in both columns of a row.

    The panels sit in a two-column grid, so the caption's vertical position is
    set by the figure above it. Unequal figure heights push one caption higher
    than the other and the two columns stop reading as a pair. Christian spotted
    it on row one; row two was worse (188 against 300) and nobody had noticed.

    These heights are derived, not hardcoded twice - the spread chart takes the
    radar's height and the bar chart takes the scatter's - so this asserts the
    derivation still holds rather than re-stating the numbers.
    """
    import re
    from app.leaderboard.render import (overlay_radar_svg, ranked_bars_svg,
                                        scatter_svg, spread_svg)
    from app.leaderboard.store import load
    e = [x for x in load() if x.get("ranked", True) and x.get("published", True)]
    assert len(e) >= 2, "needs at least two agents for the comparison band to render"

    def h(svg):
        assert svg, "panel rendered nothing; an empty figure cannot be aligned"
        return re.search(r'viewBox="[^"]* ([0-9.]+)"', svg).group(1)

    assert h(overlay_radar_svg(e)) == h(spread_svg(e)), (
        "row 1: the OVERLAY radar (what the head-to-head panel uses) and spread "
        "must match. Asserting on radar_svg here passed while the live page was "
        "misaligned, because the cards and the comparison band use different "
        "radar functions at different heights.")
    assert h(ranked_bars_svg(e)) == h(scatter_svg(e)), \
        "row 2: CI bars and scatter must be the same height"
