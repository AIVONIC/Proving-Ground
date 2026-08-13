"""The lander's hero scorecard must never drift from the graded entries.

This is the guard for a real incident: the card originally held invented figures,
and the first fix replaced them with real ones BY HAND -- which only moves the
drift one regrade into the future. These tests fail the moment the published
lander disagrees with `entries.json`, and they also pin the fail-loud behaviour,
because a sync that silently matches nothing is the failure it exists to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.leaderboard.render import DIMS
from app.leaderboard.store import load
from app.leaderboard.sync_lander import pick, scores_list, sync, sync_bundle

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

# A bundle is every file one page's scorecard numbers live in. index.html keeps
# its scores in app.js so the page carries no inline script.
BUNDLES = [
    [FRONTEND / "index.html", FRONTEND / "app.js"],
    [FRONTEND / "standalone.html"],
]
LANDERS = [FRONTEND / "index.html", FRONTEND / "standalone.html"]


@pytest.fixture(scope="module")
def featured() -> dict:
    return pick(load(), None)


@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda b: b[0].name)
def test_lander_matches_entries(bundle: list[Path], featured: dict) -> None:
    """The published files are exactly what the generator would produce."""
    before = {str(p): p.read_text() for p in bundle}
    assert sync_bundle(before, featured) == before, (
        f"{bundle[0].name} is out of date with entries.json. Regenerate with:\n"
        f"  python -m app.leaderboard.sync_lander --bundle "
        f"{','.join(p.name for p in bundle)}"
    )


@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda b: b[0].name)
def test_scores_array_is_the_graded_numbers(bundle: list[Path], featured: dict) -> None:
    """Belt and braces: read the numbers straight out of the shipped files, so the
    test still means something if sync() itself is wrong."""
    found = [re.search(r"var SCORES = \[([^\]]+)\]", p.read_text()) for p in bundle]
    hits = [m for m in found if m]
    assert len(hits) == 1, f"expected exactly one SCORES array in {[p.name for p in bundle]}"
    assert [float(x) for x in hits[0].group(1).split(",")] == scores_list(featured)


@pytest.mark.parametrize("path", LANDERS, ids=lambda p: p.name)
def test_no_placeholder_claims(path: Path) -> None:
    """The page must not describe real grades as illustrative, or a launched site
    as unlaunched. Both claims were live for weeks."""
    text = path.read_text().lower()
    for phrase in ("illustrative example", "figures shown are illustrative",
                   "not yet publicly launched"):
        assert phrase not in text, f"{path.name} still claims: {phrase!r}"


def test_scores_follow_the_radar_axis_order(featured: dict) -> None:
    subs = featured["subscores"]
    assert scores_list(featured) == [subs[key] for _, key, _ in DIMS]


def test_sync_raises_when_markup_moved(featured: dict) -> None:
    """If someone renames the card's markup, the sync must fail loudly rather
    than return the input unchanged and report success."""
    with pytest.raises(SystemExit, match="exactly 1"):
        sync("<html><body>no scorecard here</body></html>", featured)


def test_sync_raises_when_an_anchor_is_duplicated(featured: dict) -> None:
    """Two files in a bundle both carrying the card is equally wrong: the sync
    would patch one and leave the other asserting stale numbers."""
    one = (FRONTEND / "standalone.html").read_text()
    with pytest.raises(SystemExit, match="exactly 1"):
        sync_bundle({"a.html": one, "b.html": one}, featured)


def test_pick_defaults_to_the_self_operated_agent() -> None:
    """The lander features our own agent on purpose: the pitch is that we publish
    our own grade, so the hero card must not silently become someone else's."""
    assert pick(load(), None).get("self_operated") is True


def test_index_carries_no_inline_script() -> None:
    """index.html must stay free of inline <script> so the CSP can be
    script-src 'self' with no hashes to drift out of sync. Structured data
    (application/ld+json) is not executable and is not subject to script-src."""
    html = (FRONTEND / "index.html").read_text()
    inline = re.findall(r"<script(?![^>]*(?:ld\+json|src=))[^>]*>", html)
    assert not inline, f"index.html has inline script(s): {inline}"
    assert '<script src="/app.js" defer></script>' in html
