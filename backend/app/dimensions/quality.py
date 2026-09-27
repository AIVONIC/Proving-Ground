"""Conversational quality dimension.

Judge-only: relevance, coherence, tone, multi-turn context handling, and genuine
multilingual quality. No deterministic checks and no criticality (a weak but
non-malicious reply lowers the subscore, it does not cap the composite). The
judge is instructed to reward substance over style, which closes the
style-gaming vector.
"""

from __future__ import annotations

from app.dimensions.base import Dimension, Probe, ProbeResult

CRITERIA = [
    "relevance",
    "coherence",
    "tone_appropriateness",
    "multi_turn_context_handling",
    "multilingual_quality",
]

PASS_THRESHOLD = 0.6


class QualityDimension(Dimension):
    id = "conversational_quality"

    async def score_probe(self, probe: Probe, response: str, latency_ms: float, judge) -> ProbeResult:
        if judge is None:
            raise ValueError(f"probe {probe.id} needs a judge but none was provided")
        j = await judge.score_quality(probe.prompt, response, CRITERIA, context=probe.context or None)
        return ProbeResult(
            probe.id, probe.category, passed=j.score >= PASS_THRESHOLD, score=j.score, critical=False,
            reason=j.rationale, response=response[:500], latency_ms=latency_ms, family=probe.family,
        )
