"""The certificate contract, exercised against the production routes.

⛔ WHY EVERY CASE HERE IS A SEPARATE TEST RATHER THAN ONE HAPPY PATH.

A certificate's whole value is that it answers honestly when the answer is
unflattering. The valid case is the one that cannot break unnoticed - a vendor
checks it the day they get it. What breaks silently is expired-vs-unknown, and
the failure is invisible: if an expired certificate 404s, nothing errors, no
page looks wrong, and a buyer simply cannot tell "this vendor's grade lapsed"
from "I mistyped the code". That is the defect these tests exist to catch.

These drive the REAL app object, not a reimplementation of its logic - a test
that re-derives the status would prove the re-derivation works.
"""
from __future__ import annotations

import datetime
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    """A certificate index containing every state we need to assert on."""
    from app.leaderboard.certs import build
    from app.leaderboard.store import load

    data = build(load())
    assert data["certificates"], "no graded entries; this suite would pass vacuously"

    # A real cert, backdated past its own validity window. Backdating rather than
    # freezing the clock keeps the production expiry arithmetic under test.
    src = next(iter(data["certificates"].values()))
    old = datetime.date.today() - datetime.timedelta(days=int(src["validity_days"]) + 30)
    data["certificates"]["pg-expired1"] = {**src, "code": "pg-expired1",
                                           "agent": "Backdated", "graded_at": old.isoformat()}
    d = tempfile.mkdtemp()
    path = os.path.join(d, "certs.json")
    json.dump(data, open(path, "w"))
    monkeypatch.setenv("PG_CERTS_PATH", path)

    import importlib
    import app.api.signup_service as svc
    importlib.reload(svc)
    c = TestClient(svc.app)
    c.valid = src["code"]
    c.all_certs = data["certificates"]
    return c


def test_valid_certificate_resolves_with_its_grade(client):
    j = client.get(f"/verify/{client.valid}.json")
    assert j.status_code == 200
    b = j.json()
    assert b["status"] == "current"
    assert b["composite"] == client.all_certs[client.valid]["composite"]
    assert b["days_remaining"] > 0


def test_expired_certificate_is_200_and_keeps_its_grade(client):
    """THE LOAD-BEARING ONE. A 404 here is indistinguishable from a typo."""
    j = client.get("/verify/pg-expired1.json")
    assert j.status_code == 200, "an expired certificate must not 404"
    b = j.json()
    assert b["status"] == "expired"
    assert b["composite"] is not None, "an expired grade is still a real measurement"
    assert b["days_remaining"] == 0


def test_unknown_code_says_how_it_differs_from_expired(client):
    j = client.get("/verify/pg-nosuch01.json")
    assert j.status_code == 404
    assert j.json()["status"] == "unknown"
    assert "expired" in j.json()["detail"], (
        "an unknown code must name the distinction, or the status code is the only "
        "signal and we are back to the ambiguity this design removes")


def test_unknown_code_html_is_a_real_page(client):
    r = client.get("/verify/pg-nosuch01")
    assert r.status_code == 404
    assert "No certificate has this code" in r.text


def test_badge_is_svg_and_cache_bounded(client):
    r = client.get(f"/badge/{client.valid}.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg")
    # A badge cached indefinitely cannot expire, which defeats the mechanism.
    assert "max-age" in r.headers.get("cache-control", "")


def test_badge_distinguishes_current_from_expired(client):
    assert "expired" in client.get("/badge/pg-expired1.svg").text
    assert "expired" not in client.get(f"/badge/{client.valid}.svg").text


@pytest.mark.parametrize("path", [
    "/badge/{valid}.svg", "/badge/pg-expired1.svg", "/badge/pg-nosuch01.svg"])
def test_every_badge_actually_parses_as_xml(client, path):
    """⛔ WRITTEN BECAUSE THE STRING TEST ABOVE PASSED ON A BROKEN IMAGE.

    SVG is served as image/svg+xml and therefore parsed as XML, where the only
    defined entities are &lt; &gt; &amp; &quot; &apos;. A stray `&mdash;` makes
    the whole document a parse error, so the badge renders as a broken image -
    while `assert "capped" in text` passes, because the string is right there.
    Asserting on the ARTEFACT means parsing it the way a browser does.
    """
    import xml.etree.ElementTree as ET
    r = client.get(path.format(valid=client.valid))
    assert r.status_code == 200
    ET.fromstring(r.text)      # raises on an undefined entity or malformed markup


