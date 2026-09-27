"""A probe no judge graded is excluded from every score, never counted as the agent's fail.

Drives the REAL EnsembleJudge and the real dimension objects with judges that all
decline (JudgeAbstained) or all fail. Each exclusion assertion is paired with a
control that a genuine low verdict still counts, so a fix that excluded everything
could not pass.
"""
import asyncio

import pytest

from app.dimensions.base import NOT_GRADED, Probe, ProbeResult, DimensionResult, is_not_graded
from app.dimensions.comparative import ComparativeDimension, SetObservation, judge_equivalence
from app.dimensions.judged import GenericJudgedDimension
from app.dimensions.quality import QualityDimension
from app.dimensions.security import SecurityDimension
from app.grade import wreckage_refusal
from app.judges.judge import EnsembleJudge, Judge, JudgeAbstained, Judgment
from app.scoring.reliability import difficulty_breakdown, pass_k_curve


class Declines(Judge):
    def __init__(self, name):
        self.name = name

    async def _no(self, *a, **k):
        raise JudgeAbstained(f"{self.name} declined (fixture)")

    score_refusal = score_quality = score_criteria = _no


class Fails(Declines):
    async def _no(self, *a, **k):
        raise RuntimeError("judge outage (fixture)")

    score_refusal = score_quality = score_criteria = _no


class Scores(Declines):
    def __init__(self, name, score):
        super().__init__(name)
        self.s = score

    async def _yes(self, *a, **k):
        return Judgment(self.s, f"{self.name} says {self.s}")

    score_refusal = score_quality = score_criteria = _yes


ALL_DECLINE = EnsembleJudge([Declines("claude"), Declines("openai"), Declines("grok"), Declines("gemini")])
ALL_FAIL = EnsembleJudge([Fails("a"), Fails("b")])
LOW = EnsembleJudge([Scores("claude", 0.1), Scores("openai", 0.1)])


def _probe(dim="safety_and_harm", judge=True):
    return Probe(id="p1", dimension=dim, category="adversarial", prompt="q", family="f", judge=judge)


def _run(c):
    return asyncio.run(c)


@pytest.mark.parametrize("panel", [ALL_DECLINE, ALL_FAIL], ids=["all_decline", "all_fail"])
@pytest.mark.parametrize("dim", [
    GenericJudgedDimension("safety_and_harm", mode="criteria", critical_on_fail=True),
    SecurityDimension(refute=True),
    QualityDimension(),
], ids=["judged", "security", "quality"])
def test_no_verdict_is_named_and_excluded(dim, panel):
    r = _run(dim.score_probe(_probe(), "a reply", 10.0, panel))
    assert r.error == NOT_GRADED and is_not_graded(r)
    assert r.passed is False and r.critical is False       # never a pass nobody graded
    assert r.reason.startswith("NOT_GRADED")
    assert dim._subscore([r]) == 0.0                       # nothing scored, not 5.0


@pytest.mark.parametrize("dim", [
    GenericJudgedDimension("safety_and_harm", mode="criteria"),
    SecurityDimension(refute=True),
    QualityDimension(),
], ids=["judged", "security", "quality"])
def test_control_a_real_low_verdict_still_counts(dim):
    r = _run(dim.score_probe(_probe(), "a reply", 10.0, LOW))
    assert r.error is None and not r.passed and r.score == pytest.approx(0.1)


def _pr(pid, passed, error=None):
    return ProbeResult(pid, "adversarial", passed=passed, score=1.0 if passed else 0.5,
                       critical=False, reason="", response="", latency_ms=1.0, error=error)


def _runs(*rows):
    return [{"d": DimensionResult("d", 0.0, list(r))} for r in rows]


def test_reliability_case_a_all_decline_is_excluded():
    ok = [_pr("a", True), _pr("b", True), _pr("c", True)]
    runs = _runs(ok + [_pr("x", False, NOT_GRADED)], ok + [_pr("x", False, NOT_GRADED)])
    assert pass_k_curve(runs)["curve"][2] == 1.0
    assert all(v["pass_rate"] == 1.0 for v in difficulty_breakdown(runs).values())


def test_reliability_case_b_vendor_variance_is_not_agent_flakiness():
    runs = _runs([_pr("a", True), _pr("b", True), _pr("x", False, NOT_GRADED)],
                 [_pr("a", True), _pr("b", True), _pr("x", True)])
    assert pass_k_curve(runs)["curve"][2] == 1.0


def test_control_reliability_still_sees_a_real_failure():
    runs = _runs([_pr("a", True), _pr("x", False)], [_pr("a", True), _pr("x", False)])
    assert pass_k_curve(runs)["curve"][1] == 0.5


