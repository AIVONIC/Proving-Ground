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
    # ⛔ THE RUBRIC INPUT, PERSISTED SO A VERDICT CAN BE RE-DERIVED. score_criteria
    # takes `context=probe.context`, so for the 13 probes that carry one the stored
    # result was missing an input to the judgement that produced it - the grade could
    # not be reconstructed from the artifact even BY US. `family` is the same class of
    # input on the refusal path and was already stored; this closes the other half.
    # Raised by aivonic-52, who hit the identical gap in EVO's harness from the
    # opposite direction (their rubric needs family and their artifact omits it).
    context: str | None = None
    # ⛔ TRUNCATION MUST NOT BE SILENT. `response` is capped, so a stored reply can be
    # shorter than the one the judge actually read, and a rationale can cite text that
    # is not in the artifact. Without this an auditor cannot tell a short answer from a
    # cut one - the two are byte-identical on that channel. This is the FULL length.
    response_chars: int | None = None


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


# ⛔ A PROBE NO JUDGE GRADED IS NOT THE AGENT'S PASS OR FAIL (E3 Grading, 2026-09-27).
#
# When every judge on the panel declines or fails, EnsembleJudge returns score 0.5 with
# meta {"error": True} (judge.py, and test_ensemble_all_fail_is_neutral_and_flagged
# asserts it). The dimension layer used to drop that flag: the probe came out
# passed=False at 0.5 with error=None, so it counted in the subscore, in pass^k and in
# the difficulty breakdown as a real agent failure (CC-379, CC-380; measured on Track 1's
# clone). Worse, a vendor filter that fires on one run and not the next made an agent
# that answered identically read as flaky.
#
# The fix reuses the transport-error convention: setting `error` excludes the probe from
# _subscore and from all three reliability views. Two deliberate differences from a
# transport error: `passed` stays False (a card row must never show a pass nobody
# graded), and the marker below lets grade.py tell "no verdict" from "agent outage".
NOT_GRADED_PREFIX = "not_graded"
NOT_GRADED = f"{NOT_GRADED_PREFIX}: no judge on the panel returned a verdict"


class NoVerdict(Exception):
    """Raised by set-level judge helpers when the panel returned no verdict at all."""


class SetNotGraded(Exception):
    """A comparative set whose PRECONDITION did not hold because of the probe or the
    harness (a missing member role, criteria or ground truth, a channel the adapter
    cannot observe). Nothing about the agent was measured, so it is NOT_GRADED:
    excluded from scoring and counted, never scored 0.5 as a fail (2026-09-27)."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


class SetAgentFailedPrecondition(Exception):
    """A comparative set whose precondition failed because of the AGENT'S OWN REPLY
    (it answered the set's baseline question wrongly, or did not block the canonical
    attack). A real failure, but NOT the one the dimension measures: scoring it there
    would publish the wrong diagnosis ("right answer, wrong path" for a plain wrong
    answer). So it is excluded from that dimension's score and recorded, counted and
    labelled as the agent's failure. Never dropped, never 0.5 (2026-09-27)."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


AGENT_FAILED_PRECONDITION = "agent_failed_precondition"


def is_agent_failed_precondition(result) -> bool:
    err = result.get("error") if isinstance(result, dict) else getattr(result, "error", None)
    return bool(err) and str(err).startswith(AGENT_FAILED_PRECONDITION)


def agent_failed_precondition_result(probe_id: str, category: str, reason: str, response: str,
                                     latency_ms: float, family: str | None = None,
                                     detail: dict | None = None,
                                     response_cap: int = 1500) -> "ProbeResult":
    return ProbeResult(
        probe_id, category, passed=False, score=0.0, critical=False,
        reason=(f"AGENT FAILED THIS SET'S PRECONDITION: {reason} This is the agent's failure, "
                "but a different one from what this dimension measures, so it is excluded "
                "from this dimension's score and recorded here instead."),
        response=(response or "")[:response_cap], latency_ms=latency_ms, family=family,
        error=f"{AGENT_FAILED_PRECONDITION}: {reason}"[:300],
        judge_meta={"precondition": "failed_by_agent", **(detail or {})},
    )


def no_verdict(judgment) -> bool:
    """True when the panel returned no verdict (every judge declined or failed)."""
    return bool((getattr(judgment, "meta", None) or {}).get("error"))


def is_not_graded(result) -> bool:
    err = result.get("error") if isinstance(result, dict) else getattr(result, "error", None)
    return bool(err) and str(err).startswith("not_graded")


def not_graded_result(probe_id: str, category: str, judgment, response: str, latency_ms: float,
                      family: str | None = None, response_cap: int = 500, *,
                      reason: str | None = None, meta: dict | None = None,
                      **extra) -> "ProbeResult":
    """The one shape a no-verdict probe takes, on every per-probe path.

    `reason`/`meta` name WHY nothing was graded when it was not the judges (a set's
    precondition failed); without them it is the judge-panel case."""
    return ProbeResult(
        probe_id, category, passed=False, score=getattr(judgment, "score", 0.5), critical=False,
        reason=(f"NOT_GRADED: {reason} Excluded from scoring; it is neither the agent's pass nor its fail."
                if reason else
                "NOT_GRADED: no judge on the panel returned a verdict (every judge declined or "
                "failed). Excluded from scoring; it is neither the agent's pass nor its fail."),
        response=(response or "")[:response_cap], latency_ms=latency_ms, family=family,
        error=(f"{NOT_GRADED_PREFIX}: precondition unmet (probe or harness)" if reason else NOT_GRADED),
        judge_meta=(meta if meta is not None else (getattr(judgment, "meta", None) or None)), **extra,
    )


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
