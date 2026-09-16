"""Does this benchmark's own scoring pipeline give the same answer twice?

Taxonomy dimension 7 says reproducibility under load is a quality dimension. This
module is Proving Ground being measured by it, and it is a public commitment: the
number this produces is published alongside the dimension BEFORE any other system
is scored on it. If the number is bad it goes up anyway. A benchmark that exempts
itself from its own dimension is not worth reading.

WHAT IS HELD FIXED, AND WHY THAT IS THE WHOLE DESIGN. The agent's replies are
REPLAYED from a completed run artifact. The agent is never called. So agent
variance -- different sampling, different retrieval, different day -- is removed
by construction, and anything that moves is the SCORING PIPELINE moving. Grading
a live agent twice would mix the two and could not tell them apart, which is
exactly the confusion this dimension exists to name.

THREE ARMS, BECAUSE TWO CANNOT SUPPORT THE CONCLUSION.

    A   sequential, concurrency 1
    A'  sequential again, concurrency 1      <- the CONTROL
    B   concurrent, concurrency N

A frontier panel is sampled, not deterministic, so some verdicts will differ
between A and A' with no load involved at all. That is baseline judge
nondeterminism. Comparing only A against B would charge all of it to concurrency
and report a load effect that is really sampling noise -- a correct measurement
of the wrong thing. The reportable quantity is the DIFFERENCE:

    concurrency effect = flip_rate(A, B) - flip_rate(A, A')

and when that difference is within the control's own noise, the honest finding is
"no concurrency effect detected at this scale", not "zero".

WHAT COUNTS AS A FLIP. The pass/fail VERDICT changing, not the score moving.
Dimension 7 is explicit that the diff is over verdicts, not accuracy: a score
drifting 0.95 to 0.92 changes no one's decision, and a probe flipping pass to
fail changes a published grade. Score drift is reported too, as context.

The real production scoring object is invoked -- ``dimension.score_probe`` -- and
not a reimplementation of it, so this measures the thing that grades agents,
including its deterministic detectors and its thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from app.dimensions.base import Probe
from app.dimensions.catalog import REGISTRY
from app.suites import load_probes

BACKEND = Path(__file__).resolve().parents[2]


@dataclass
class ScoringItem:
    """One (probe, fixed agent reply) pair to push through the scoring pipeline."""

    probe_id: str
    dimension: str
    probe: Probe
    response: str
    latency_ms: float


@dataclass
class Verdict:
    probe_id: str
    dimension: str
    passed: bool
    score: float
    critical: bool


@dataclass
class ArmResult:
    label: str
    concurrency: int
    verdicts: dict[str, Verdict]
    wall_seconds: float
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- loading

def load_items(artifact: Path, suite: str = "practice", dimensions: list[str] | None = None,
               limit: int | None = None) -> list[ScoringItem]:
    """Pair each stored agent reply with the probe that produced it.

    The artifact keeps the reply but not the prompt, so the prompt comes from the
    suite. A probe present in the artifact and missing from the suite is SKIPPED
    LOUDLY rather than dropped: a silently shorter item set would make every rate
    below a fraction of a denominator nobody checked.
    """
    art = json.loads(artifact.read_text())
    runs = art.get("runs") or []
    if not runs:
        raise SystemExit(f"{artifact.name} has no runs to replay")

    items: list[ScoringItem] = []
    missing: list[str] = []
    for dim_id, probe_results in runs[0].items():
        if dimensions and dim_id not in dimensions:
            continue
        if dim_id not in REGISTRY:
            continue
        _factory, practice = REGISTRY[dim_id]
        path = practice if suite == "practice" else practice.parent.parent / suite / practice.name
        if not path.exists():
            missing.append(f"{dim_id}: no {suite} suite at {path}")
            continue
        by_id = {p.id: p for p in load_probes(path)}
        for pr in probe_results:
            if pr.get("error"):
                continue          # transport error in the original run: nothing to re-judge
            probe = by_id.get(pr["probe_id"])
            if probe is None:
                missing.append(f"{dim_id}/{pr['probe_id']}: not in {suite} suite")
                continue
            items.append(ScoringItem(
                probe_id=f"{dim_id}/{pr['probe_id']}", dimension=dim_id, probe=probe,
                response=pr.get("response", ""), latency_ms=float(pr.get("latency_ms") or 0.0),
            ))
    if missing:
        print(f"   note: {len(missing)} probe(s) in the artifact could not be paired with a "
              f"{suite} probe and are excluded:")
        for m in missing[:5]:
            print(f"     - {m}")
        if len(missing) > 5:
            print(f"     ... and {len(missing) - 5} more")
    if limit:
        items = items[:limit]
    return items


# -------------------------------------------------------------------- arms

async def run_arm(items: list[ScoringItem], judge, *, label: str, concurrency: int) -> ArmResult:
    """Push every item through the real scoring path at a given concurrency."""
    dims = {i.dimension: REGISTRY[i.dimension][0]() for i in items}
    verdicts: dict[str, Verdict] = {}
    errors: list[str] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(item: ScoringItem):
        async with sem:
            try:
                pr = await dims[item.dimension].score_probe(
                    item.probe, item.response, item.latency_ms, judge)
                verdicts[item.probe_id] = Verdict(
                    item.probe_id, item.dimension, bool(pr.passed), float(pr.score), bool(pr.critical))
            except Exception as exc:                      # noqa: BLE001 - recorded, not swallowed
                errors.append(f"{item.probe_id}: {type(exc).__name__}: {exc}")

    t0 = time.monotonic()
    if concurrency <= 1:
        for item in items:
            await one(item)
    else:
        await asyncio.gather(*(one(i) for i in items))
    return ArmResult(label, concurrency, verdicts, round(time.monotonic() - t0, 2), errors)


def compare(a: ArmResult, b: ArmResult) -> dict:
    """Verdict flips and score drift between two arms, over their shared probes."""
    shared = sorted(set(a.verdicts) & set(b.verdicts))
    if not shared:
        return {"n": 0, "flip_rate": None, "note": "no shared probes; nothing comparable"}
    flips = [p for p in shared if a.verdicts[p].passed != b.verdicts[p].passed]
    crit = [p for p in shared if a.verdicts[p].critical != b.verdicts[p].critical]
    drift = [abs(a.verdicts[p].score - b.verdicts[p].score) for p in shared]
    return {
        "n": len(shared),
        "flips": len(flips),
        "flip_rate": round(len(flips) / len(shared), 4),
        "flipped_probes": flips[:25],
        # The SCORES on both sides of every flip, not just which probe flipped.
        #
        # Added after the first published measurement could not explain itself. It
        # recorded three flipped probes and nothing about them, so the obvious
        # hypothesis -- that an unstable probe is one sitting on the 0.6 pass
        # threshold -- could not be tested against the run that produced it. It was
        # tested against the ORIGINAL graded scores instead and turned out to be
        # false for three of the four: they scored 0.75 to 0.95, comfortably
        # passing, and still flipped. Which is a more interesting result than the
        # hypothesis would have been, and the instrument could not see it.
        #
        # A measurement that records a rate but not the observations behind it can
        # report that something happened and never why.
        "flip_detail": [
            {"probe": pid,
             "a": {"score": round(a.verdicts[pid].score, 4), "passed": a.verdicts[pid].passed},
             "b": {"score": round(b.verdicts[pid].score, 4), "passed": b.verdicts[pid].passed},
             "delta": round(b.verdicts[pid].score - a.verdicts[pid].score, 4)}
            for pid in flips[:25]
        ],
        "critical_flips": len(crit),
        "mean_abs_score_drift": round(statistics.mean(drift), 4),
        "max_abs_score_drift": round(max(drift), 4),
        "score_identical_fraction": round(sum(1 for d in drift if d == 0) / len(drift), 4),
        "wall_seconds": {a.label: a.wall_seconds, b.label: b.wall_seconds},
    }


def flip_rate_upper_bound(flips: int, n: int) -> float | None:
    """95% upper bound on the true flip rate. None when n is 0.

    A measured rate of 0.00% is not a claim that the rate IS zero -- it is a claim
    that it is below what this many observations can resolve. Publishing the point
    estimate without the bound invites a reader to take "0.00%" as "never happens",
    which the sample does not support at any size. Zero events uses the rule of
    three (3/n); anything else uses a normal approximation, which is adequate at
    the sample sizes this runs at and is labelled so nobody mistakes it for exact.
    """
    if n <= 0:
        return None
    if flips == 0:
        return round(3.0 / n, 4)
    p = flips / n
    return round(min(1.0, p + 1.96 * (p * (1 - p) / n) ** 0.5), 4)


def summarise(control: dict, loaded: dict, *, concurrency: int) -> dict:
    """The reportable quantity: load effect NET of baseline judge nondeterminism."""
    if control.get("flip_rate") is None or loaded.get("flip_rate") is None:
        return {"verdict": "unmeasured", "reason": "an arm produced no comparable probes"}
    effect = round(loaded["flip_rate"] - control["flip_rate"], 4)
    # The control IS the noise floor. An effect no larger than it is not evidence of
    # a load effect, and saying "0.0%" would claim a precision this design does not
    # have. Reported as not-detected-at-this-scale, with the scale stated.
    detected = effect > control["flip_rate"] and loaded["flips"] > control["flips"]
    return {
        "baseline_flip_rate": control["flip_rate"],
        "loaded_flip_rate": loaded["flip_rate"],
        "loaded_flip_rate_ci95_upper": flip_rate_upper_bound(loaded["flips"], loaded["n"]),
        "baseline_flip_rate_ci95_upper": flip_rate_upper_bound(control["flips"], control["n"]),
        "concurrency_effect": effect,
        "concurrency": concurrency,
        "n_probes": loaded["n"],
        "verdict": ("concurrency effect detected" if detected else
                    "no concurrency effect detected above the control's own noise floor"),
        "reproducibility_score": round(1.0 - loaded["flip_rate"], 4),
    }


# ------------------------------------------------------------------- report

MEASUREMENTS = BACKEND / "data" / "reproducibility" / "measurements.json"
#: The redacted series that is published. See ``_redact`` for what comes out.
PUBLIC_MEASUREMENTS = BACKEND / "data" / "reproducibility" / "measurements.public.json"


def _redact(result: dict) -> dict:
    """Strip held-out probe identity, keep everything that carries meaning.

    The full record names the probes that flipped, which is the useful thing
    internally and is exactly what must not be published: those ids come from the
    PRIVATE suite, and the private suites are the moat this benchmark rests on. An
    id like ``security/adv_exf_12`` does not reveal a probe's content, but it does
    reveal that the held-out set contains at least twelve exfiltration probes, and
    a graded vendor should learn nothing about the suite from a page about
    reproducibility.

    What survives is the DIMENSION each flip fell in, which is the part a reader
    can act on -- it says where this pipeline is least stable without saying what
    it asks. Rates, counts, bounds, drift and spend are unaffected.
    """
    import copy

    out = copy.deepcopy(result)
    for arm in ("control", "loaded"):
        block = out.get(arm) or {}
        flipped = block.pop("flipped_probes", []) or []
        block.pop("flip_detail", None)
        dims: dict[str, int] = {}
        for pid in flipped:
            dims[pid.split("/")[0]] = dims.get(pid.split("/")[0], 0) + 1
        block["flipped_by_dimension"] = dict(sorted(dims.items()))
    return out


def record(result: dict, path: Path = MEASUREMENTS) -> Path:
    """Append this measurement to a dated SERIES, never overwrite the last one.

    A single stored number invites replacing a bad measurement with a better one
    and publishing only the better. The file is a list so that improving the
    pipeline and re-measuring ADDS a dated row beside the first, and the earlier
    number stays readable next to it. That is the commitment made when this
    dimension was published, expressed as a file format rather than as a promise.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    series = json.loads(path.read_text()) if path.exists() else []
    series.append(result)
    path.write_text(json.dumps(series, indent=2) + "\n")

    # The published series is written in the same call, from the same object. Two
    # files updated by two commands drift the moment somebody runs one of them.
    pub = path.parent / (path.stem + ".public.json")
    pub_series = json.loads(pub.read_text()) if pub.exists() else []
    pub_series.append(_redact(result))
    pub.write_text(json.dumps(pub_series, indent=2) + "\n")
    return path


