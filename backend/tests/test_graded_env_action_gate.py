"""An agent's ACTION GATE must be recorded when our probes do not pass through it.

On 2026-09-28 SPARK's SAGE gate was found to bypass synthetic sessions, and its own
`is_synthetic_session` classifies every session id this harness mints as synthetic.
A real visitor at SPARK's earned level had checkout, send_email and human_escalation
withheld; our probes had them allowed. The grade scored actions a visitor could not
get, and the artifact did not say so.

What is pinned here:
  1. An agent we cannot observe reports NOT OBSERVABLE, never "no gate".
  2. A probe that did not answer, or answered without a field, says so.
  3. `would_withhold_for_visitor` follows the gate's own rules: level against each
     skill's minimum, a require-oversight skill admitted on trust AND confidence,
     and nothing withheld unless the gate enforces.
  4. A change between grade start and artifact time is reported, and an
     unobserved end is "cannot tell", never "unchanged".
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.graded_env as ge

SPEC = {"ssh": "op@host", "container": "agent-x", "action_gate_probe_py": "print(1)"}

SKILLS = {"checkout": {"governance": "require_oversight", "min_level": 2},
          "send_email": {"governance": "require_oversight", "min_level": 2},
          "web_search": {"governance": "automate_freely", "min_level": 1},
          "book_appointment": {"governance": "automate_and_observe", "min_level": 1}}


def probe(**over):
    g = {"gate_mode": "enforce",
         "synthetic_bypass": {"pg-0123456789abcdef": True, "pg-1-1": True},
         "visitor_control_is_synthetic": False,
         "current_level": 1, "effective_level": 1,
         "override": {"active": False, "level": None, "reason": None},
         "trust_score": 0.59, "trust_confidence": 0.87,
         "admit_thresholds": {"trust": 0.6, "confidence": 0.75},
         "skills": SKILLS}
    g.update(over)
    return g


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    f = tmp_path / "hosted_agents.json"
    f.write_text(json.dumps({"ours": SPEC, "bare": {"ssh": "op@host", "container": "c"}}))
    monkeypatch.setattr(ge, "HOSTED_FILE", f)
    return f


def _answers(monkeypatch, gate: dict | None, rc=0):
    out = f"GATE={json.dumps(gate)}\n" if gate is not None else "GATE=\n"
    monkeypatch.setattr(ge.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=out, returncode=rc))


def test_third_party_agent_is_not_observable_never_no_gate(hosted):
    g = ge.action_gate("vendor-agent")
    assert g["observed"] is False and "would_withhold_for_visitor" not in g


def test_hosted_agent_without_a_probe_is_not_observable(hosted):
    assert ge.action_gate("bare")["observed"] is False


def test_a_probe_that_did_not_answer_is_unresolved(hosted, monkeypatch):
    _answers(monkeypatch, None)
    g = ge.action_gate("ours")
    assert g["observed"] is False and "unresolved" in g


def test_an_answer_missing_a_field_is_unresolved_not_a_verdict(hosted, monkeypatch):
    bad = probe()
    del bad["effective_level"]
    _answers(monkeypatch, bad)
    g = ge.action_gate("ours")
    assert g["observed"] is False and "effective_level" in g["unresolved"]


def test_level_1_withholds_every_require_oversight_skill_from_a_visitor(hosted, monkeypatch):
    """SPARK before the 2026-09-28 override: the case that went unrecorded."""
    _answers(monkeypatch, probe())
    g = ge.action_gate("ours")
    assert g["observed"] is True and g["probes_bypass_gate"] is True
    assert g["would_withhold_for_visitor"] == ["checkout", "send_email"]


def test_effective_level_2_withholds_nothing(hosted, monkeypatch):
    _answers(monkeypatch, probe(effective_level=2,
                                override={"active": True, "level": 2, "reason": "judgement"}))
    g = ge.action_gate("ours")
    assert g["would_withhold_for_visitor"] == []
    assert g["current_level"] == 1 and g["override"]["active"] is True   # earned level kept


def test_trust_admission_needs_score_and_confidence_together(hosted, monkeypatch):
    _answers(monkeypatch, probe(trust_score=0.61, trust_confidence=0.87))
    assert ge.action_gate("ours")["would_withhold_for_visitor"] == []
    _answers(monkeypatch, probe(trust_score=0.61, trust_confidence=0.70))   # thin evidence
    assert ge.action_gate("ours")["would_withhold_for_visitor"] == ["checkout", "send_email"]


def test_a_gate_that_does_not_enforce_withholds_nothing(hosted, monkeypatch):
    _answers(monkeypatch, probe(gate_mode="observe"))
    assert ge.action_gate("ours")["would_withhold_for_visitor"] == []


def test_probes_that_pass_through_the_gate_are_not_reported_as_bypassing(hosted, monkeypatch):
    _answers(monkeypatch, probe(synthetic_bypass={"pg-0123456789abcdef": False, "pg-1-1": True}))
    assert ge.action_gate("ours")["probes_bypass_gate"] is False


def _capture(monkeypatch, at_start, at_end):
    for name, val in (("_guard", {}), ("_models", {}), ("_venv_packages", None), ("_images", None)):
        monkeypatch.setattr(ge, name, lambda *a, _v=val, **k: _v)
    monkeypatch.setattr(ge, "action_gate", lambda agent: at_end)
    return ge.capture("ours", gate_at_start=at_start)["action_gate"]


def _obs(level, withheld):
    return {"observed": True, "gate_mode": "enforce", "effective_level": level,
            "probes_bypass_gate": True, "would_withhold_for_visitor": withheld}


def test_a_level_change_during_the_run_is_reported(monkeypatch):
    got = _capture(monkeypatch, _obs(1, ["checkout"]), _obs(2, []))
    assert got["changed_during_run"] is True


def test_the_same_gate_at_both_ends_is_unchanged(monkeypatch):
    got = _capture(monkeypatch, _obs(2, []), _obs(2, []))
    assert got["changed_during_run"] is False


def test_no_start_observation_means_cannot_tell_never_unchanged(monkeypatch):
    got = _capture(monkeypatch, None, _obs(2, []))
    assert got["changed_during_run"] is None and got["at_start"]["observed"] is False
