"""A re-promote must not silently change who can see an entry.

⛔ TWO FIELDS, SAME DEFECT, BOTH FOUND ON THE n=5 PROMOTION AND BOTH UNSAFE BY
DEFAULT. `ranked` and `published` are read by render.py, store.py and certs.py
with permissive defaults (`e.get(..., True)`) and were written by NOTHING. So
every re-promote dropped them and they reverted to True:

  ranked     -> SPARK, our OWN agent, deliberately recused, would have entered
                the public ranking.
  published  -> Onyx, withheld pending vendor disclosure, would have GONE PUBLIC.

Nothing failed. Both were caught by reading the flags back rather than by any
error. The shape to watch for: a property that lives only in the stored record,
is read with a permissive default, and is written by no code path - every
regeneration silently reverts it, and here the default was the unsafe value both
times.
"""
from __future__ import annotations

from app.leaderboard.promote import entry_from_run

RUN = {"grade": {"composite": 80.0, "tier": "Premium", "critical_failures": 0,
                 "subscores": {}, "confidence": {"runs": 5}, "composite_id": "pgc-x"}}
META = {"id": "a", "name": "A", "vendor": "V", "graded_at": "2026-09-18"}


def _entry(meta_extra=None, prev=None):
    # bypass the full-grade check by giving the run every registered dimension
    from app.dimensions.catalog import REGISTRY
    run = {"grade": {**RUN["grade"], "subscores": {d: 8.0 for d in REGISTRY}}}
    return entry_from_run(run, {**META, **(meta_extra or {})}, prev)


def test_withheld_entry_stays_withheld_across_a_repromote():
    assert _entry(prev={"published": False})["published"] is False


def test_recused_entry_stays_recused_across_a_repromote():
    assert _entry(prev={"ranked": False})["ranked"] is False


def test_self_operated_is_recused_by_default():
    """We built it, we grade it. A benchmark that ranks its author's own product
    has spent the only asset it has."""
    assert _entry({"self_operated": True})["ranked"] is False


def test_third_party_is_ranked_and_published_by_default():
    """NEGATIVE CONTROL. Without this the two above would also pass if the code
    simply recused and withheld everything."""
    e = _entry()
    assert e["ranked"] is True and e["published"] is True


def test_an_explicit_prior_choice_beats_the_self_operated_inference():
    assert _entry({"self_operated": True}, prev={"ranked": True})["ranked"] is True