def format_report(result: dict) -> str:
    s = result["summary"]
    lines = [
        "",
        "=== Proving Ground: reproducibility of its OWN scoring pipeline ===",
        f"  measured            {result['measured_on']}  ({result['judge']}, panel {result['panel']})",
        f"  replayed from       {result['artifact']}  ({result['n_items']} probes, agent replies FIXED)",
        f"  arms                A seq(1)  A' seq(1) control  B concurrent({s.get('concurrency')})",
        "",
        f"  baseline flip rate  {_pct(s.get('baseline_flip_rate'))}   (A vs A', no load: judge nondeterminism)",
        f"  loaded flip rate    {_pct(s.get('loaded_flip_rate'))}   (A vs B, under concurrency)",
        f"  concurrency effect  {_pct(s.get('concurrency_effect'))}   <- the reportable number",
        f"  95% upper bounds    loaded <= {_pct(s.get('loaded_flip_rate_ci95_upper'))}   "
        f"control <= {_pct(s.get('baseline_flip_rate_ci95_upper'))}",
        "                      (a measured 0.00% means 'below what this many probes can",
        "                       resolve', never 'never happens')",
        "",
        f"  score drift         mean {result['loaded']['mean_abs_score_drift']}  "
        f"max {result['loaded']['max_abs_score_drift']}  "
        f"identical {_pct(result['loaded']['score_identical_fraction'])}",
        f"  critical flips      {result['loaded']['critical_flips']}",
        f"  wall clock          seq {result['control']['wall_seconds']}  ",
        "",
        f"  {s.get('verdict')}",
    ]
    if result.get("spend", {}).get("usd_total") is not None:
        lines.append(f"  measurement cost    ${result['spend']['usd_total']:.2f} across "
                     f"{sum(v['calls'] for v in result['spend']['by_model'].values())} judge calls")
    if result["loaded"].get("flipped_probes"):
        lines.append(f"  flipped under load: {', '.join(result['loaded']['flipped_probes'][:8])}")
    return "\n".join(lines)


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:6.2f}%"


