"""Dimension scaffolding.

A dimension is a set of probes plus a way to turn probe results into a 0..10
subscore. Every dimension runs its probes through an AgentAdapter, so a dimension
never knows or cares what kind of agent it is grading.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.adapters.base import AgentAdapter, Turn


@dataclass
class Probe:
    """One test. ``prompt`` is the user turn; multi-turn probes list priors in
    ``context`` (agent replies are filled in live). ``checks`` are deterministic
    fail-detectors (see dimensions/checks.py); ``judge`` requests an LLM judgment.
    """

    id: str
    dimension: str
    category: str                 # baseline | adversarial | long_context | execution
    prompt: str
    family: str = ""
    critical: bool = False
    checks: list[str] = field(default_factory=list)
    judge: bool = False
    judge_kind: str = "refusal"   # which judge rubric to apply
    context: list[str] = field(default_factory=list)  # prior user turns (isolated session)
    meta: dict[str, Any] = field(default_factory=dict)


# Difficulty tier derived from a probe's category, so every probe is stratified without
# hand-labelling. A tier means the same thing across dimensions: L1 = ordinary in-scope
# use, L2 = adversarial / effect-producing, L3 = long-horizon or long-context. A probe may
# override via meta["difficulty"]. Stratified reporting keeps a tier meaningful and makes
# saturation visible (all-L1-pass but L3-failing tells a story a single mean hides).
DIFFICULTY_BY_CATEGORY = {
    "baseline": "L1",
    "execution": "L2",
    "adversarial": "L2",
    "long_context": "L3",
}


def probe_difficulty(category: str, meta: dict | None = None) -> str:
    if meta and meta.get("difficulty"):
        return str(meta["difficulty"])
    return DIFFICULTY_BY_CATEGORY.get(category, "L2")


@dataclass
class ProbeResult:
    probe_id: str
    category: str
    passed: bool
    score: float                  # 0..1 (partial credit allowed)
    critical: bool
    reason: str
    response: str
    latency_ms: float
    family: str = ""
    error: str | None = None
    judge_agreement: float | None = None   # 1 - (max-min judge score); None if deterministic/no panel
    judge_meta: dict | None = None          # per-judge scores + spread from the ensemble
    severity: str = "none"                  # graded breach severity when a detector fired: none|low|medium|high|critical


@dataclass
class DimensionResult:
    dimension: str
    subscore: float               # 0..10
    probe_results: list[ProbeResult]

    @property
    def critical_failures(self) -> list[ProbeResult]:
        return [r for r in self.probe_results if r.critical and not r.passed]

    @property
    def runnable(self) -> list[ProbeResult]:
        return [r for r in self.probe_results if r.error is None]


class Dimension(ABC):
    """Base dimension. Subclasses provide ``id`` and how a probe is scored."""

    id: str = "dimension"

    @abstractmethod
    async def score_probe(self, probe: Probe, response: str, latency_ms: float, judge) -> ProbeResult:
        """Turn one agent reply into a scored ProbeResult."""

    async def run(self, adapter: AgentAdapter, probes: list[Probe], judge=None) -> DimensionResult:
        import asyncio
        import os
        # Optional throttle so grading a LIVE production agent does not overwhelm it
        # (e.g. SPARK over the network with retrieval per message). Off by default;
        # the grade runner sets it when the target is production. Never changes scores.
        delay = float(os.environ.get("PROVING_GROUND_PROBE_DELAY_MS", "0") or 0) / 1000.0
        results: list[ProbeResult] = []
        for i, probe in enumerate(probes):
            if delay and i:
                await asyncio.sleep(delay)
            await adapter.reset()  # isolated by default; cumulative probes opt out via meta
            history: list[Turn] = []
            for prior in probe.context:
                reply = await adapter.send(history, prior)
                history.extend([Turn("user", prior), Turn("agent", reply.response_text)])
            reply = await adapter.send(history, probe.prompt)
            if not reply.ok:
                # A transport error on our side is not the agent's fault: record and skip scoring.
                results.append(ProbeResult(
                    probe.id, probe.category, passed=True, score=1.0, critical=probe.critical,
                    reason=f"transport_error_retryable: {reply.error}", response="",
                    latency_ms=reply.latency_ms, family=probe.family, error=reply.error,
                ))
                continue
            results.append(await self.score_probe(probe, reply.response_text, reply.latency_ms, judge))
        return DimensionResult(self.id, self._subscore(results), results)

    def _subscore(self, results: list[ProbeResult]) -> float:
        scored = [r for r in results if r.error is None]
        if not scored:
            return 0.0
        return round(10.0 * sum(r.score for r in scored) / len(scored), 2)
