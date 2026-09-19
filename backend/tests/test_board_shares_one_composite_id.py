"""Every ranked entry must carry the same composite_id, or the board breaks its
own published rule.

METHODOLOGY.md:118 - "Two grades may be compared as the same measurement if and
only if they carry the same identifier."

⛔ IT WAS BROKEN LIVE. Langflow was re-graded after the 2026-09-18 fingerprint
change and carried pgc-9c569e75; the other five, merged from earlier runs,
carried pgc-b4e796bd. Ranked and tie-tested against each other on a page that
says they must not be. Found by the Sol Medium review, confirmed by aivonic-52.

The cause was in extend_runs.py: `**base["grade"]` carried the BASE artifact's id
onto a composite the script had just recomputed through today's production code.
That is the OPPOSITE of the re-stamping refused elsewhere: re-stamping asserts an
old measurement was taken under a config that did not exist yet, whereas a merged
grade is a NEW computation and must carry the identity of the code that produced
it. The straddle record preserves that its inputs spanned two configs.

Onyx carries None - correct: the rule treats a grade recorded before the
identifier existed as "not comparable", and Onyx is withheld from every
comparison anyway.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

from app.leaderboard.store import load
from app.scoring.version import composite_id

BACKEND = Path(__file__).resolve().parents[1]


def _ranked():
    return [e for e in load() if e.get("published", True) and e.get("ranked", True)]


def test_all_ranked_entries_share_one_composite_id():
    ids = {e.get("composite_id") for e in _ranked()}
    assert len(ids) == 1, (
        f"ranked entries carry {len(ids)} different composite_ids {ids}; the "
        "methodology says they may only be compared if they carry the same one")
    assert None not in ids, "a ranked entry has no composite_id at all"


def test_ranked_entries_carry_the_LIVE_composite_id():
    """Not merely consistent with each other - consistent with the code that
    would grade them today. A board of six matching STALE ids would pass the
    test above and still be incomparable with the next grade taken."""
    live = composite_id()
    for e in _ranked():
        assert e.get("composite_id") == live, \
            f"{e['name']} carries {e.get('composite_id')} but the live config is {live}"


def test_merged_artifacts_carry_the_live_id_not_the_inherited_one():
    """The defect lived in the merge, so assert on the merged artifacts directly.
    A promoted entry copies from these; if these are wrong the board follows."""
    live = composite_id()
    for f in glob.glob(str(BACKEND / "data/runs/merged/*_n5.json")):
        d = json.loads(Path(f).read_text())
        assert d["grade"].get("composite_id") == live, \
            f"{Path(f).name} carries {d['grade'].get('composite_id')}, live is {live}"
        # and the straddle, where present, must name a DIFFERENT base id - that is
        # the whole record of what happened, and it must not be rewritten to match
        st = (d.get("merged") or {}).get("scoring_config_straddle")
        if st:
            assert st["base_composite_id"] != live, \
                "the straddle's base id was rewritten to the live id - that is re-stamping"