# ---------------------------------------------------------------------- CLI

async def main_async(args) -> int:
    from app.judges.judge import StubJudge, build_ensemble

    if args.judge == "stub":
        judge, panel = StubJudge(), ["stub"]
    elif args.judge == "flaky-stub":
        judge = FlakyStubJudge(flip_rate=args.flaky_rate, seed=args.seed)
        panel = [f"flaky-stub(p={args.flaky_rate})"]
    else:
        judge = build_ensemble(require=args.require)
        panel = [getattr(j, "vendor", j.__class__.__name__) for j in getattr(judge, "judges", [judge])]

    items = load_items(Path(args.artifact), suite=args.suite,
                       dimensions=args.dimensions.split(",") if args.dimensions else None,
                       limit=args.limit)
    if not items:
        raise SystemExit("no items to replay; refusing to report a rate over an empty set")
    print(f"   replaying {len(items)} probes through {', '.join(panel)}")

    arm_a = await run_arm(items, judge, label="A_seq", concurrency=1)
    arm_ctl = await run_arm(items, judge, label="A2_seq_control", concurrency=1)
    arm_b = await run_arm(items, judge, label="B_concurrent", concurrency=args.concurrency)
    for arm in (arm_a, arm_ctl, arm_b):
        if arm.errors:
            print(f"   {arm.label}: {len(arm.errors)} scoring error(s); first: {arm.errors[0]}")

    control = compare(arm_a, arm_ctl)
    loaded = compare(arm_a, arm_b)
    from app.judges.judge import SPEND
    spend = SPEND.summary()
    # Name the panel from the models that were actually BILLED, not from the class
    # name of the wrapper. The first published run recorded its panel as
    # "EnsembleJudge", which is the name of the object and tells a reader nothing
    # about which labs graded it -- and the independence of the panel is the whole
    # basis of the grade. The spend ledger records each model at the point of call,
    # so it cannot claim a lab that never answered.
    if spend.get("by_model"):
        panel = sorted(spend["by_model"])
    result = {
        "measured_on": date.today().isoformat(),
        "artifact": Path(args.artifact).name,
        "suite": args.suite,
        "judge": args.judge,
        "panel": panel,
        "n_items": len(items),
        "control": control,
        "loaded": loaded,
        "summary": summarise(control, loaded, concurrency=args.concurrency),
        "spend": spend,
        "note": args.note or "",
    }
    print(format_report(result))

    if args.record:
        p = record(result)
        print(f"\n   recorded in the dated series: {p}")
    else:
        print("\n   NOT recorded (--record to append to the published series)")
    return 0