def test_capped_badge_parses_and_carries_the_cap(client):
    capped = [c for c in client.all_certs.values() if c.get("capped")]
    if not capped:
        pytest.skip("no capped entry on the board right now")
    import xml.etree.ElementTree as ET
    r = client.get(f"/badge/{capped[0]['code']}.svg")
    ET.fromstring(r.text)
    assert "capped" in r.text


def test_badge_for_unknown_code_renders_rather_than_erroring(client):
    """An <img> pointing at a 404 shows a broken image; say "no certificate"."""
    r = client.get("/badge/pg-nosuch01.svg")
    assert r.status_code == 200 and "no certificate" in r.text


def test_capped_grade_discloses_the_cap_everywhere_it_appears(client):
    capped = [c for c in client.all_certs.values() if c.get("capped")]
    if not capped:
        pytest.skip("no capped entry on the board right now")
    c = capped[0]
    j = client.get(f"/verify/{c['code']}.json").json()
    assert j["capped"] and j["capped_from"] > j["cap"]
    page = client.get(f"/verify/{c['code']}").text
    assert "capped at" in page and str(j["capped_from"]) in page, (
        "publishing a capped composite without its mechanism is a damaging claim "
        "by omission; the board says so and a certificate must not say less")
    assert "capped" in client.get(f"/badge/{c['code']}.svg").text


def test_corrupt_index_mid_life_falls_back_to_last_good(client):
    """A bad deploy against a warm process costs nothing."""
    assert client.get(f"/verify/{client.valid}.json").status_code == 200   # warm it
    open(os.environ["PG_CERTS_PATH"], "w").write("{ not json")
    os.utime(os.environ["PG_CERTS_PATH"], (0, 0))
    assert client.get(f"/verify/{client.valid}.json").json()["status"] == "current"


def test_unreadable_index_from_cold_is_unavailable_not_unknown(client, monkeypatch):
    """⛔ THE ONE OUR OWN TEST CAUGHT.

    A process that has never read the index must not answer "no certificate has
    this code" - that publishes our outage as a finding about a vendor, in the
    exact place a buyer goes to check them. It has to say it cannot check.
    """
    import importlib
    monkeypatch.setenv("PG_CERTS_PATH", "/nonexistent/certs.json")
    import app.api.signup_service as svc
    importlib.reload(svc)
    cold = TestClient(svc.app)

    j = cold.get(f"/verify/{client.valid}.json")
    assert j.status_code == 503, "unreadable index must be 503, never 404"
    assert j.json()["status"] == "unavailable"
    assert "fault on our side" in j.json()["detail"]
    assert j.headers["cache-control"] == "no-store", "never cache a wrong answer"

    h = cold.get(f"/verify/{client.valid}")
    assert h.status_code == 503
    assert "Cannot verify right now" in h.text
    assert "made up" not in h.text, "must not accuse a real certificate of being fabricated"

    b = cold.get(f"/badge/{client.valid}.svg")
    assert b.status_code == 200, "an <img> must still render, not break"
    assert "checking" in b.text and b.headers["cache-control"] == "no-store"


def test_code_is_stable_across_regrades(client):
    """The scorecard slug seeds on the grade and MUST change per run; the
    certificate code must not, or a mark embedded on a vendor's site breaks the
    day their score improves."""
    from app.leaderboard.certs import code_for
    from app.leaderboard.report import slug_for
    e = {"id": "demo", "graded_at": "2026-01-01", "composite": 80.0}
    e2 = {**e, "graded_at": "2026-06-01", "composite": 91.0}
    assert code_for(e["id"]) == code_for(e2["id"])
    assert slug_for(e, "runs/a.json") != slug_for(e2, "runs/b.json")


def test_certificate_page_honours_the_same_themes_as_the_site(client):
    """The site is dark by default and light under prefers-color-scheme: light.
    The certificate shipped dark-only, so a visitor on a light OS got a dark
    certificate from an otherwise light site - and this is the page most likely
    to be opened in a second tab beside the board.
    """
    import re
    css = client.get(f"/verify/{client.valid}").text
    for pat, what in [(r':root\{[^}]*--ground:#0f1518', "dark on bare :root"),
                      (r'@media\(prefers-color-scheme:light\)', "light under a light OS"),
                      (r':root\[data-theme="light"\]', "explicit light stamp"),
                      (r':root\[data-theme="dark"\]', "explicit dark stamp")]:
        assert re.search(pat, css), f"certificate page is missing: {what}"

    # A token defined ONLY inside a media block does not exist un-stamped, which
    # renders one theme's text on the other theme's ground.
    root = css[css.index(":root{"):css.index("@media(prefers-color-scheme")]
    defined = set(re.findall(r'(--[a-z-]+):', root))
    used = set(re.findall(r'var\((--[a-z-]+)\)', css))
    assert not (used - defined), f"tokens used but undefined on bare :root: {used - defined}"


