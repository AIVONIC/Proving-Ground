"""A published scorecard never prints a held-out probe id.

Until 2026-09-26 every card printed the id of each probe it showed, and the cards
are linked from the public board, while the public repo is forbidden to name even
one. A card now shows an opaque reference (dimension code + suite position) that a
vendor can quote to dispute a result and that says nothing without the suite.
"""

from __future__ import annotations

import json

import pytest

import app.leaderboard.report as rp
from app.dimensions.catalog import REGISTRY


@pytest.fixture
def suite(tmp_path, monkeypatch):
    (tmp_path / "security_practice.json").write_text(json.dumps(
        {"probes": [{"id": "sec_secret_name_one"}, {"id": "sec_secret_name_two"}]}))
    monkeypatch.setattr(rp, "PRIVATE_SUITES", tmp_path)
    monkeypatch.setattr(rp, "_PROBE_INDEX", None)


def test_every_graded_dimension_has_a_reference_code():
    assert set(rp.PROBE_REF_CODES) == set(REGISTRY)
    assert len(set(rp.PROBE_REF_CODES.values())) == len(rp.PROBE_REF_CODES)


def test_reference_is_dimension_code_and_suite_position(suite):
    assert rp.probe_ref("security", "sec_secret_name_two") == "SEC-002"


def test_a_probe_outside_the_held_out_suite_refuses_rather_than_printing_its_id(suite):
    with pytest.raises(SystemExit) as e:
        rp.probe_ref("security", "sec_not_in_suite")
    assert "sec_not_in_suite" not in str(e.value)


def test_the_rendered_row_carries_the_reference_and_never_the_id(suite):
    runs = [{"security": [{"probe_id": "sec_secret_name_one", "score": 0.2,
                           "category": "adversarial", "reason": "r", "response": "x"}]}]
    row = rp.probe_rollup(runs, "security")[0]
    out = rp.probe_html(row)
    assert "SEC-001" in out and "sec_secret_name_one" not in out


# --- one scorecard per agent, the latest grade (Christian, 2026-09-26) -------------

def test_a_superseded_card_shows_no_score_no_date_and_points_at_the_current_card():
    import re
    stub = rp.redirect_stub("spark-b12e7ee36141")
    assert rp.SUPERSEDED_MARKER in stub
    assert "url=/scorecards/spark-b12e7ee36141" in stub
    assert not re.search(r"\b\d{2}\.\d{1,2}\b", stub)          # no composite
    assert not re.search(r"\d{4}-\d{2}-\d{2}", stub)            # no grade date


def test_the_board_never_links_a_stub_as_a_card(tmp_path):
    from app.leaderboard.render import card_slugs
    e = {"id": "spark", "graded_at": "2026-09-18", "composite": 88.68,
         "run_artifact": "spark_n5.json"}                       # its exact card is absent
    (tmp_path / "spark-ffffffffffff.html").write_text(rp.redirect_stub("spark-x"))
    (tmp_path / "spark-000000000000.html").write_text("<html>a real older card</html>")
    assert card_slugs([e], tmp_path) == {"spark": "spark-000000000000"}
