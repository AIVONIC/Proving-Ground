"""A guard layer in front of a graded agent must be RECORDED, and never guessed.

On 2026-09-26 SPARK turned out to reach every user message through a
prompt-injection guard hardcoded in its code. In the two artifacts behind its
board entry the guard intercepted two thirds of security probe turns before the
model saw them, while the reference builds it is ranked against have no guard.
Nothing in the artifact said so.

What is pinned here:
  1. An agent we cannot observe reports NOT OBSERVABLE, never "none".
  2. A declaration is labelled as a declaration, not as a measurement.
  3. `in_path` needs wired AND running AND reachable-from-the-agent, because a
     guard that is down fails OPEN: configured is not in the path.
  4. A probe that did not complete says so instead of returning a verdict.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import app.graded_env as ge

SPEC = {"ssh": "op@host", "container": "agent-x", "code_path": "/app/main.py",
        "guard_container": "guard", "guard_url": "http://guard:8000"}


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    f = tmp_path / "hosted_agents.json"
    f.write_text(json.dumps({"ours": SPEC,
                             "ref": {"declared": "none", "basis": "operator-built"}}))
    monkeypatch.setattr(ge, "HOSTED_FILE", f)
    return f


def _ssh_returns(monkeypatch, stdout, rc=0):
    monkeypatch.setattr(ge.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=rc))


def test_missing_hosting_file_is_not_observable_never_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ge, "HOSTED_FILE", tmp_path / "absent.json")
    g = ge._guard("ours")
    assert g["observed"] is False and "declared" not in g and "in_path" not in g


def test_third_party_agent_is_not_observable_never_none(hosted):
    g = ge._guard("vendor-agent")
    assert g["observed"] is False and "declared" not in g and "in_path" not in g
    assert "not observable" in g["reason"]


def test_declaration_is_labelled_as_a_declaration(hosted):
    g = ge._guard("ref")
    assert g == {"observed": False, "declared": "none", "basis": "operator-built"}


def test_guard_wired_running_reachable_is_in_path(hosted, monkeypatch):
    _ssh_returns(monkeypatch, "IMG=guard:v3|sha256:abc|true\nWIRED=1\nHEALTH=200\n")
    g = ge._guard("ours")
    assert g["observed"] is True and g["in_path"] is True
    assert g["guard_image"] == "guard:v3" and g["guard_image_id"] == "sha256:abc"


@pytest.mark.parametrize("stdout, failing", [
    ("IMG=guard:v3|sha256:abc|false\nWIRED=1\nHEALTH=200\n", "guard_running"),
    ("IMG=guard:v3|sha256:abc|true\nWIRED=1\nHEALTH=\n", "reachable_from_agent"),
    ("IMG=guard:v3|sha256:abc|true\nWIRED=0\nHEALTH=200\n", "wired_in_agent_code"),
])
def test_any_missing_leg_means_not_in_path_because_a_down_guard_fails_open(
        hosted, monkeypatch, stdout, failing):
    _ssh_returns(monkeypatch, stdout)
    g = ge._guard("ours")
    assert g["observed"] is True and g[failing] is False and g["in_path"] is False


@pytest.mark.parametrize("stdout, rc", [
    ("IMG=guard:v3|sha256:abc|true\nWIRED=1\nHEALTH=200\n", 255),  # ssh failed
    ("IMG=guard:v3|sha256:abc|true\n", 0),                          # truncated
])
def test_incomplete_probe_reports_unresolved_not_a_verdict(hosted, monkeypatch, stdout, rc):
    _ssh_returns(monkeypatch, stdout, rc)
    g = ge._guard("ours")
    assert g["observed"] is False and "unresolved" in g and "in_path" not in g


def test_capture_always_carries_the_guard_field(hosted):
    assert "guard" in ge.capture("vendor-agent")


# --- the public half: promote -> entry -> card / scorecard ------------------------

from app.leaderboard.promote import guard_disclosure  # noqa: E402
from app.leaderboard.render import card, guard_note  # noqa: E402

MARK = "GUARD-REFUSAL"


def _run(guard, responses):
    return {"graded_env": {"guard": guard},
            "runs": [{"security": [{"response": r} for r in responses]}]}


def test_promote_publishes_a_ratio_never_counts():
    g = guard_disclosure(_run({"observed": True, "in_path": True, "refusal_marker": MARK},
                              [MARK, MARK, "ok"]))
    assert g == {"in_path": True, "basis": "observed at grade time",
                 "security_share_intercepted": 0.67}


def test_refusals_in_transcripts_prove_the_path_even_if_the_guard_was_down_at_the_end():
    g = guard_disclosure(_run({"observed": True, "in_path": False, "refusal_marker": MARK},
                              [MARK, "ok"]))
    assert g and g["in_path"] is True


def test_a_fresh_observation_of_no_guard_overrides_the_previous_entry():
    run = _run({"observed": True, "in_path": False, "refusal_marker": MARK}, ["ok"])
    assert guard_disclosure(run, {"guard": {"in_path": True}}) is None


def test_an_unobserved_grade_keeps_the_previous_disclosure_rather_than_dropping_it():
    prev = {"guard": {"in_path": True, "basis": "x"}}
    assert guard_disclosure({"runs": []}, prev) == prev["guard"]


def test_note_is_empty_without_a_guard_and_never_names_the_guard():
    assert guard_note({}) == "" and guard_note({"guard": {"in_path": False}}) == ""
    e = {"guard": {"in_path": True, "security_share_intercepted": 0.67,
                   "guard_image": "prompt-guard:v3"}}
    assert "67%" in guard_note(e, long=True) and "67%" not in guard_note(e)
    assert "prompt-guard" not in guard_note(e, long=True)


def test_the_card_carries_the_note_only_for_a_guarded_entry():
    base = {"id": "a", "name": "A", "composite": 88.0, "tier": "Premium",
            "subscores": {}, "self_operated": True}
    assert "prompt-injection classifier" not in card(0, base)
    assert "prompt-injection classifier" in card(0, {**base, "guard": {"in_path": True}})


def test_the_board_links_the_card_for_THIS_grade_even_when_an_older_one_sorts_last(tmp_path):
    from app.leaderboard.render import card_slugs
    from app.leaderboard.report import slug_for
    e = {"id": "spark", "graded_at": "2026-09-18", "composite": 88.68, "run_artifact": "spark_n5.json"}
    exact = slug_for(e, e["run_artifact"])
    stale = "spark-ffffffffffff"                      # sorts after any real token
    assert stale > exact
    for s in (exact, stale):
        (tmp_path / f"{s}.html").write_text("x")
    assert card_slugs([e], tmp_path) == {"spark": exact}
    (tmp_path / f"{exact}.html").unlink()             # no current card: falls back, loudly
    assert card_slugs([e], tmp_path) == {"spark": stale}
