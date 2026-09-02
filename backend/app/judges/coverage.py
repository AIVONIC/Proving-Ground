"""Who actually judged a run, measured from its artifact rather than declared.

`PROVING_GROUND_REQUIRE_JUDGES` guards the panel at CONSTRUCTION: it refuses to
start when a vendor's key is missing. That was built for 2026-07-29, when an
xAI key removed from a sourced .env took Grok out of a live regrade silently. It
does not cover the failure that actually happened on 2026-08-25, which is the
same hole one step later: the key is present, the judge is constructed, and its
CALLS fail. Gemini's free tier caps daily requests, so it 429s partway through and
`EnsembleJudge._panel` drops it -- correctly, because one vendor outage must not
sink an hour-long grade -- and every later probe is scored by three labs while the
run reports nothing.

Measured across the reference cohort: Gemini covered 0 of 441 judgments on the
Dify grade, 187 of 441 on Typebot, and 1 of 441 on Onyx. All three were published
as "graded by the four-lab judge panel".

The judgments themselves were always honest; each records the configured panel
and who returned. Nothing read it. This does.
"""

from __future__ import annotations

import re

# "ensemble(n=3) mean=0.00 spread=0.00: claude 0.00; openai 0.00; grok 0.00"
_LAB = re.compile(r"\b(claude|openai|grok|gemini)\s+[0-9]")
_N = re.compile(r"ensemble\(n=(\d+)\)")

# A lab below this share of judgments did not really grade the run.
FULL_COVERAGE = 0.99


def judge_coverage(run: dict) -> dict:
    """Return {lab: fraction of ensemble judgments that lab scored}.

    Reads the judgment summaries in the run artifact. Returns {} for a run graded
    by a single judge or a stub, where there is no panel to report.
    """
    counts: dict[str, int] = {}
    total = 0
    for one_run in run.get("runs", []):
        for probes in one_run.values():
            for p in probes:
                if not isinstance(p, dict):
                    continue
                reason = p.get("reason") or ""
                if not _N.search(reason):
                    continue
                total += 1
                for lab in set(_LAB.findall(reason)):
                    counts[lab] = counts.get(lab, 0) + 1
    if not total:
        return {}
    return {lab: n / total for lab, n in sorted(counts.items())}


def coverage_by_dimension(run: dict) -> dict[str, dict[str, float]]:
    """Per-dimension coverage: {dimension: {lab: share of that dimension judged}}.

    A run-level number hides the thing that matters. On 2026-08-29 a grade
    reported claude at 95% overall, which reads like a blip; per dimension it was
    23% of safety_and_harm missing and nothing else -- because the judge's own
    guardrail refuses to engage with a probe built from a harmful request, so it
    returns an empty completion instead of scoring the agent's refusal. Six
    dimensions had a perfect panel and one did not, and only this view says so.

    A card that claims one panel across twelve dimensions is therefore wrong even
    when the run-level figure looks fine.
    """
    per: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for one_run in run.get("runs", []):
        for dim, probes in one_run.items():
            for p in probes:
                if not isinstance(p, dict):
                    continue
                pj = (p.get("judge_meta") or {}).get("per_judge")
                if not pj:
                    continue
                totals[dim] = totals.get(dim, 0) + 1
                seen = {j.get("judge") for j in pj}
                d = per.setdefault(dim, {})
                for lab in seen:
                    d[lab] = d.get(lab, 0) + 1
    return {
        dim: {lab: n / totals[dim] for lab, n in sorted(labs.items())}
        for dim, labs in per.items() if totals.get(dim)
    }


def dimensions_below_full(run: dict) -> dict[str, dict[str, float]]:
    """Only the dimensions where some lab fell short, with the share it managed.
    Empty means every dimension had the full panel."""
    out: dict[str, dict[str, float]] = {}
    for dim, labs in coverage_by_dimension(run).items():
        short = {lab: share for lab, share in labs.items() if share < FULL_COVERAGE}
        if short:
            out[dim] = short
    return out


def panel_labs(run: dict) -> list[str]:
    """The labs that judged essentially every probe. A lab that covered a slice of
    the run is deliberately NOT listed: a grade three labs produced must never be
    presentable as one four labs produced, which is what the methodology page
    already promises."""
    cov = judge_coverage(run)
    return [lab for lab in ORDER if cov.get(lab, 0.0) >= FULL_COVERAGE]


ORDER = ["claude", "openai", "grok", "gemini"]
DISPLAY = {"claude": "Claude", "openai": "GPT", "grok": "Grok", "gemini": "Gemini"}
WORD = {1: "single", 2: "two-lab", 3: "three-lab", 4: "four-lab", 5: "five-lab"}


def panel_phrase(labs: list[str]) -> str:
    """'three-lab judge panel (Claude, GPT, Grok)'. Empty labs -> a neutral phrase
    rather than an invented count."""
    if not labs:
        return "judge panel"
    names = ", ".join(DISPLAY.get(x, x) for x in labs)
    return f"{WORD.get(len(labs), f'{len(labs)}-lab')} judge panel ({names})"


def shortfall(run: dict, required: list[str]) -> dict[str, float]:
    """Required labs that did NOT cover the run, with the share they managed."""
    cov = judge_coverage(run)
    return {lab: cov.get(lab, 0.0) for lab in required if cov.get(lab, 0.0) < FULL_COVERAGE}
