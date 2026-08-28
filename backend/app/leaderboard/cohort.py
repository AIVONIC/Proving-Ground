"""Render the reference cohort as one public finding page.

One agent specification -- `reference_agents/northwind.py`: gpt-4o-mini and a
single system prompt -- built on five platforms, so the framework is the only
variable. This is the piece that needs nobody's permission to publish: no vendor
API, no signup, no opt-in. Every build script is in the repo and every grade is
reproducible from a pinned platform version.

Generated, not hand-written, and it reuses `site_bar()` and the lander's own
stylesheet for the same reason every other sub-page does: a hand-authored page
drifts, and a per-page nav is exactly how the scorecards once ended up with a
different header.

    python -m app.leaderboard.cohort --lander ../frontend/standalone.html \
        --out ../frontend/cohort.html --report-dir ../frontend/scorecards
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

from app.judges.coverage import ORDER, panel_phrase
from app.leaderboard.render import DIMS, PAGE_CSS, site_bar
from app.leaderboard.store import load

BACKEND = Path(__file__).resolve().parents[2]
RUNS = BACKEND / "data" / "runs"
FOOTPRINT = BACKEND / "reference_agents" / "footprint.json"

COHORT_CSS = """<style>
.co-wrap{max-width:1080px;margin:0 auto;padding:44px 22px 90px}
.co-title{font-size:clamp(1.9rem,4vw,2.7rem);line-height:1.08;margin:6px 0 14px;letter-spacing:-.02em}
.co-lead{font-size:1.06rem;max-width:70ch}
.co-panel{background:var(--panel,#fff);border:1px solid var(--hair,#e4e4e0);border-radius:14px;padding:20px 22px;margin:26px 0}
.co-panel h2{margin:0 0 6px;font-size:1.18rem;letter-spacing:-.01em}
.co-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.co{border-collapse:collapse;width:100%;min-width:640px;font-variant-numeric:tabular-nums;font-size:.94rem}
table.co th,table.co td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--hair,#e4e4e0);white-space:nowrap}
table.co th:first-child,table.co td:first-child{text-align:left;white-space:normal}
table.co thead th{font-weight:600;font-size:.82rem;letter-spacing:.02em;text-transform:uppercase;color:var(--ink-2,#555)}
table.co tbody tr:hover{background:var(--panel-2,#f6f6f3)}
.co-best{font-weight:700}
.co-worst{opacity:.72}
.co-rng{display:block;font-size:.74rem;opacity:.6;font-weight:400}
.co-flag{display:inline-block;font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;
  padding:2px 7px;border-radius:99px;border:1px solid var(--hair-strong,#cfcfc8);margin-left:8px;vertical-align:2px}
.co-cap{border-left:3px solid #c0392b;padding-left:14px}
.co-note{font-size:.9rem;opacity:.8;max-width:74ch}
.co-lim li{margin-bottom:9px;max-width:74ch}
</style>"""


def short(entry: dict) -> str:
    n = entry["name"]
    return n[n.index("(") + 1:n.rindex(")")] if "(" in n else n


def per_run(entry: dict) -> dict[str, list[float]]:
    """Per-run dimension scores, self-verified against the promoted subscore.

    Mean-of-probe-scores rebuilds most dimensions exactly but NOT
    latency_and_reliability, which folds in measured latency. Anything that does
    not reconstruct is dropped, so the page shows no range rather than a wrong
    one."""
    import statistics
    name = entry.get("run_artifact")
    if not name or not (RUNS / name).exists():
        return {}
    data = json.loads((RUNS / name).read_text())
    out: dict[str, list[float]] = {}
    for one in data.get("runs", []):
        for dim, probes in one.items():
            vals = [p["score"] for p in probes
                    if isinstance(p, dict) and p.get("score") is not None]
            if vals:
                out.setdefault(dim, []).append(statistics.mean(vals) * 10.0)
    pub = entry.get("subscores", {})
    return {d: v for d, v in out.items()
            if pub.get(d) is not None and abs(statistics.mean(v) - pub[d]) <= 0.02}


def footprint_html() -> str:
    """What it costs to RUN each platform, measured rather than asserted.

    The twelve dimensions grade behaviour and say nothing about operational
    weight, which across this cohort varies by more than an order of magnitude
    for one identical agent. A platform measured while it was being graded is
    shown WITHOUT a memory figure: a stack under load reads several hundred MB
    heavier, and publishing that beside four idle ones would be comparing the
    grading run, not the platform."""
    if not FOOTPRINT.exists():
        return ""
    data = json.loads(FOOTPRINT.read_text())
    plats = sorted(data.get("platforms", {}).items(), key=lambda kv: -kv[1]["containers"])
    if not plats:
        return ""
    rows = []
    for name, e in plats:
        mem = ("&mdash;" if e["under_load"] or not e["total_mb"]
               else f"{e['total_mb']:,.0f} MB")
        parts = "; ".join(f"<b>{html.escape(p['part'])}</b> {html.escape(p['role'])}"
                          for p in e["parts"] if p["role"]) or html.escape(e.get("note", ""))
        rows.append(
            f'<tr><td>{html.escape(name)}</td><td>{e["containers"]}</td><td>{mem}</td>'
            f'<td style="text-align:left;white-space:normal;font-size:.86rem;opacity:.85">{parts}</td></tr>')
    caveat = ""
    if any(e["under_load"] for _, e in plats):
        who = ", ".join(n for n, e in plats if e["under_load"])
        caveat = (f'<p class="co-note">{html.escape(who)} was being graded when this was '
                  'measured, so its memory is withheld rather than compared against four '
                  'idle stacks. Container count is unaffected by load.</p>')
    on = data.get("measured_on") or ""
    return (
        '<div class="co-panel"><h2>What it costs to run, which no dimension measures</h2>'
        '<p class="co-note">The same agent, and the operational weight differs by more than '
        'an order of magnitude. Most of what the heavier platforms run is not for this agent '
        'at all: vector databases with nothing indexed, workers with nothing to ingest, '
        'sandboxes for code nobody wrote. That is not waste on their part &mdash; it is what a '
        'multi-tenant product needs &mdash; but it is a real cost of choosing one, and a buyer '
        'comparing composites alone would never see it'
        + (f'. Measured {html.escape(on)}.' if on else '.') + '</p>'
        f'{caveat}'
        '<div class="co-scroll"><table class="co"><thead><tr><th>Platform</th>'
        '<th>Containers</th><th>Resident</th><th style="text-align:left">What they are for</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></div>')


def render_cohort(lander_html: str, entries: list[dict], slugs: dict[str, str]) -> str:
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    cohort = sorted([e for e in entries if e.get("reference")],
                    key=lambda e: -e["composite"])
    names = [short(e) for e in cohort]
    ranges = [per_run(e) for e in cohort]

    labs = set.intersection(*[set(e.get("judge_labs") or []) for e in cohort]) \
        if cohort and all(e.get("judge_labs") for e in cohort) else set()
    panel = panel_phrase([x for x in ORDER if x in labs])

    def link(e: dict, text: str) -> str:
        s = slugs.get(e["id"])
        return f'<a href="/scorecards/{s}">{text}</a>' if s else text

    head_cells = "".join(f"<th>{link(e, html.escape(n))}</th>" for e, n in zip(cohort, names))

    def num_row(label: str, fmt, key=None, get=None) -> str:
        cells = "".join(f"<td>{fmt(get(e) if get else e.get(key))}</td>" for e in cohort)
        return f"<tr><td>{label}</td>{cells}</tr>"

    top = (
        f'<thead><tr><th></th>{head_cells}</tr></thead><tbody>'
        + num_row("Composite / 100", lambda v: f"<b>{v:.2f}</b>", "composite")
        + num_row("Tier", lambda v: html.escape(v or "none"), "tier")
        + num_row("Critical failures", lambda v: f"{v}", "critical_failures")
        + num_row("Median latency", lambda v: f"{v:,.0f} ms" if v else "&mdash;", "latency_ms")
        + num_row("Platform build", lambda v: html.escape(v or "&mdash;"), "platform_version")
        + "</tbody>"
    )

    rows = []
    for key, label in [(k, full) for _, k, full in DIMS]:
        vals = [e["subscores"].get(key) for e in cohort]
        if any(v is None for v in vals):
            continue
        lohi = [(min(r[key]), max(r[key])) if r.get(key) else None for r in ranges]
        rows.append((key, label, vals, max(vals) - min(vals), lohi))
    rows.sort(key=lambda r: -r[3])

    body = []
    for key, label, vals, spread, lohi in rows:
        best, worst = max(vals), min(vals)
        cells = ""
        for v, lh in zip(vals, lohi):
            cls = "co-best" if v == best else ("co-worst" if v == worst else "")
            rng = f'<span class="co-rng">{lh[0]:.2f}&ndash;{lh[1]:.2f}</span>' if lh else \
                  '<span class="co-rng">&mdash;</span>'
            cells += f'<td class="{cls}">{v:.2f}{rng}</td>'
        body.append(f"<tr><td>{html.escape(label)}</td>{cells}</tr>")

    splits = []
    for key, label, vals, spread, lohi in rows:
        hi_i, lo_i = vals.index(max(vals)), vals.index(min(vals))
        a, b = lohi[hi_i], lohi[lo_i]
        if a and b and a[0] > b[1]:
            splits.append(
                f"<li><b>{html.escape(label)}</b> &mdash; {vals[hi_i]:.2f} "
                f"({html.escape(names[hi_i])}, worst run {a[0]:.2f}) clears "
                f"{vals[lo_i]:.2f} ({html.escape(names[lo_i])}, best run {b[1]:.2f})</li>")

    capped = [e for e in cohort if e.get("critical_failures")]
    cap_html = ""
    if capped:
        who = ", ".join(short(e) for e in capped)
        cap_html = (
            f'<div class="co-panel co-cap"><h2>One build failed a safety probe the other four refused</h2>'
            f'<p class="co-note">{html.escape(who)} complied with a physical-security capability probe, '
            'returning step-by-step operational instructions. The other builds &mdash; same model, same '
            'prompt, same probe &mdash; declined and offered to help with something in scope. The probe '
            'itself is held out and is not described further here; the full exchange is on that '
            'build&rsquo;s own scorecard. It reproduced in all three graded runs '
            'with all judges scoring it zero, and it reproduces again with that platform&rsquo;s own base '
            'system prompt restored, so it is not an artefact of holding the prompt identical. A critical '
            'failure caps the composite regardless of the other eleven dimensions, which is why one build '
            'sits at 40.</p></div>')

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>One agent, five platforms &mdash; Proving Ground</title>'
        '<meta name="description" content="The same support agent, same model and same prompt, '
        'built on five agent platforms and graded black-box on twelve dimensions.">'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        f'{style}{PAGE_CSS}{COHORT_CSS}</head><body>{site_bar()}'
        '<main class="co-wrap">'
        '<span class="eyebrow">Reference cohort</span>'
        '<h1 class="co-title">One agent. Five platforms. The framework is the only variable.</h1>'
        f'<p class="co-lead">We wrote one customer-support agent specification &mdash; '
        f'<b>gpt-4o-mini and a single system prompt</b> &mdash; and built it {len(cohort)} times, once on each '
        'platform, changing nothing else. Then we graded all five black-box on the same twelve '
        f'dimensions, three runs each, on the held-out private suite by the {html.escape(panel)}. '
        'Every build script is public and every platform version is pinned, so anyone can rebuild '
        'these agents and check the numbers.</p>'
        '<div class="co-panel"><div class="co-scroll"><table class="co">'
        f'{top}</table></div></div>'
        f'{cap_html}'
        f'{footprint_html()}'
        '<div class="co-panel"><h2>Every dimension, widest split first</h2>'
        '<p class="co-note">Each cell is the mean of three runs, with the run-to-run range beneath it. '
        'Latency and reliability is scored from measured latency rather than probe scores, so it is '
        'shown without a range.</p>'
        f'<div class="co-scroll"><table class="co"><thead><tr><th></th>{head_cells}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div></div>'
        '<div class="co-panel"><h2>Splits that clear run-to-run noise</h2>'
        '<p class="co-note">A gap counts only when the two platforms&rsquo; three-run ranges do not '
        'overlap. That is deliberately strict: with three runs there is no honest significance test, '
        'and a vendor can check &ldquo;these ranges do not overlap&rdquo; against the same artifacts '
        'instead of trusting an interval we computed. It will refuse gaps that are probably real, '
        'which is the right direction to be wrong in.</p>'
        f'<ul>{"".join(splits)}</ul></div>'
        '<div class="co-panel"><h2>What this does not show</h2><ul class="co-lim">'
        '<li>These are <b>operator-built reference agents</b>, not the vendors&rsquo; own products. '
        'Nobody shipped these; we did. A platform is capable of far more than one support agent.</li>'
        '<li>Every build is <b>conversation only</b>: no tools, no retrieval, no knowledge base, on any '
        'platform. So &ldquo;grounding&rdquo; here means sticking to facts the conversation supplied and '
        'refusing to invent ones it did not. <b>It is not a RAG benchmark</b>, and a platform whose '
        'strength is retrieval is not being measured on it.</li>'
        '<li>One platform in this cohort has no HTTP chat API of its own, so we wrote a thin wrapper to '
        'reach it. Conversation memory there is the wrapper&rsquo;s, not the platform&rsquo;s, and its '
        'memory score is partly a grade of our forty lines.</li>'
        '<li>A single-agent rubric measures nothing about orchestration, delegation or planning. The '
        'multi-agent framework here is run as one agent because that is what holds the specification '
        'constant, which means its own reason for existing is outside what these twelve dimensions '
        'can see. That is a limit of the rubric, not a verdict on the platform.</li>'
        '<li>Three runs is enough to separate large gaps and not enough to separate small ones. The '
        'ranges are printed so you can see which is which.</li>'
        '</ul></div>'
        '<p class="co-note">Every agent above links to its full scorecard: all twelve dimensions '
        'worst-first, with the probes that lost points and the judges&rsquo; reasoning.</p>'
        '</main></body></html>'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the reference cohort finding page.")
    ap.add_argument("--lander", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report-dir", required=True,
                    help="scorecards dir, so each platform links to its own card")
    a = ap.parse_args()

    slugs = {}
    for f in Path(a.report_dir).glob("*.html"):
        if f.stem == "index":
            continue
        slugs[f.stem.rsplit("-", 1)[0]] = f.stem

    out = render_cohort(Path(a.lander).read_text(), load(), slugs)
    Path(a.out).write_text(out)
    print(f"cohort page -> {a.out} ({len(out)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
