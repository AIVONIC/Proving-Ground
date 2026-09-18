"""A stored verdict must carry the inputs that produced it.

The published credibility claim is that an outside reader can download the
artifacts and recompute what we published. That only holds if the rubric inputs
behind each verdict are IN the artifact. Two were not:

  - `context` is passed to score_criteria as a rubric input and was never stored,
    so the 13 context-carrying probes produced verdicts nobody could re-derive -
    not a vendor, not an auditor, not us.
  - `response` was capped at 500 with no record of the true length, so a judge
    rationale could cite text absent from the artifact and a short reply was
    byte-identical to a cut one.

`family` is the same class of input on the refusal path and was ALREADY stored -
which is why this gap was invisible: the half that was covered made the half that
was not look covered too.

Raised by aivonic-52, who hit the mirror image in EVO's harness: their rubric
needs `family` and their artifact omits it.
"""
from __future__ import annotations

import dataclasses

from app.dimensions.base import ProbeResult
from app.dimensions.judged import RESPONSE_CAP


def test_probe_result_persists_rubric_context():
    r = ProbeResult("p", "c", True, 0.9, False, "why", "reply", 1.0,
                    context="the source document", response_chars=1834)
    d = dataclasses.asdict(r)
    assert d["context"] == "the source document"
    assert d["response_chars"] == 1834


def test_truncation_is_never_silent():
    """A cut response must be distinguishable from a short one."""
    full = "x" * (RESPONSE_CAP + 500)
    r = ProbeResult("p", "c", True, 0.9, False, "why", full[:RESPONSE_CAP], 1.0,
                    response_chars=len(full))
    d = dataclasses.asdict(r)
    assert len(d["response"]) == RESPONSE_CAP
    assert d["response_chars"] > len(d["response"]), "truncation must be detectable"


def test_legacy_construction_still_works():
    """NEGATIVE CONTROL. Six other construction sites omit both fields; if the
    defaults ever go away this test fails before production does."""
    d = dataclasses.asdict(ProbeResult("p", "c", True, 0.9, False, "why", "r", 1.0))
    assert d["context"] is None and d["response_chars"] is None


def test_judged_paths_pass_both_fields():
    """Assert on the SOURCE, because the fields above can exist on the dataclass
    while no production path ever sets them - a field nothing populates is
    indistinguishable from one that does not exist."""
    import inspect

    from app.dimensions import judged
    src = inspect.getsource(judged.GenericJudgedDimension)
    # `response_chars=` is the unambiguous marker: it appears ONLY on the two
    # ProbeResult constructions. Counting `context=probe.context` instead gives 3,
    # because the score_criteria CALL passes it too - a wrong field name returning a
    # plausible number, which is the defect class this whole file is about. The first
    # version of this test asserted 2 and failed against correct code.
    assert src.count("response_chars=len(response)") == 2, "both judged paths must record length"
    assert src.count("context=probe.context") == 3, "2 ProbeResult sites + 1 score_criteria call"
    assert "response[:500]" not in src, "hardcoded cap must go through RESPONSE_CAP"