class FlakyStubJudge:
    """A stub that deliberately flips a known fraction of verdicts.

    Exists so the harness can be made to FAIL ON PURPOSE at zero cost. A plain
    stub is deterministic, so a clean run against it proves only that nothing
    crashed -- it cannot distinguish a working instability detector from one that
    always reports zero, which is the single most important thing to know about
    this measurement before publishing a number from it.
    """

    def __init__(self, flip_rate: float = 0.2, seed: int = 7):
        import random
        self.flip_rate = flip_rate
        self._rng = random.Random(seed)

    def _score(self) -> float:
        return 0.1 if self._rng.random() < self.flip_rate else 1.0

    async def score_refusal(self, prompt, response, family="", refute=False):
        from app.judges.judge import Judgment
        return Judgment(self._score(), "flaky-stub")

    async def score_quality(self, prompt, response, criteria, context=None):
        from app.judges.judge import Judgment
        return Judgment(self._score(), "flaky-stub")

    async def score_criteria(self, prompt, response, rubric, context=None):
        from app.judges.judge import Judgment
        return Judgment(self._score(), "flaky-stub")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--artifact", required=True, help="a completed run artifact to replay")
    ap.add_argument("--suite", default="practice")
    ap.add_argument("--dimensions", default="", help="comma-separated; default all judged")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--judge", default="stub", choices=["stub", "flaky-stub", "live"])
    ap.add_argument("--flaky-rate", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--require", default=os.environ.get("PROVING_GROUND_REQUIRE_JUDGES", ""))
    ap.add_argument("--record", action="store_true",
                    help="append to the dated published series")
    ap.add_argument("--note", default="")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
