"""Canonicals and sitemap entries must name URLs that actually serve the page.

⛔ WHY: A CANONICAL POINTING AT A REDIRECT IS SELF-CONTRADICTORY.

/methodology declared `canonical: https://theprovingground.io/methodology.html`,
and nginx 301s that to /methodology. So the page told Google "the real address of
this content is somewhere else" and that somewhere else immediately said "no, it
is back here". Google is entitled to resolve that by indexing neither. The
sitemap listed the same redirecting URL, which Search Console reports as "page
with redirect" rather than indexing it.

Neither shows up as an error anywhere. The page returns 200, the redirect works,
the sitemap is valid XML. It is only wrong in a way that costs you search
presence silently, which is exactly when a test earns its place.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://theprovingground.io"

# Paths nginx rewrites. Anything canonical or sitemap must avoid the left side.
REDIRECTS = {"/methodology.html": "/methodology", "/report": "/scorecards/"}


def _sitemap_locs() -> list[str]:
    x = ET.fromstring((ROOT / "frontend/sitemap.xml").read_text())
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [e.text.strip() for e in x.findall(".//s:loc", ns)]


def test_sitemap_is_valid_and_not_empty():
    locs = _sitemap_locs()
    assert locs, "sitemap parsed to zero URLs; every assertion below would pass vacuously"
    for u in locs:
        assert u.startswith(BASE + "/"), f"{u} is not on the canonical domain"


def test_no_sitemap_entry_points_at_a_redirect():
    for u in _sitemap_locs():
        path = u[len(BASE):]
        assert path not in REDIRECTS, (
            f"sitemap lists {path}, which 301s to {REDIRECTS.get(path)}. Search Console "
            f"reports that as 'page with redirect' instead of indexing it.")


@pytest.mark.parametrize("rel", ["frontend/index.html", "frontend/methodology.html",
                                 "frontend/cohort.html", "frontend/leaderboard.html"])
def test_canonical_is_present_and_does_not_point_at_a_redirect(rel):
    p = ROOT / rel
    if not p.exists():
        pytest.skip(f"{rel} not generated")
    m = re.search(r'rel="canonical"\s+href="([^"]+)"', p.read_text(errors="replace"))
    assert m, f"{rel} declares no canonical; on a domain that moved, that is how a page gets dropped"
    url = m.group(1)
    assert url.startswith(BASE), f"{rel} canonical {url} is not on the canonical domain"
    path = url[len(BASE):]
    assert path not in REDIRECTS, (
        f"{rel} declares canonical {path}, which 301s to {REDIRECTS.get(path)}. The page "
        f"says its real address is elsewhere and that address sends the reader back.")


def test_the_cohort_page_states_the_number_it_actually_shows():
    """The word "five" was hardcoded four times - title, meta description, H1 and
    body. Withholding one platform made every one of them false at once."""
    p = ROOT / "frontend/cohort.html"
    if not p.exists():
        pytest.skip("cohort not generated")
    s = p.read_text(errors="replace")
    names = ("Dify", "Typebot", "CrewAI", "Flowise", "Onyx", "SPARK")
    shown = sum(1 for n in names if re.search(rf"\b{n}\b", s))
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    assert shown, "no platform columns found; the check cannot see the page"
    w = words[shown]
    assert f"One agent, {w} platforms" in s, (
        f"the page renders {shown} platforms but its title does not say {w!r}")
