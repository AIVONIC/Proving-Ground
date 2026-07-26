"""Ensemble judge: aggregation, agreement, and robustness to a judge outage."""
import asyncio

from app.judges.judge import EnsembleJudge, Judgment, Judge


class FakeJudge(Judge):
    def __init__(self, name, score, boom=False):
        self.name = name
        self._score = score
        self._boom = boom

    async def _one(self):
        if self._boom:
            raise RuntimeError("judge outage")
        return Judgment(self._score, f"{self.name} says {self._score}")

    async def score_refusal(self, *a, **k):
        return await self._one()

    async def score_quality(self, *a, **k):
        return await self._one()

    async def score_criteria(self, *a, **k):
        return await self._one()


def _run(coro):
    return asyncio.run(coro)


def test_ensemble_averages_and_reports_agreement():
    e = EnsembleJudge([FakeJudge("claude", 0.9), FakeJudge("openai", 0.7)])
    j = _run(e.score_criteria("p", "r", "rubric"))
    assert abs(j.score - 0.8) < 1e-9
    assert j.meta["spread"] == 0.2
    assert j.meta["agreement"] == 0.8
    assert {p["judge"] for p in j.meta["per_judge"]} == {"claude", "openai"}


def test_ensemble_survives_one_judge_outage():
    e = EnsembleJudge([FakeJudge("claude", 0.9), FakeJudge("openai", 0.0, boom=True)])
    j = _run(e.score_criteria("p", "r", "rubric"))
    assert j.score == 0.9                     # decided by the surviving judge
    assert len(j.meta["per_judge"]) == 1


def test_ensemble_all_fail_is_neutral_and_flagged():
    e = EnsembleJudge([FakeJudge("a", 0.0, boom=True), FakeJudge("b", 0.0, boom=True)])
    j = _run(e.score_criteria("p", "r", "rubric"))
    assert j.score == 0.5
    assert j.meta.get("error") is True


def test_single_judge_perfect_agreement():
    e = EnsembleJudge([FakeJudge("solo", 0.6)])
    j = _run(e.score_refusal("p", "r"))
    assert j.score == 0.6
    assert j.meta["agreement"] == 1.0
