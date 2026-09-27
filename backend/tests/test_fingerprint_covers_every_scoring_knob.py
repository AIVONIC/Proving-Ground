"""Nothing that can move a published composite may be invisible to its identifier.

⛔ THIS IS THE REGRESSION GATE FOR A DEFECT IN THE GATE ITSELF.

The composite's identifier is derived from the scoring configuration so that two
grades can only be compared when they were computed the same way. It was derived
from a hand-written list of three inputs, and two constants that move a published
composite lived somewhere else:

    LATENCY_W=0.6 STABILITY_W=0.4  -> composite 85.07   id pgc-b4e796bd
    LATENCY_W=0.3 STABILITY_W=0.7  -> composite 85.27   id pgc-b4e796bd

The composite moved; the identifier did not. The lesson is not "we missed two
constants", it is that deriving from a hand-maintained LIST of inputs is still
hand-maintained -- the word "derived" reads as safe while the burden of keeping
the input set complete is untouched.

So these tests do not check that LATENCY_W is covered. They check the PROPERTY:
perturb each scoring knob in turn, and assert the identifier moves whenever the
composite does. A future knob is covered by the same test without anyone editing
it, which is the only version of this that survives a contributor who has not read
this file.
"""

from __future__ import annotations

import pytest

from app.dimensions.base import DimensionResult, ProbeResult
from app.scoring import config, scorer, version


def _run(reliability: float = 0.8, other: float = 0.85):
    dims = {}
    for dim in config.DIMENSION_WEIGHTS:
        s = reliability if dim == config.RELIABILITY_DIM else other
        dims[dim] = DimensionResult(dim, round(s * 10, 2),
                                    [ProbeResult("p", "baseline", True, s, False, "", "", 10.0)])
    return scorer.score_single_run(dims)


def _composite(spread=(0.8, 0.9, 0.7)) -> float:
    return scorer.aggregate_runs([_run(reliability=r) for r in spread]).composite


def test_the_reliability_blend_is_inside_the_fingerprint():
    """The exact hole. Perturb the blend, and BOTH the composite and the id must move.

    Fails while scorer.py still defines its own LATENCY_W/STABILITY_W, because then
    config.py holds values the scorer never reads and the fingerprint covers
    constants that do not drive anything -- coverage that looks real and is not.
    """
    before_c, before_id = _composite(), version.composite_id()
    config.LATENCY_W, config.STABILITY_W = 0.3, 0.7
    try:
        assert _composite() != before_c, (
            "changing the reliability blend in config.py did not move the composite. "
            "scorer.py is still reading its own copy, so the fingerprint covers a constant "
            "that drives nothing -- coverage in name only."
        )
        assert version.composite_id() != before_id, (
            "the blend moved the composite and the identifier stayed still. This is the "
            "original defect: a change that alters every published score is invisible to "
            "the one mechanism built to catch it."
        )
    finally:
        config.LATENCY_W, config.STABILITY_W = 0.6, 0.4
    assert version.composite_id() == before_id and _composite() == before_c


def test_the_blend_TARGET_is_inside_the_fingerprint():
    """RELIABILITY_DIM is a NAME, not a number, and it still moves a composite: it
    selects which weighted subscore receives the stability blend. Raised by
    aivonic-48, who asked rather than assumed it was covered."""
    before_id = version.composite_id()
    original = config.RELIABILITY_DIM
    config.RELIABILITY_DIM = "memory"
    try:
        assert version.composite_id() != before_id, (
            "redirecting the stability blend to a different weighted dimension left the "
            "identifier unchanged"
        )
    finally:
        config.RELIABILITY_DIM = original
    assert version.composite_id() == before_id


@pytest.mark.parametrize("name", sorted(
    k for k in vars(config) if k.isupper() and not k.startswith("_")))
def test_every_public_scoring_constant_is_swept(name):
    """The PROPERTY, not a list. A constant added to config.py tomorrow is covered
    by this test without anyone editing it."""
    assert name in version.scoring_fingerprint_inputs(), (
        f"{name} is a public constant in scoring/config.py and is not in the fingerprint. "
        f"Either it belongs in the sweep, or it is not scoring configuration and belongs "
        f"in another module."
    )


def test_an_unrepresentable_constant_is_REFUSED_not_skipped():
    """A silently skipped constant is the hole reopening in the place nobody looks."""
    config._TEMP_BAD = object()          # not swept: underscore-prefixed
    config.BAD_CONSTANT = object()
    try:
        with pytest.raises(TypeError, match="cannot represent"):
            version.scoring_fingerprint_inputs()
    finally:
        del config.BAD_CONSTANT, config._TEMP_BAD


def test_scorer_does_not_keep_its_own_copy_of_a_scoring_constant():
    """Two definitions of one knob is the defect in its most durable form: the
    fingerprint reads one and the arithmetic reads the other, and they agree right
    up until somebody edits the wrong file."""
    import inspect

    src = inspect.getsource(scorer)
    for knob in ("RELIABILITY_DIM", "LATENCY_W", "STABILITY_W"):
        assert f"\n{knob} =" not in src, (
            f"scorer.py defines its own {knob}. It must import it from scoring/config.py, "
            f"or the fingerprint and the arithmetic are reading different values."
        )
