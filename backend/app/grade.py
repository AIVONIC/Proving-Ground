"""Grader orchestrator + CLI.

Runs the selected dimensions through an adapter, aggregates over runs, and writes
a run artifact (grade + full transcripts) for audit. This is the end-to-end path:
adapter -> dimensions -> judge -> scoring -> certificate-shaped result.

Usage:
    python -m app.grade --agent spark --base-url http://host:8011 \
        --dimensions security,conversational_quality --runs 1 --judge stub
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import time
from pathlib import Path

from app.adapters.aivonic import aivonic_adapter
from app.adapters import RestApiAdapter
from app.adapters.config import RestAdapterConfig
from app.adapters.socketio_adapter import aivonic_socketio_adapter
from app.dimensions.catalog import REGISTRY
from app.judges.coverage import judge_coverage, shortfall
from app.judges.judge import ClaudeJudge, OpenAIJudge, StubJudge, build_ensemble
from app.scoring.reliability import (
    difficulty_breakdown,
    format_reliability,
    pass_k_curve,
    severity_summary,
)
from app.scoring.scorer import aggregate_runs, score_single_run
from app.suites import load_probes

BACKEND = Path(__file__).resolve().parents[1]


def _resolve_suite(practice_path: Path, suite: str) -> Path:
    """Map a dimension's practice-suite path onto the chosen suite dir. 'private' is
    the held-out set the agent never sees, used for official grades; 'practice' is
    the public set vendors self-test on. A missing private file is a hard error, so
    an official grade can never silently fall back to the public probes."""
    if suite == "practice":
        return practice_path
    resolved = practice_path.parent.parent / suite / practice_path.name
    if not resolved.exists():
        raise SystemExit(f"suite '{suite}' has no probes for {practice_path.name} at {resolved}")
    return resolved


async def _grade_once(adapter, dim_ids, judge, suite="practice"):
    dim_results = {}
    for dim_id in dim_ids:
        factory, practice = REGISTRY[dim_id]
        probes = load_probes(_resolve_suite(practice, suite))
        dim_results[dim_id] = await factory().run(adapter, probes, judge)
    return score_single_run(dim_results), dim_results


async def _grade_once_parallel(adapter_factory, dim_ids, judge, suite, concurrency):
    """Grade dimensions concurrently, each on its OWN adapter connection. Probes are
    isolated sessions (full reset between them), so parallel dimensions never share
    state. A semaphore bounds concurrency to what the agent can serve, so wall-clock
    drops from sum-of-dimensions to roughly the slowest dimension times ceil(N/cap)."""
    sem = asyncio.Semaphore(concurrency)

    async def grade_dim(dim_id):
        async with sem:
            factory, practice = REGISTRY[dim_id]
            probes = load_probes(_resolve_suite(practice, suite))
            adapter = adapter_factory()
            try:
                return dim_id, await factory().run(adapter, probes, judge)
            finally:
                await adapter.aclose()

    results = await asyncio.gather(*[grade_dim(d) for d in dim_ids])
    return dict(results)


async def grade_agent(adapter_factory, dim_ids, judge, runs: int, suite="practice", concurrency: int = 1):
    run_grades, all_dim_results = [], []
    for _ in range(runs):
        if concurrency > 1:
            dim_results = await _grade_once_parallel(adapter_factory, dim_ids, judge, suite, concurrency)
            grade = score_single_run(dim_results)
        else:
            adapter = adapter_factory()
            try:
                grade, dim_results = await _grade_once(adapter, dim_ids, judge, suite)
            finally:
                await adapter.aclose()
        run_grades.append(grade)
        all_dim_results.append(dim_results)
    return aggregate_runs(run_grades), all_dim_results


def _format_report(agent: str, grade) -> str:
    lines = [
        f"\n=== Grade: {agent} ===",
        f"Composite: {grade.composite}/100   Tier: {grade.tier}"
        + ("   [INCOMPLETE: partial dimension set]" if grade.incomplete else "")
        + ("   [CAPPED: critical failure]" if grade.capped else ""),
        f"Critical failures: {grade.critical_failures}",
        "Subscores:",
    ]
    for d in sorted(grade.subscores):
        lines.append(f"  {d:<28} {grade.subscores[d]:>5}/10")
    if grade.confidence:
        c = grade.confidence
        lines.append(f"Confidence: runs={c['runs']} variance={c.get('variance')} "
                     f"CI95=[{c.get('ci95_low')}, {c.get('ci95_high')}]")
    return "\n".join(lines)


def _write_run(agent: str, grade, all_dim_results) -> Path:
    out_dir = BACKEND / "data/runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{agent}_{stamp}.json"
    transcripts = [
        {dim: [dataclasses.asdict(pr) for pr in dr.probe_results] for dim, dr in run.items()}
        for run in all_dim_results
    ]
    path.write_text(json.dumps({
        "agent": agent,
        "grade": dataclasses.asdict(grade),
        "reliability": {
            "pass_k": pass_k_curve(all_dim_results),
            "by_difficulty": difficulty_breakdown(all_dim_results),
            "breach_severity": severity_summary(all_dim_results),
        },
        "runs": transcripts,
    }, indent=2))
    return path


def _build_factory(args):
    if args.socketio_agent_id:
        return lambda: aivonic_socketio_adapter(args.agent, args.socketio_agent_id, base_url=args.base_url or "https://agents.aivonic.ai")
    if args.adapter_config:
        cfg = RestAdapterConfig(**json.loads(Path(args.adapter_config).read_text()))
        return lambda: RestApiAdapter(cfg)
    if args.base_url:
        return lambda: aivonic_adapter(args.agent, args.base_url)
    raise SystemExit("provide --socketio-agent-id (Aivonic widget agent), --base-url (REST), or --adapter-config")


def _load_profile(agent: str, path: str | None) -> str:
    """Load the agent's declared capability manifest and render it to a compact string.

    Accepts either an explicit ``--profile`` path or the default
    ``data/profiles/<agent>.json``. The manifest declares what the agent is FOR;
    task-relative dimensions grade against this scope instead of a fixed task list.
    A file with a ``profile`` string is used verbatim; otherwise role/can/cannot are
    rendered. Missing file => empty string => grade against the raw rubric (back-compat).
    """
    p = Path(path) if path else BACKEND / "data" / "profiles" / f"{agent}.json"
    if not p.exists():
        return ""
    data = json.loads(p.read_text())
    if isinstance(data.get("profile"), str):
        return data["profile"].strip()
    parts = []
    if data.get("role"):
        parts.append(str(data["role"]).strip())
    if data.get("can"):
        parts.append("It can: " + "; ".join(data["can"]) + ".")
    if data.get("cannot"):
        parts.append("It cannot: " + "; ".join(data["cannot"]) + ".")
    return " ".join(parts).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade an agent through the Proving Ground engine.")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--base-url", help="agent base URL (REST preset, or Socket.IO host override)")
    ap.add_argument("--socketio-agent-id", help="grade one of our own Socket.IO widget agents by agent_id")
    ap.add_argument("--adapter-config", help="path to a RestAdapterConfig JSON for any REST agent")
    ap.add_argument("--dimensions", default=",".join(REGISTRY.keys()),
                    help="comma-separated dimension ids (default: all registered)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--suite", choices=["practice", "private"], default="practice",
                    help="private = held-out grading set (official grades); practice = public self-test set")
    ap.add_argument("--judge", choices=["stub", "claude", "openai", "ensemble"], default="stub",
                    help="ensemble = multi-vendor frontier panel (Claude+OpenAI); the grade-affecting default")
    ap.add_argument("--profile", help="path to the agent's declared capability manifest JSON "
                                       "(default: data/profiles/<agent>.json if present)")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="grade this many dimensions in parallel (1 = sequential, gentlest on a live agent)")
    ap.add_argument("--probe-delay-ms", type=int, default=0,
                    help="sleep this long between probes; throttles load when grading a live production agent")
    args = ap.parse_args()

    if args.probe_delay_ms:
        os.environ["PROVING_GROUND_PROBE_DELAY_MS"] = str(args.probe_delay_ms)

    dim_ids = [d.strip() for d in args.dimensions.split(",") if d.strip()]
    unknown = [d for d in dim_ids if d not in REGISTRY]
    if unknown:
        raise SystemExit(f"unknown dimensions: {unknown}. available: {sorted(REGISTRY)}")

    profile = _load_profile(args.agent, args.profile)
    if args.judge == "ensemble":
        judge = build_ensemble(profile)
    elif args.judge == "claude":
        judge = ClaudeJudge(agent_profile=profile)
    elif args.judge == "openai":
        judge = OpenAIJudge(agent_profile=profile)
    else:
        judge = StubJudge()
    if profile:
        print(f"[capability-relative grading: declared scope loaded for {args.agent}]")
    factory = _build_factory(args)

    grade, all_dim_results = asyncio.run(grade_agent(factory, dim_ids, judge, args.runs, args.suite, concurrency=args.concurrency))
    print(_format_report(args.agent, grade))
    print(format_reliability(all_dim_results))
    path = _write_run(args.agent, grade, all_dim_results)

    # Panel completeness, checked on the RESULT and not only at construction.
    # PROVING_GROUND_REQUIRE_JUDGES refuses to start when a vendor's key is
    # missing. It cannot see the failure that a key which is present but whose
    # calls fail: the ensemble drops that judge per probe, correctly, so one
    # vendor outage cannot sink an hour of grading -- and the run then reports a
    # full panel it did not have. Gemini's free tier is daily-quota-capped and
    # did exactly this on 2026-08-25, covering 0 of 441 judgments on one grade
    # that was published as four-lab.
    import json as _json
    cov = judge_coverage(_json.loads(path.read_text()))
    if cov:
        print("\nJudge panel coverage: "
              + ", ".join(f"{k} {v:.0%}" for k, v in sorted(cov.items())))
        required = [v.strip().lower()
                    for v in (os.environ.get("PROVING_GROUND_REQUIRE_JUDGES") or "").split(",")
                    if v.strip() and v.strip().lower() != "all"] or list(cov)
        short = shortfall(_json.loads(path.read_text()), required)
        if short:
            print("WARNING: the panel this run required did not hold. Short: "
                  + ", ".join(f"{k} {v:.0%} of judgments" for k, v in short.items())
                  + ".\n         The grade stands -- the remaining labs judged every probe -- but it "
                  "must NOT be published as though those labs took part.")
    print(f"\nRun artifact: {path}")
    if args.judge == "stub":
        print("NOTE: stub judge used (offline heuristic). Scores are for plumbing validation, not a real grade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
