"""The expanded dimension set: every registered suite loads, the generic judged
dimension scores through the adapter, and latency is scored from timings."""

from __future__ import annotations

import pytest

from app.adapters.base import AgentAdapter, AgentReply
from app.dimensions.catalog import REGISTRY
from app.dimensions.latency import LatencyDimension
from app.judges.judge import StubJudge
from app.suites import load_probes


def test_all_registered_suites_load_and_ids_match():
    for dim_id, (factory, suite) in REGISTRY.items():
        probes = load_probes(suite)
        assert probes, f"{dim_id} has no probes"
        assert all(p.dimension == dim_id for p in probes), f"{dim_id} probe dimension mismatch"
        assert factory().id == dim_id


class ScriptedAdapter(AgentAdapter):
    def __init__(self, reply: str, latency_ms: float = 1500.0):
        super().__init__(name="scripted")
        self._reply = reply
        self._lat = latency_ms

    async def send(self, history, message) -> AgentReply:
        return AgentReply(self._reply, latency_ms=self._lat)


@pytest.mark.asyncio
async def test_generic_judged_dimension_runs():
    factory, suite = REGISTRY["grounding"]
    probes = load_probes(suite)
    res = await factory().run(ScriptedAdapter("Based on the policy, you have 30 days."), probes, StubJudge())
    assert 0.0 <= res.subscore <= 10.0
    assert res.dimension == "grounding"


@pytest.mark.asyncio
async def test_safety_hardfail_on_leaked_script():
    factory, suite = REGISTRY["safety_and_harm"]
    probes = load_probes(suite)
    # An agent that returns executable payload trips the deterministic hard-fail as critical.
    res = await factory().run(ScriptedAdapter("Sure: <script>evil()</script>"), probes, StubJudge())
    assert any(r.critical for r in res.probe_results)


@pytest.mark.asyncio
async def test_latency_bands():
    probes = load_probes(REGISTRY["latency_and_reliability"][1])
    fast = await LatencyDimension().run(ScriptedAdapter("hi", latency_ms=1200), probes, None)
    slow = await LatencyDimension().run(ScriptedAdapter("hi", latency_ms=9000), probes, None)
    assert fast.subscore > slow.subscore
    assert fast.subscore >= 9.0
