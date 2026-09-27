"""The composite's identity is DERIVED, and must move when the scoring set moves.

A composite score is comparable to another only if both were computed the same way.
The obvious implementation is a version constant someone bumps by hand, which works
until the day somebody edits the weights and does not bump it -- and a stale version
is worse than none, because it actively asserts comparability that no longer holds.

So it is computed from the configuration, and these tests exist to prove the
computation actually responds. A fingerprint that never changes is indistinguishable
from a correct one right up to the moment it matters.
"""

from __future__ import annotations

import pytest

from app.scoring import config, version


def test_a_weight_change_changes_the_id():
    before = version.composite_id()
    config.DIMENSION_WEIGHTS["security"] = 17
    config.DIMENSION_WEIGHTS["memory"] = 3
    try:
        assert version.composite_id() != before
    finally:
        config.DIMENSION_WEIGHTS["security"] = 16
        config.DIMENSION_WEIGHTS["memory"] = 4
    assert version.composite_id() == before, "the id did not return to its original value"


def test_adding_a_weighted_dimension_changes_the_id():
    """The v0.4 case. When a new dimension acquires weight, every grade computed
    before that moment must stop claiming comparability -- automatically, with
    nobody having to remember."""
    before = version.composite_id()
    config.DIMENSION_WEIGHTS["a_future_weighted_dimension"] = 4
    try:
        assert version.composite_id() != before
    finally:
        del config.DIMENSION_WEIGHTS["a_future_weighted_dimension"]
    assert version.composite_id() == before


def test_a_tier_gate_change_changes_the_id():
    """Tier floors change the published TIER for unchanged behaviour, so they are
    part of the measurement's identity even though they do not touch the composite."""
    before = version.composite_id()
    original = config.TIERS["Elite"]
    config.TIERS["Elite"] = (91.0, 9.0, 8.0)
    try:
        assert version.composite_id() != before
    finally:
        config.TIERS["Elite"] = original
    assert version.composite_id() == before


def test_a_missing_id_is_never_treated_as_a_match():
    """Grades written before this existed carry no id. Assuming they match the
    current configuration is a default read as a measurement."""
    assert version.comparable(None, None) is False
    assert version.comparable(None, "pgc-12345678") is False
    assert version.comparable("pgc-12345678", "pgc-12345678") is True
    assert version.comparable("pgc-12345678", "pgc-87654321") is False


def test_methodology_version_is_separate_from_the_composite_id():
    """Two jobs, two names. A prose clarification must not invalidate a score, and a
    weight change must, and one string cannot do both."""
    before = version.composite_id()
    original = version.METHODOLOGY_VERSION
    version.METHODOLOGY_VERSION = "0.9"
    try:
        assert version.composite_id() == before, (
            "editing the methodology's prose version changed the composite id, which would "
            "invalidate every published grade for a documentation edit"
        )
    finally:
        version.METHODOLOGY_VERSION = original


def test_the_fingerprint_inputs_are_publishable_and_reproducible():
    """A reader must be able to recompute it. A fingerprint nobody can reproduce is
    an assertion, not a check."""
    inputs = version.scoring_fingerprint_inputs()
    # The shape is DERIVED from scoring/config.py, so this asserts the property
    # (every public constant is present) rather than a fixed set of three keys --
    # which is what the fingerprint used to be, and why it missed two knobs.
    expected = {k for k in vars(config) if k.isupper() and not k.startswith("_")}
    assert set(inputs) == expected, "the fingerprint no longer matches the config module"
    assert sum(inputs["DIMENSION_WEIGHTS"].values()) == 100
    import hashlib, json
    blob = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    assert version.composite_id() == "pgc-" + hashlib.sha256(blob.encode()).hexdigest()[:8]
