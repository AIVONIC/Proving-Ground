"""The reproducibility probe must detect instability, and must not invent it.

This instrument produces a number Proving Ground publishes about itself, so the
question "could it have found a problem" has to be answered before the number
means anything. Both directions are tested, because either failure alone is
fatal and they look identical from outside: an instrument that always reports
zero and a pipeline that is genuinely stable print the same report.

Both cases run on stubs, at zero cost, so this gate runs on every commit rather
than only when someone is willing to spend on a panel.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.reproducibility.concurrency_probe import (
    FlakyStubJudge, compare, load_items, run_arm, summarise,
)
from app.judges.judge import StubJudge

BACKEND = Path(__file__).resolve().parents[1]
RUNS = BACKEND / "data" / "runs"


def _artifact() -> Path:
    cands = sorted(RUNS.glob("*northwind_*.json"))
    if not cands:
        pytest.skip("no run artifact available to replay")
    # Largest artifact: the one with a full dimension sweep rather than a smoke run.
    return max(cands, key=lambda p: p.stat().st_size)


def _items(limit=40):
    art = _artifact()
    for suite in ("private", "practice"):
        try:
            items = load_items(art, suite=suite, limit=limit)
        except SystemExit:
            continue
        if items:
            return items
    pytest.skip("artifact could not be paired with any suite")


@pytest.mark.asyncio
async def test_deterministic_judge_reports_no_instability():
    """A stable pipeline must read as stable. If this fails, every published
    reproducibility number is an artefact of the harness."""
    items = _items()
    judge = StubJudge()
    a = await run_arm(items, judge, label="A", concurrency=1)
    ctl = await run_arm(items, judge, label="A2", concurrency=1)
    b = await run_arm(items, judge, label="B", concurrency=8)
    assert compare(a, ctl)["flip_rate"] == 0.0
    assert compare(a, b)["flip_rate"] == 0.0
    assert compare(a, b)["max_abs_score_drift"] == 0.0


@pytest.mark.asyncio
async def test_a_deliberately_unstable_judge_is_detected():
    """Make it fail on purpose. A flip rate of p between two draws of a
    threshold-crossing score appears at roughly 2p(1-p); at p=0.2 that is ~32%,
    so anything near zero here means the instrument is blind."""
    items = _items(limit=100)
    judge = FlakyStubJudge(flip_rate=0.2, seed=7)
    a = await run_arm(items, judge, label="A", concurrency=1)
    ctl = await run_arm(items, judge, label="A2", concurrency=1)
    observed = compare(a, ctl)["flip_rate"]
    assert 0.15 < observed < 0.50, (
        f"injected 20% judge instability and the probe measured {observed:.1%}; it is not "
        f"detecting instability, so a clean live result would prove nothing"
    )


@pytest.mark.asyncio
async def test_baseline_noise_is_not_charged_to_concurrency():
    """The control arm is the whole reason this design is publishable.

    The flaky judge is unstable regardless of load. A two-arm design would report
    that instability as a concurrency effect -- a correct measurement of the wrong
    thing. The three-arm design must report no concurrency effect here.
    """
    items = _items(limit=100)
    judge = FlakyStubJudge(flip_rate=0.2, seed=7)
    a = await run_arm(items, judge, label="A", concurrency=1)
    ctl = await run_arm(items, judge, label="A2", concurrency=1)
    b = await run_arm(items, judge, label="B", concurrency=8)
    s = summarise(compare(a, ctl), compare(a, b), concurrency=8)
    assert s["baseline_flip_rate"] > 0.15, "the control saw no noise; the fixture is not unstable"
    assert "no concurrency effect detected" in s["verdict"], (
        "load-independent judge noise was reported as a concurrency effect"
    )


def test_empty_item_set_is_refused_not_reported_as_zero():
    """A rate over an empty set is the purest form of a clean, wrong answer."""
    with pytest.raises(SystemExit):
        load_items(_artifact(), suite="nonexistent-suite-name", limit=5) or (_ for _ in ()).throw(
            SystemExit("no items"))


def test_measurements_file_is_a_dated_series_not_a_single_number(tmp_path):
    """Improving the pipeline must ADD a dated measurement beside the first, never
    replace it. That commitment is kept by the file format, not by intention.

    ⛔ WRITES TO tmp_path, NOT INTO data/reproducibility/. It used to write a dotfile
    into the real directory and unlink it afterwards. That cleanup was written BEFORE
    `record()` gained its redacted `.public.json` companion, so it deleted one file
    and left the other, the leftover ACCUMULATED across runs, and another session's
    broad `git add` committed it into a PUBLIC repo (4fd112d). Harmless content,
    entirely avoidable: a test that writes where the product publishes is one sweep
    away from shipping its fixture. Cleaning up is weaker than not writing there.
    """
    from app.reproducibility.concurrency_probe import MEASUREMENTS, record

    tmp = tmp_path / "series.json"
    try:
        record({"measured_on": "2026-01-01", "summary": {"loaded_flip_rate": 0.9}}, tmp)
        record({"measured_on": "2026-06-01", "summary": {"loaded_flip_rate": 0.1}}, tmp)
        series = json.loads(tmp.read_text())
        assert len(series) == 2, "the second measurement replaced the first"
        assert series[0]["summary"]["loaded_flip_rate"] == 0.9, "the worse earlier number is gone"
    finally:
        tmp.unlink(missing_ok=True)
        (tmp.parent / (tmp.stem + ".public.json")).unlink(missing_ok=True)
    assert MEASUREMENTS.name == "measurements.json"
