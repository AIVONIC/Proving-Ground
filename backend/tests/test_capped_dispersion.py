"""A capped composite is a ceiling, not a measurement, and must not claim precision.

CRITICAL_CAP is applied PER RUN, before aggregation, so every run of a capped agent
is flattened to exactly the cap. Every dispersion statistic computed from those
numbers then describes the determinism of the CAP rather than the stability of the
agent, and two published numbers were wrong as a result:

    variance 0.0 and ci95 [40.0, 40.0]   perfect precision, asserted about a constant
    latency_and_reliability 9.85         a capped agent credited with flawless stability

The second is the one that surprises: stability_part = 10 - stdev feeds a WEIGHTED
SUBSCORE that appears on the scorecard and the radar.

The fix is one principle -- dispersion comes from the UNCAPPED per-run composites --
and the most important test here is the NO-OP one: for any agent without a critical
failure the capped and uncapped composites are identical, so nothing on the board
may move. A fix that quietly moved five published grades would be worse than the
defect it corrects.
"""

from __future__ import annotations

import glob
import json
import statistics
from pathlib import Path

import pytest

from app.dimensions.base import DimensionResult, ProbeResult
from app.scoring import config
from app.scoring.scorer import aggregate_runs, compute_composite, score_single_run

BACKEND = Path(__file__).resolve().parents[1]


def _mk(reliability=0.8, other=0.85, critical=False):
    dims = {}
    for dim in config.DIMENSION_WEIGHTS:
        s = reliability if dim == config.RELIABILITY_DIM else other
        pr = ProbeResult("p", "baseline", not critical, s, critical, "", "", 10.0)
        dims[dim] = DimensionResult(dim, round(s * 10, 2), [pr])
    return score_single_run(dims)


def _runs_from_artifact(path):
    a = json.loads(Path(path).read_text())
    out = []
    for run in a["runs"]:
        dims = {}
        for dim, prs in run.items():
            rs = [ProbeResult(**{k: v for k, v in pr.items()
                                 if k in ProbeResult.__dataclass_fields__}) for pr in prs]
            sc = [r for r in rs if r.error is None]
            dims[dim] = DimensionResult(
                dim, round(10.0 * sum(r.score for r in sc) / len(sc), 2) if sc else 0.0, rs)
        out.append(score_single_run(dims))
    return out


def _onyx():
    c = sorted(glob.glob(str(BACKEND / "data/runs/onyx-northwind_2026091*.json")))
    if not c:
        pytest.skip("no capped artifact available")
    return _runs_from_artifact(c[0])


# ------------------------------------------------- the no-op guarantee, first

@pytest.mark.parametrize("spread", [(0.8, 0.9, 0.7), (0.5, 0.5, 0.5), (0.95, 0.4, 0.7)])
def test_an_UNCAPPED_grade_is_completely_unaffected(spread):
    """The load-bearing test. Five published grades pass through this code, and a
    correctness fix that moved any of them would be a worse defect than the one it
    fixes. Capped and uncapped per-run composites are identical without a critical
    failure, so the result must be bit-identical."""
    g = aggregate_runs([_mk(reliability=r) for r in spread])
    assert not g.capped
    assert g.confidence["variance"] is not None
    assert g.confidence["ci95_low"] is not None and g.confidence["ci95_high"] is not None
    assert "capped_from" not in g.confidence
    assert g.confidence["ci95_low"] <= g.composite <= g.confidence["ci95_high"], (
        "an uncapped composite must sit inside its own interval"
    )


def test_the_five_board_grades_do_not_move(tmp_path):
    """The same guarantee against REAL artifacts rather than synthetic ones."""
    arts = [p for p in sorted(glob.glob(str(BACKEND / "data/runs/*northwind_2026091*.json")))
            if "onyx" not in p and "pre-rederive" not in p]
    if not arts:
        pytest.skip("no uncapped artifacts available")
    for a in arts:
        runs = _runs_from_artifact(a)
        g = aggregate_runs(runs)
        if g.capped:
            continue
        stored = json.loads(Path(a).read_text())["grade"]["composite"]
        assert g.composite == stored, f"{Path(a).name}: composite moved {stored} -> {g.composite}"


# --------------------------------------------------------- the capped case

def test_a_capped_grade_reports_NO_interval_and_NO_variance():
    runs = _onyx()
    g = aggregate_runs(runs)
    assert g.capped and g.composite == config.CRITICAL_CAP
    assert g.confidence["variance"] is None, (
        "variance 0.0 on a capped grade is the determinism of the cap, not the agent, "
        "and it reads as the smallest number on the board"
    )
    assert g.confidence["ci95_low"] is None and g.confidence["ci95_high"] is None
    assert "ci95_unavailable" in g.confidence


def test_the_measurement_beneath_the_cap_keeps_its_interval():
    runs = _onyx()
    g = aggregate_runs(runs)
    uncapped = [compute_composite(r.subscores, 0)[0] for r in runs]
    assert g.confidence["capped_from"] == pytest.approx(
        round(statistics.mean(uncapped), 2), abs=0.5)
    lo, hi = g.confidence["capped_from_ci95_low"], g.confidence["capped_from_ci95_high"]
    assert lo < g.confidence["capped_from"] < hi, "the real measurement has a real interval"
    assert hi - lo > 0, "a zero-width interval is the defect, wherever it appears"
    assert g.confidence["capped_from_variance"] > 0, (
        "the underlying runs genuinely differ; a zero here means the cap leaked through"
    )


def test_the_cap_no_longer_inflates_a_published_SUBSCORE():
    """The half neither of us saw first: stability_part feeds a WEIGHTED subscore
    that is on the scorecard and the radar, so a capped agent was published as
    perfectly stable."""
    runs = _onyx()
    g = aggregate_runs(runs)
    meta = g.confidence["reliability"]
    assert meta["stability_component"] < 10.0, (
        "a capped agent is still being credited with flawless run-to-run stability"
    )
    assert meta["composite_stdev"] > 0, "dispersion is still being read off the capped runs"


def test_the_oracle_the_defect_was_found_on():
    """Not a synthetic fixture: the stored artifact that exhibited it. An oracle
    proves the fix still addresses the real case, which a fixture written by the
    author of the fix cannot."""
    runs = _onyx()
    capped = [r.composite for r in runs]
    uncapped = [compute_composite(r.subscores, 0)[0] for r in runs]
    assert len(set(capped)) == 1, "fixture drift: the capped runs are no longer flattened"
    assert statistics.pstdev(uncapped) > 0.1, "fixture drift: the underlying runs no longer differ"
