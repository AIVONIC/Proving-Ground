"""Every published page is a DOCUMENT, not a fragment.

⛔ THIS IS A REGRESSION GATE FOR A LIVE DEFECT.

The lander was served for some time as a bare fragment: no doctype, no <html>,
no <head>, no <body>, just content. Browsers render that fine, because HTML
parsers recover from almost anything -- which is exactly why nobody noticed. The
things that do not recover are the strict parsers: LinkedIn's preview fetcher
took it as malformed and would not build a card for the site. A benchmark whose
whole distribution model is people sharing a link had a link that would not
preview, and every page looked perfect in a browser.

It was repaired on the live server on 2026-09-16, and the repair did not come
back into the repository. The next deploy would have overwritten it with the
fragment again, silently, because rsync has no opinion about whether the file it
is copying is a valid document. That near-miss is why this is a test and not a
note in a runbook.

The page list is DERIVED from deploy.sh's manifest rather than hand-maintained
here. A hand-kept list covers the pages someone remembered; a new page added to
the manifest is exactly the one nobody would think to add to a list of pages to
check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy.sh"


def published_html_pages() -> list[Path]:
    """Repo-side sources of every .html the manifest publishes."""
    text = DEPLOY.read_text()
    block = re.search(r"MANIFEST=\((.*?)\n\)", text, re.S)
    assert block, "could not find the MANIFEST in deploy.sh; this test cannot see what is published"
    pages = []
    for line in block.group(1).splitlines():
        m = re.match(r'\s*"([^"]+):([^"]+)"', line)
        if not m:
            continue
        src = m.group(1)
        if src.endswith(".html"):
            pages.append(ROOT / "frontend" / src)
    return pages


def test_the_manifest_is_actually_readable():
    """A positive control. If the manifest cannot be parsed, every assertion below
    passes over an empty list and this file proves nothing."""
    pages = published_html_pages()
    assert len(pages) >= 4, f"only found {len(pages)} published pages; the manifest parse is wrong"
    assert any(p.name == "index.html" for p in pages)


@pytest.mark.parametrize("page", published_html_pages(), ids=lambda p: p.name)
def test_page_is_a_complete_document(page: Path):
    assert page.exists(), f"{page.name} is in the manifest but not in frontend/"
    html = page.read_text()
    head = html[:4096].lower()
    assert head.lstrip().startswith("<!doctype html"), (
        f"{page.name} does not begin with a doctype. Browsers recover from this; strict "
        f"parsers, including LinkedIn's preview fetcher, do not."
    )
    for tag in ("<html", "<head", "<body"):
        assert tag in html.lower(), f"{page.name} has no {tag}> element"
    for close in ("</head>", "</body>", "</html>"):
        assert close in html.lower(), f"{page.name} never closes with {close}"


@pytest.mark.parametrize("page", published_html_pages(), ids=lambda p: p.name)
def test_document_structure_is_not_duplicated(page: Path):
    """The other direction. Wrapping an already-wrapped page produces two <html>
    elements, which is malformed in a way that also survives a browser."""
    low = page.read_text().lower()
    assert low.count("<!doctype") == 1, f"{page.name} has {low.count('<!doctype')} doctypes"
    assert low.count("<html") == 1, f"{page.name} has {low.count('<html')} <html> elements"
    assert low.count("<body") == 1, f"{page.name} has {low.count('<body')} <body> elements"