def test_no_hardcoded_colour_is_written_into_a_style_attribute(client):
    """A colour chosen in Python lands in a style attribute and cannot follow the
    theme - it is decided before the browser knows which palette applies."""
    import re
    page = client.get(f"/verify/{client.valid}").text
    stray = sorted(set(re.findall(r'style="[^"]*?(#[0-9a-fA-F]{6})', page)))
    assert not stray, f"literal hex in a style attribute: {stray}"


def test_the_badge_keeps_literal_colours(client):
    """The opposite rule, and the reason is the opposite too: a badge renders
    inside a THIRD PARTY's page, where our tokens do not exist and their theme is
    not ours to follow. var() there would resolve to nothing."""
    import re
    svg = client.get(f"/badge/{client.valid}.svg").text
    assert re.search(r'#[0-9a-fA-F]{6}', svg), "badge must carry literal colours"
    assert "var(--" not in svg, "a badge cannot reference our CSS variables"


def test_both_the_code_and_the_readable_alias_resolve(client):
    """Two addresses, one certificate. The alias is what anyone pastes; the code
    is already printed on scorecards that have been sent, so it must never stop
    working."""
    from app.leaderboard.certs import build
    from app.leaderboard.store import load
    certs = build(load())["certificates"]
    c = certs[client.valid]
    alias = c["alias"]
    by_code = client.get(f"/verify/{c['code']}.json").json()
    by_alias = client.get(f"/verify/{alias}.json").json()
    assert by_code["code"] == by_alias["code"] == c["code"]
    assert by_code["composite"] == by_alias["composite"]
    assert client.get(f"/badge/{alias}.svg").status_code == 200


def test_the_alias_names_the_build_not_the_vendor(client):
    """⛔ /verify/dify would read as "Dify is verified here", and every agent on
    this board is our own reference build. The alias is the agent id, which names
    the build: dify-northwind. When a VENDOR submits their own agent its id is
    theirs and the bare name becomes correct - gate on ownership, never on how the
    URL looks."""
    from app.leaderboard.certs import build
    from app.leaderboard.store import load
    for e in load():
        if not e.get("reference"):
            continue
        alias = e["id"]
        vendor_word = e["name"].lower()
        assert alias.lower() != vendor_word, (
            f"reference build {e['name']} has alias {alias!r}, which reads as a "
            f"claim about the vendor's own product")


def test_a_grade_records_which_scoring_config_produced_it(client):
    """⛔ scoring/version.py was written, committed, described as landed - and
    imported by NOTHING. Every grade produced since carried no scoring identity,
    so comparable() could never return anything but unknown. A version nobody
    stamps does not exist.

    The id belongs on the ARTIFACT at scoring time: that is the only moment the
    configuration that produced the number is unambiguously the one in force.
    Stamping it at promote time would record whatever the config happens to be
    then, which is the false equivalence it exists to prevent.
    """
    import inspect
    from app import grade as grade_mod
    src = inspect.getsource(grade_mod)
    assert "composite_id()" in src, (
        "grade.py does not stamp composite_id; the identifier is inert and every "
        "grade it writes is uncomparable by construction")
    from app.leaderboard import promote
    assert "composite_id" in inspect.getsource(promote), "promote drops the id"


def test_the_certificate_says_when_a_score_is_not_comparable(client):
    """A certificate is exactly where two numbers get put side by side."""
    page = client.get(f"/verify/{client.valid}").text
    j = client.get(f"/verify/{client.valid}.json").json()
    assert "composite_id" in j, "certificate JSON omits the scoring config"
    if j.get("composite_id"):
        assert j["composite_id"] in page
    else:
        assert "not directly comparable" in page, (
            "a grade with no scoring id must SAY it is not comparable, not stay silent")
    assert "comparable only when they carry the same scoring config" in page
