"""Conversational quality: a substantive agent scores well, an empty/terse one
scores poorly. Uses the offline StubJudge; multi-turn context is threaded."""

from __future__ import annotations

import pytest

from app.adapters.base import AgentAdapter, AgentReply, Turn
from app.dimensions.quality import QualityDimension
from app.judges.judge import StubJudge
from app.suites import load_probes


class ScriptedAdapter(AgentAdapter):
    def __init__(self, reply: str):
        super().__init__(name="scripted")
        self._reply = reply
        self.saw_context = False

    async def send(self, history, message) -> AgentReply:
        if history:
            self.saw_context = True
        return AgentReply(self._reply, latency_ms=2.0)


@pytest.mark.asyncio
async def test_substantive_agent_scores_reasonably():
    probes = load_probes("data/practice/quality_practice.json")
    adapter = ScriptedAdapter("Happy to help. Here is a clear, relevant answer that addresses your question directly.")
    res = await QualityDimension().run(adapter, probes, judge=StubJudge())
    assert res.subscore >= 6.0
    assert res.critical_failures == []           # quality never produces critical failures
    assert adapter.saw_context                    # multi-turn probes threaded their prior turns


@pytest.mark.asyncio
async def test_empty_agent_scores_poorly():
    probes = load_probes("data/practice/quality_practice.json")
    res = await QualityDimension().run(ScriptedAdapter(""), probes, judge=StubJudge())
    assert res.subscore <= 2.0


def test_quality_suite_loads_and_has_categories():
    probes = load_probes("data/practice/quality_practice.json")
    # Assert the invariants that matter, not a frozen total that re-breaks every
    # time a probe is added. A floor guards against an accidentally gutted suite.
    assert len(probes) >= 10
    ids = [p.id for p in probes]
    assert len(ids) == len(set(ids)), "probe ids must be unique"
    assert all(p.prompt.strip() for p in probes)  # every probe has a prompt to send
    assert all(p.dimension for p in probes)        # every probe declares its dimension
    cats = {p.category for p in probes}
    assert {"baseline", "adversarial", "long_context"} <= cats  # required categories present
    assert any(p.context for p in probes)         # at least one multi-turn probe
