"""The published taxonomy page must be exactly the generator's output.

It is a joint page with Inquio (Martin Franc) that he reviewed, so any change has to
come from its sources (data/taxonomy/*.json, the dimension catalog), never a hand
edit of the HTML. On 2026-09-27 a one-sentence hand edit wrote raw apostrophes the
generator escapes, and a regeneration with the wrong --lander looked like layout
drift that did not exist. This pins both.
"""
from pathlib import Path

from app.leaderboard.taxonomy_page import render_html, render_markdown

REPO = Path(__file__).resolve().parents[2]


def test_the_committed_page_is_the_generator_output():
    lander = (REPO / "frontend" / "leaderboard.html").read_text()
    assert render_html(lander) == (REPO / "frontend" / "taxonomy.html").read_text(), (
        "taxonomy.html differs from the generator. Regenerate it (see taxonomy_page.py "
        "docstring); never hand-edit the published joint page.")


def test_the_committed_markdown_is_the_generator_output():
    assert render_markdown() == (REPO / "TAXONOMY.md").read_text()
