"""Latency and reliability dimension.

Deterministic: each probe is a benign message; the reply's wall-clock latency
(captured by the adapter) is scored against thresholds. Transport errors are
already excluded upstream (they are our fault, not the agent's), so this reflects
only replies the agent actually produced.
"""

from __future__ import annotations

from app.dimensions.base import Dimension, Probe, ProbeResult

# (ceiling_ms, score) bands, first match wins.
BANDS = [(2000, 1.0), (4000, 0.85), (7000, 0.6), (12000, 0.35), (float("inf"), 0.15)]


class LatencyDimension(Dimension):
    id = "latency_and_reliability"

    async def score_probe(self, probe: Probe, response: str, latency_ms: float, judge) -> ProbeResult:
        score = next(s for ceil, s in BANDS if latency_ms < ceil)
        return ProbeResult(
            probe.id, probe.category, passed=score >= 0.5, score=score, critical=False,
            reason=f"{latency_ms:.0f}ms", response=response[:120], latency_ms=latency_ms, family=probe.family,
        )
