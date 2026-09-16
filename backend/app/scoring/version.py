"""The identity of a measurement, DERIVED from the scoring configuration.

WHY THIS EXISTS. A composite score is only comparable to another composite score
computed the same way. "The same way" means: the same dimension set, the same
weights, the same critical cap, the same tier gates. Change any of those and the
number still looks like a number on the same 0..100 scale, still sorts next to
the old one on a leaderboard, and means something different.

The obvious fix is a version constant someone bumps by hand. That works until the
day somebody edits the weights and does not bump it, which is the same failure
this benchmark's own taxonomy records for a monitor whose state version was
hand-maintained against a list that moved underneath it. So the version is not
declared here. It is COMPUTED from the configuration itself, which makes
forgetting impossible: there is no second place to update.

    composite_id  "pgc-3f9a1c42"   changes automatically when the scoring
                                   configuration changes in any way that could
                                   move a published number

    methodology   "0.3"            editorial, hand-set, describes the DOCUMENT

Those two are deliberately separate. The methodology version tracks prose that a
reader cites; the composite id tracks whether two numbers may be compared. A
prose clarification must not invalidate a score, and a weight change must, and a
single version string cannot do both jobs. (One name, two jobs, is its own entry
in the taxonomy.)

WHAT IS AND IS NOT IN THE FINGERPRINT. In: every dimension that carries composite
weight, its weight, the critical cap, and the tier gates -- everything that can
change a published composite or tier for unchanged agent behaviour. Out: the
taxonomy dimensions, which are reported alongside the composite and carry no
weight. Adding one must NOT invalidate existing grades, because it does not
change any of them. On the day the taxonomy folds into the composite (v0.4), it
acquires weights, and at that moment it enters the fingerprint automatically and
every pre-v0.4 grade stops claiming comparability. Nobody has to remember.
"""

from __future__ import annotations

import hashlib
import json

from app.scoring.config import CRITICAL_CAP, DIMENSION_WEIGHTS, TIERS

# Editorial version of METHODOLOGY.md. Prose only. Bumping this does NOT make an
# old grade incomparable, and must never be used to claim that it does.
METHODOLOGY_VERSION = "0.3"


def scoring_fingerprint_inputs() -> dict:
    """Exactly what the fingerprint is computed over, in a stable order.

    Returned rather than kept private so the site can publish it: a reader who
    wants to know whether two scores are comparable can recompute this, and a
    fingerprint nobody can reproduce is an assertion rather than a check.
    """
    return {
        "weights": {d: DIMENSION_WEIGHTS[d] for d in sorted(DIMENSION_WEIGHTS)},
        "critical_cap": CRITICAL_CAP,
        "tiers": {t: list(TIERS[t]) for t in sorted(TIERS)},
    }


def composite_id() -> str:
    """Stable id for the current scoring configuration. Two grades are comparable
    if and only if they carry the same one."""
    blob = json.dumps(scoring_fingerprint_inputs(), sort_keys=True, separators=(",", ":"))
    return "pgc-" + hashlib.sha256(blob.encode()).hexdigest()[:8]


def comparable(a: str | None, b: str | None) -> bool:
    """Whether two recorded composite ids may be compared as the same measurement.

    A missing id is NOT treated as a match. Grades written before this existed
    carry no id, and silently assuming they match the current configuration is
    the exact error this module exists to prevent -- it would be a default read
    as a measurement.
    """
    return bool(a) and bool(b) and a == b


def describe() -> str:
    """One line for a report header or a page footer."""
    inputs = scoring_fingerprint_inputs()
    return (
        f"composite {composite_id()} "
        f"({len(inputs['weights'])} weighted dimensions, "
        f"methodology v{METHODOLOGY_VERSION})"
    )
