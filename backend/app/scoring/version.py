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

from app.scoring import config

# Editorial version of METHODOLOGY.md. Prose only. Bumping this does NOT make an
# old grade incomparable, and must never be used to claim that it does.
METHODOLOGY_VERSION = "0.3"


def _normalise(value):
    """Make a config value deterministically comparable, or REFUSE it.

    Tuples and lists are the same thing for this purpose, and dict ordering must
    not matter, so both are canonicalised. Anything that cannot be represented
    raises instead of being skipped -- a constant quietly dropped from the
    fingerprint is precisely the hole this sweep exists to close, and a silent
    skip would reopen it in the one place nobody would look.
    """
    if isinstance(value, dict):
        return {str(k): _normalise(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(
        f"scoring config holds a value the fingerprint cannot represent: {type(value).__name__}. "
        f"Give it a JSON-representable form, or move it out of scoring/config.py if it is not "
        f"scoring configuration. Do NOT special-case it here: a constant excluded from the "
        f"fingerprint can move a published composite invisibly."
    )


def scoring_fingerprint_inputs() -> dict:
    """Exactly what the fingerprint is computed over, in a stable order.

    ⛔ DERIVED FROM THE MODULE, NOT FROM A LIST MAINTAINED HERE.

    This used to name three inputs: DIMENSION_WEIGHTS, CRITICAL_CAP and TIERS. That
    was a hand-maintained list, and a value derived from a hand-maintained list is
    still hand-maintained -- it simply moves the thing you must remember one step
    further away, where the word "derived" makes it look safe. It failed exactly as
    a hand-bumped version fails: LATENCY_W and STABILITY_W lived in scorer.py,
    changed the composite through the reliability subscore, and were invisible here.

    So every public constant in ``scoring/config.py`` is swept. A new scoring knob
    added to that module is covered with nobody having to register it, which is the
    only version of this that survives contact with a future contributor.

    Returned rather than kept private so the site can publish it: a reader who
    wants to know whether two scores are comparable can recompute this, and a
    fingerprint nobody can reproduce is an assertion rather than a check.
    """
    return {
        name: _normalise(getattr(config, name))
        for name in sorted(vars(config))
        if name.isupper() and not name.startswith("_")
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


#: How an absent id is stated. A grade taken before the identifier existed carries
#: none, and the reader must be told that plainly rather than shown a blank: a
#: missing field reads as "nothing to say here", when what it means is "this number
#: may not be compared with the one next to it".
NO_ID_PHRASE = ("not recorded {dash} predates scoring-config tracking, so this score is "
                "{b}not directly comparable{b_}to one carrying an id")


def describe_identity(composite: str | None, *, html: bool = False) -> str:
    """One sentence naming the scoring configuration a grade was computed under.

    Shared by every surface that shows it -- the certificate verification page and
    the per-agent scorecard -- because it is a claim about comparability made to a
    buyer, and two hand-written copies of such a claim drift on whichever page
    nobody re-reads. Exactly the reason the conflict disclosure has one definition.
    """
    if composite:
        return f"<code>{composite}</code>" if html else composite
    if html:
        return NO_ID_PHRASE.format(dash="&mdash;", b="<b>", b_="</b> ")
    return NO_ID_PHRASE.format(dash="-", b="", b_=" ")


def describe() -> str:
    """One line for a report header or a page footer."""
    inputs = scoring_fingerprint_inputs()
    return (
        f"composite {composite_id()} "
        f"({len(config.DIMENSION_WEIGHTS)} weighted dimensions, "
        f"methodology v{METHODOLOGY_VERSION})"
    )