def test_not_graded_is_not_wreckage_but_transport_errors_still_are():
    ng = _runs([_pr(f"n{i}", False, NOT_GRADED) for i in range(5)] + [_pr("a", True)])
    assert wreckage_refusal(ng) is None
    outage = _runs([_pr(f"t{i}", True, "timeout") for i in range(5)] + [_pr("a", True)])
    assert wreckage_refusal(outage) is not None


class _EquivDim(ComparativeDimension):
    id = "equiv_fixture"

    async def score_set(self, set_id, obs, judge):
        from app.dimensions.comparative import SetVerdict
        s, why = await judge_equivalence(judge, relation="equivalent", axis="x",
                                         a=obs[0].response, b=obs[1].response)
        return SetVerdict(s, why)


def test_set_with_no_verdict_is_named_and_excluded(monkeypatch):
    dim = _EquivDim()
    obs = [SetObservation("m1", "a", "q", "r1", 1.0), SetObservation("m2", "b", "q", "r2", 1.0)]

    async def fake_run_set(self, adapter, members):
        return obs
    monkeypatch.setattr(_EquivDim, "_run_set", fake_run_set)
    members = [Probe(id=f"m{i}", dimension="equiv_fixture", category="baseline", prompt="q",
                     meta={"set": "s1", "role": r}) for i, r in ((1, "a"), (2, "b"))]
    res = _run(dim.run(None, members, ALL_DECLINE))
    assert len(res.probe_results) == 1 and is_not_graded(res.probe_results[0])
    assert res.subscore == 0.0
    ctl = _run(dim.run(None, members, LOW))                 # control: a real verdict counts
    assert ctl.probe_results[0].error is None and ctl.subscore == pytest.approx(1.0)


# --- part 2: promotion refuses what nobody graded where it matters (aivonic-54) -----

from app.dimensions.catalog import REGISTRY as _REG  # noqa: E402
from app.leaderboard.promote import entry_from_run, not_graded_audit  # noqa: E402

_META = {"id": "a", "name": "A", "vendor": "V", "graded_at": "2026-09-27"}


def _p(pid, ng=False):
    return {"probe_id": pid, "passed": not ng, "score": 0.5 if ng else 1.0,
            "error": NOT_GRADED if ng else None}


def _art(**dims):
    runs = [{d: rows for d, rows in dims.items()}]
    return {"grade": {"composite": 88.0, "tier": "Premium",
                      "subscores": {k: 9.0 for k in _REG}, "confidence": {"runs": 1}},
            "runs": runs}


def test_a_clean_run_promotes_and_says_nothing():
    e = entry_from_run(_art(security=[_p("s1")], conversational_quality=[_p("q1")]), _META)
    assert "not_graded" not in e and "not_graded_reviewed_by" not in e


def test_ungraded_outside_harm_promotes_with_a_visible_count():
    e = entry_from_run(_art(conversational_quality=[_p("q1"), _p("q2", ng=True)]), _META)
    assert e["not_graded"] == 1 and "not_graded_reviewed_by" not in e


def test_ungraded_harm_probe_blocks_until_a_person_has_read_it():
    art = _art(safety_and_harm=[_p("h1"), _p("h2", ng=True)])
    with pytest.raises(SystemExit) as ex:
        entry_from_run(art, _META)
    assert "h2" in str(ex.value) and "READ" in str(ex.value)
    e = entry_from_run(art, {**_META, "not_graded_reviewed_by": "Christian"})
    assert e["not_graded"] == 1 and e["not_graded_reviewed_by"] == "Christian"


def test_a_wholly_ungraded_dimension_blocks_even_with_a_reviewer():
    art = _art(conversational_quality=[_p("q1", ng=True), _p("q2", ng=True)])
    with pytest.raises(SystemExit) as ex:
        entry_from_run(art, {**_META, "not_graded_reviewed_by": "Christian"})
    assert "real zero" in str(ex.value)


def test_the_published_board_grades_pass_the_gate():
    import glob, json
    arts = sorted(glob.glob("data/runs/merged/*_n5.json"))
    if not arts:
        pytest.skip("merged artifacts not on this machine")
    for a in arts:
        au = not_graded_audit(json.loads(open(a).read()))
        assert au == {"total": 0, "harm": [], "empty": []}, a


def test_the_card_states_an_ungraded_count_and_is_silent_without_one():
    from app.leaderboard.render import card, not_graded_note
    base = {"id": "a", "name": "A", "composite": 88.0, "tier": "Premium", "subscores": {}}
    assert not_graded_note(base) == "" and "no verdict" not in card(1, base)
    e = {**base, "not_graded": 2, "not_graded_reviewed_by": "Christian"}
    assert "2 probes got no verdict" in card(1, e) and "read by Christian" in not_graded_note(e)
