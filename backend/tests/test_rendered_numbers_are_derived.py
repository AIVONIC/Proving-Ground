"""The numbers the page RENDERS must match the values they claim to state.

⛔ WHY: two defects shipped on one page while 211 tests passed.

  1. STALE - "the field really is that close" was true at n=3 (span under a point)
     and false at n=5 (span 2.56, top entry separable). A test pinned the literal
     string "that close", so correcting the page BROKE the test and the test named
     the false wording as the thing to protect. The suite was arguing for the error.

  2. NEVER TRUE - the Elite gate rendered as "a composite of 90 with every dimension
     at 8.0 or above". The gate is (composite, security, min_any) = (90, 9.0, 8.0):
     the SECURITY floor was missing entirely, so a reader could correctly conclude an
     agent qualifies when it does not. Stale prose misleads about a number; an
     incomplete rule misleads about a DECISION.

Both were fixed by deriving them. But a derivation nobody verifies is one refactor
from becoming a literal again - which is exactly how the first one got there. These
tests assert the rendered output against the source of truth, so the next
hand-written restatement fails at commit rather than at someone's careful reading.

Each is paired with a MUTATION: corrupt the source of truth and confirm the test
notices. A test that cannot fail is a test that proves nothing, and that is the
defect class this file exists for.
"""
from __future__ import annotations

import html
import re

from app.leaderboard.render import _elite_rule, _radar_cap, tie_structure
from app.leaderboard.store import load
from app.scoring import config


def _ranked():
    return [e for e in load() if e.get("published", True) and e.get("ranked", True)]


def _text(s: str) -> str:
    return " ".join(html.unescape(re.sub("<[^>]+>", " ", s)).split())


def test_rendered_elite_gate_matches_config():
    """Every floor in the gate must appear, not just the ones someone remembered."""
    comp, sec, anyd = config.TIERS["Elite"]
    t = _text(_elite_rule(_ranked()))
    assert f"composite of {comp:.0f}" in t, t
    assert f"security at {sec:.1f}" in t, "the SECURITY floor is the one that went missing"
    assert f"every dimension at {anyd:.1f}" in t, t


def test_elite_gate_test_fails_when_config_moves():
    """MUTATION. If this passes unchanged after moving the gate, the assertion above
    is decorative."""
    orig = config.TIERS["Elite"]
    try:
        config.TIERS["Elite"] = (92.0, 9.5, 8.5)
        t = _text(_elite_rule(_ranked()))
        assert "composite of 92" in t and "security at 9.5" in t, \
            "the rendered gate did not follow config - it is hand-written again"
        assert f"composite of {orig[0]:.0f}" not in t
    finally:
        config.TIERS["Elite"] = orig


def test_rendered_tie_claim_matches_the_computed_bands():
    """The caption's tie membership and count must be the computed ones."""
    e = _ranked()
    bands, pairs = tie_structure(e)
    cap = _text(_radar_cap(e))
    total = len(e) * (len(e) - 1) // 2
    assert f"{pairs} are supported by the measurement" in cap, cap
    assert f"Of {total} possible orderings" in cap, cap
    big = max(bands, key=len)
    if len(big) > 1:
        for member in big:
            assert member["name"] in cap, f"{member['name']} is in the tie band but not named"


def test_tie_claim_follows_the_data():
    """MUTATION. Move an agent clear of the band; the caption must change with it."""
    e = [dict(x) for x in _ranked()]
    before = _text(_radar_cap(e))
    low = min(e, key=lambda x: x["composite"])
    low["composite"] = 70.0
    low["ci95"] = [69.5, 70.5]
    after = _text(_radar_cap(e))
    assert before != after, "the caption did not move with the data - it is baked"
    assert low["name"] not in after.split("are a statistical tie")[0], \
        "an agent moved 16 points clear is still described as tied"
    # ⛔ ASSERT THE COUNT TRACKS, NOT MERELY THAT THE TEXT CHANGED. A baked `pairs`
    # that happens to equal today's value passes a text-difference check, because
    # the span and band membership still move around it. The first mutation test
    # written here baked pairs=4 - the value it already had - and passed, which
    # proved nothing. The count is the number that must follow the data.
    _, pairs_after = tie_structure(e)
    assert f"{pairs_after} are supported by the measurement" in after, \
        f"caption did not report the recomputed count {pairs_after} - it is baked"


