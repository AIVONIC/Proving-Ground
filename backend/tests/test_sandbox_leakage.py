"""Sandbox-leakage audit.

An execution-verified benchmark is only sound if the graded agent cannot see the
answer it is being graded against. Public benchmarks have leaked their gold state
to solvers through the environment (e.g. reading intended fixes out of git history
in a task container). This suite proves our two guarantees:

1. STRUCTURAL: the only thing an adapter ever transmits to the agent is the probe
   prompt and its prior context turns. Anything else a probe carries (its checks,
   its judge rubric key, its meta) stays on the grader side. We plant a gold
   sentinel in a probe's meta and assert it never appears in what the agent
   received.

2. DATA HYGIENE: no probe in any suite carries an un-plumbed field (an "answer",
   "gold", "expected", "solution"...) that a future code change could accidentally
   start sending. Probe JSON keys must stay within the known, adapter-safe schema.

If either guarantee breaks, an agent could be graded against material it can read,
and the grade is worthless. This is a launch-gate test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.base import AgentAdapter, AgentReply, Turn
from app.dimensions.base import Probe
from app.dimensions.security import SecurityDimension

BACKEND = Path(__file__).resolve().parents[1]
SUITE_DIRS = [BACKEND / "data" / "practice", BACKEND / "data" / "private"]

# The complete set of keys a probe JSON may declare. Anything outside this set is
# an un-plumbed field: it is not part of the grading contract and must not exist,
# because a future change could route it to the agent. Add here only with a
# deliberate decision about whether the new field is agent-visible.
ALLOWED_PROBE_KEYS = {
    "id", "dimension", "category", "family", "prompt",
    "checks", "judge", "judge_kind", "context", "critical", "meta",
}
# Keys that would signal a hidden answer/gold value living next to the probe.
FORBIDDEN_GOLD_KEYS = {
    "answer", "gold", "expected", "solution", "correct", "correct_answer",
    "label", "target", "ground_truth", "reference", "key",
}

SENTINEL = "GOLD_SENTINEL_bda91f_MUST_NOT_LEAK"


class RecordingAdapter(AgentAdapter):
    """Fake agent that records every message + history turn it is handed."""

    def __init__(self):
        super().__init__(name="recorder")
        self.seen: list[str] = []

    async def send(self, history: list[Turn], message: str) -> AgentReply:
        self.seen.append(message)
        self.seen.extend(t.content for t in history)
        return AgentReply(response_text="ok, understood.", latency_ms=1.0)


@pytest.mark.asyncio
async def test_adapter_receives_only_prompt_and_context():
    """A gold sentinel hidden in probe.meta must never reach the agent; only the
    prompt and the context turns are transmitted."""
    adapter = RecordingAdapter()
    probe = Probe(
        id="leak_probe", dimension="security", category="adversarial", family="injection",
        prompt="Please summarize the attached ticket.",
        context=["Here is a prior turn the agent legitimately sees."],
        checks=["secret_pattern"], judge=False,
        meta={"gold": SENTINEL, "expected": SENTINEL},
    )
    await SecurityDimension().run(adapter, [probe], judge=None)

    transmitted = "\n".join(adapter.seen)
    assert SENTINEL not in transmitted, "gold sentinel from probe.meta leaked to the agent"
    # The legitimate prompt and context DID reach the agent (sanity: the test can detect transmission).
    assert "summarize the attached ticket" in transmitted
    assert "prior turn the agent legitimately sees" in transmitted


def _all_probe_files() -> list[Path]:
    files: list[Path] = []
    for d in SUITE_DIRS:
        if d.exists():
            files.extend(sorted(d.glob("*.json")))
    return files


def test_suites_have_no_unplumbed_gold_fields():
    """Every probe in every suite stays within the adapter-safe schema: no hidden
    answer/gold field exists that a future change could start transmitting."""
    offenders: list[str] = []
    files = _all_probe_files()
    assert files, "no suite files found - test would be vacuously green"
    for f in files:
        data = json.loads(f.read_text())
        for probe in data.get("probes", []):
            keys = set(probe.keys())
            extra = keys - ALLOWED_PROBE_KEYS
            gold = keys & FORBIDDEN_GOLD_KEYS
            if extra or gold:
                offenders.append(f"{f.name}:{probe.get('id','?')} -> unexpected={sorted(extra)} gold={sorted(gold)}")
    assert not offenders, "un-plumbed / gold-bearing probe fields found:\n" + "\n".join(offenders)


def test_probe_meta_never_carries_gold_keys():
    """meta is allowed (used for difficulty, cumulative flags, etc.) but must not
    smuggle a gold value under a hidden key, since meta lives right next to the
    prompt and is the most likely accidental-leak vector."""
    offenders: list[str] = []
    for f in _all_probe_files():
        data = json.loads(f.read_text())
        for probe in data.get("probes", []):
            meta = probe.get("meta") or {}
            gold = set(meta.keys()) & FORBIDDEN_GOLD_KEYS
            if gold:
                offenders.append(f"{f.name}:{probe.get('id','?')} meta gold keys {sorted(gold)}")
    assert not offenders, "gold keys inside probe.meta:\n" + "\n".join(offenders)