def test_no_hand_written_score_range_in_prose():
    """The board once carried a hard-coded '6.9 and 9.9' score range beside real
    subscores of 7.34-9.92. Any such literal range must be derived or absent."""
    cap = _text(_radar_cap(_ranked()))
    assert not re.search(r"between \d+\.\d+ and \d+\.\d+", cap), \
        "a literal score range in prose: derive it or drop it"


def test_robust_orderings_are_still_robust_against_the_live_data():
    """ROBUST_ORDERINGS is a MEASURED SET written into the renderer, which is the
    exact shape of claim this file exists to police. It must be re-derived from the
    artifacts on every run: any ordering it publishes as "holds under both methods"
    must still be distinct under Welch on the live per-run composites AND under a
    paired bootstrap over probe ids, on every seed tried. A reduced bootstrap keeps
    the test fast; the direction that matters - claiming an ordering that has
    stopped being robust - is caught at any resolution."""
    import glob, json, math, random, statistics
    from collections import defaultdict
    from pathlib import Path
    from app.leaderboard.render import ROBUST_ORDERINGS, DRIFT_NOT_EXCLUDED
    from app.scoring.scorer import compute_composite
    BACKEND = Path(__file__).resolve().parents[1]
    _T = {4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306}

    def runs_of(f):
        out = []
        for run in json.loads(Path(f).read_text())["runs"]:
            subs, crit = {}, 0
            for dim, ps in run.items():
                if not isinstance(ps, list) or not ps:
                    continue
                v = [p["score"] for p in ps if isinstance(p, dict) and p.get("score") is not None]
                crit += sum(1 for p in ps if isinstance(p, dict) and p.get("critical"))
                if v:
                    subs[dim] = round(statistics.mean(v) * 10, 2)
            out.append(compute_composite(subs, crit)[0])
        return out

    def probe_tbl(f):
        acc, dim_of = defaultdict(list), {}
        for run in json.loads(Path(f).read_text())["runs"]:
            for dim, ps in run.items():
                if not isinstance(ps, list):
                    continue
                for p in ps:
                    if isinstance(p, dict) and p.get("score") is not None:
                        acc[p["probe_id"]].append(p["score"]); dim_of[p["probe_id"]] = dim
        return {k: (dim_of[k], statistics.mean(v)) for k, v in acc.items()}

    def comp(tbl, ids):
        by = defaultdict(list)
        for i in ids:
            d, s = tbl[i]; by[d].append(s)
        return compute_composite({d: round(statistics.mean(v) * 10, 2) for d, v in by.items() if v}, 0)[0]

    arts = {json.loads(Path(f).read_text())["agent"]: f
            for f in glob.glob(str(BACKEND / "data/runs/merged/*_n5.json"))}
    for a, b in ROBUST_ORDERINGS:
        assert a in arts and b in arts, f"{a} or {b} has no merged artifact"
        # Welch on per-run composites
        x, y = runs_of(arts[a]), runs_of(arts[b])
        mx, my = statistics.mean(x), statistics.mean(y)
        se = math.sqrt(statistics.variance(x) / len(x) + statistics.variance(y) / len(y))
        df = (statistics.variance(x)/len(x) + statistics.variance(y)/len(y)) ** 2 / (
            (statistics.variance(x)/len(x))**2/(len(x)-1) + (statistics.variance(y)/len(y))**2/(len(y)-1))
        gap = mx - my
        assert gap > _T.get(int(round(df)), 1.96) * se and gap > DRIFT_NOT_EXCLUDED, \
            f"{a} > {b} is published as robust but no longer distinct under Welch"
        # paired bootstrap, 6 seeds x 600 resamples - every seed must be distinct
        ta, tb = probe_tbl(arts[a]), probe_tbl(arts[b])
        shared = sorted(set(ta) & set(tb))
        for s in range(6):
            r = random.Random(500 + s)
            d = sorted(comp(ta, smp) - comp(tb, smp)
                       for smp in ([shared[r.randrange(len(shared))] for _ in shared] for _ in range(600)))
            lo = d[int(0.025 * len(d))]
            assert lo > 0, (f"{a} > {b} is published as robust across seeds but seed {500+s} "
                            f"gives a bootstrap CI-low of {lo:+.2f}")
