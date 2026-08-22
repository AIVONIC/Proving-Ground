"""Render a private, per-agent scorecard from a grade run.

The leaderboard answers "who is better". This answers "where exactly did MY agent
slip, and what did it say". It is the artifact the outreach templates promise: a
vendor gets a link, sees their own weakest dimensions with their own transcripts,
and can argue with it.

Deliberately UNLISTED, not part of the public board:

  * ``noindex, nofollow`` and never added to the sitemap. The URL carries a token
    derived from the grade itself, so it is shareable but not enumerable.
  * The private probe PROMPTS are never rendered. They are not even present in a
    run artifact, so this cannot leak the held-out suite by accident. What is
    shown is the agent's own reply and the judge's rationale, which belong to the
    vendor anyway.

Nothing is published by generating one. Sending the link is a separate, human act.

    python -m app.leaderboard.report --run ../backend/data/runs/spark_x.json \
        --id spark --lander ../frontend/standalone.html --out-dir ../frontend/report
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import statistics
from pathlib import Path

from app.leaderboard.render import DIMS, PAGE_CSS, PREMIUM_FLOOR, radar_svg
from app.leaderboard.store import load

DIM_KEYS = {k: full for _short, k, full in DIMS}
MAX_PROBES_PER_DIM = 6   # worst-first; the rest are counted, never silently dropped
RESPONSE_CLIP = 900


def token_for(entry: dict, run_path: str) -> str:
    """Stable, non-enumerable id. Derived from the grade, so regenerating the same
    report reproduces the same URL and a vendor's link never rots. No clock and no
    randomness on purpose: the engine has neither."""
    seed = f'{entry["id"]}|{entry.get("graded_at", "")}|{entry["composite"]}|{Path(run_path).name}'
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def probe_rollup(runs: list[dict], dim_key: str) -> list[dict]:
    """Collapse N runs into one row per probe: mean score, and the evidence from
    the WORST run. A probe that fails once in three is the interesting one, so
    averaging away the bad run would hide exactly what the report is for."""
    by_id: dict[str, list[dict]] = {}
    for r in runs:
        for p in r.get(dim_key, []) or []:
            by_id.setdefault(p["probe_id"], []).append(p)
    rows = []
    for pid, ps in by_id.items():
        scores = [float(p.get("score", 0.0)) for p in ps]
        worst = min(ps, key=lambda p: float(p.get("score", 0.0)))
        rows.append({
            "probe_id": pid,
            "category": worst.get("category", ""),
            "mean": statistics.fmean(scores),
            "low": min(scores),
            "n": len(ps),
            "flaky": len(set(round(s, 3) for s in scores)) > 1,
            "critical": any(p.get("critical") for p in ps),
            "reason": worst.get("reason", ""),
            "response": worst.get("response", ""),
        })
    return sorted(rows, key=lambda r: (r["mean"], r["probe_id"]))


def _bar(score10: float) -> str:
    pct = max(0.0, min(100.0, score10 * 10))
    tone = "fail" if score10 < 5 else "warn" if score10 < PREMIUM_FLOOR else "pass"
    return (f'<span class="rp-bar"><span class="rp-bar-fill rp-{tone}" '
            f'style="width:{pct:.1f}%"></span></span>')


def _clip(s: str, n: int = RESPONSE_CLIP) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + " …"


def probe_html(p: dict) -> str:
    flags = []
    if p["critical"]:
        flags.append('<span class="rp-flag rp-crit">critical</span>')
    if p["flaky"]:
        flags.append(f'<span class="rp-flag">inconsistent &middot; low {p["low"]:.2f}</span>')
    cat = html.escape(p["category"])
    resp = html.escape(_clip(p["response"])) or "<em>no reply captured</em>"
    return (
        '<div class="rp-probe">'
        f'<div class="rp-probe-top"><code>{html.escape(p["probe_id"])}</code>'
        f'<span class="rp-cat">{cat}</span>{"".join(flags)}'
        f'<span class="rp-pscore">{p["mean"]:.2f}</span></div>'
        f'<div class="rp-judge">{html.escape(_clip(p["reason"], 320))}</div>'
        f'<div class="rp-resp">{resp}</div>'
        "</div>"
    )


def dimension_html(entry: dict, runs: list[dict], key: str, full: str) -> str:
    score = float(entry.get("subscores", {}).get(key, 0.0))
    probes = probe_rollup(runs, key)
    shown = [p for p in probes if p["mean"] < 1.0][:MAX_PROBES_PER_DIM]
    clean = len(probes) - len([p for p in probes if p["mean"] < 1.0])
    weak = score < PREMIUM_FLOOR
    if shown:
        omitted = len([p for p in probes if p["mean"] < 1.0]) - len(shown)
        tail = (f'<p class="rp-omitted">{omitted} further probe(s) below full marks not shown.</p>'
                if omitted > 0 else "")
        body = "".join(probe_html(p) for p in shown) + tail
    else:
        body = '<p class="rp-omitted">Every probe in this dimension scored full marks.</p>'
    return (
        f'<details class="rp-dim{" rp-dim-weak" if weak else ""}">'
        f'<summary><span class="rp-dim-name">{html.escape(full)}</span>'
        f'{_bar(score)}<span class="rp-dim-score">{score:.2f}</span>'
        f'<span class="rp-dim-meta">{len(probes)} probes &middot; {clean} clean</span></summary>'
        f'<div class="rp-dim-body">{body}</div></details>'
    )


REPORT_CSS = """<style>
.rp-wrap{max-width:900px;margin:0 auto;padding:0 22px}
.rp-head{padding:44px 0 8px}
.rp-title{font-size:clamp(1.8rem,3.6vw,2.6rem);margin:6px 0 4px;letter-spacing:-.01em}
.rp-sub{color:var(--muted);margin:0 0 22px}
.rp-kpis{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 8px}
.rp-kpi{flex:1;min-width:132px;background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:14px 16px}
.rp-kpi b{display:block;font-size:1.7rem;line-height:1;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.rp-kpi span{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.rp-two{display:grid;grid-template-columns:minmax(0,460px) minmax(0,1fr);gap:26px;align-items:start;margin:26px 0 8px}
@media(max-width:820px){.rp-two{grid-template-columns:1fr}}
.rp-note{background:var(--accent-ghost);border:1px solid var(--hair);border-left:2px solid var(--accent);
  border-radius:10px;padding:14px 16px;margin:22px 0;font-size:13.5px;color:var(--ink)}
.rp-weakbox{background:var(--panel);border:1px solid var(--hair);border-radius:12px;padding:16px 18px}
.rp-weakbox h3{margin:0 0 10px;font-size:1rem}
.rp-weak-row{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--hair)}
.rp-weak-row:last-child{border-bottom:none}
.rp-weak-name{flex:1;min-width:0}
.rp-bar{flex:0 0 120px;height:6px;border-radius:99px;background:var(--hair);overflow:hidden;display:inline-block}
.rp-bar-fill{display:block;height:100%;border-radius:99px}
.rp-pass{background:var(--pass)} .rp-warn{background:var(--warn)} .rp-fail{background:var(--fail)}
.rp-dim{border:1px solid var(--hair);border-radius:12px;background:var(--panel);margin:9px 0;overflow:hidden}
.rp-dim-weak{border-color:var(--warn)}
.rp-dim summary{display:flex;align-items:center;gap:12px;padding:13px 16px;cursor:pointer;list-style:none}
.rp-dim summary::-webkit-details-marker{display:none}
.rp-dim-name{flex:1;font-weight:600;min-width:0}
.rp-dim-score{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600;min-width:46px;text-align:right}
.rp-dim-meta{font-family:var(--mono);font-size:11px;color:var(--muted);min-width:118px;text-align:right}
@media(max-width:620px){.rp-dim-meta{display:none}}
.rp-dim-body{padding:2px 16px 14px;border-top:1px solid var(--hair)}
.rp-probe{border-top:1px solid var(--hair);padding:12px 0}
.rp-probe:first-child{border-top:none}
.rp-probe-top{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:6px}
.rp-probe-top code{font-family:var(--mono);font-size:11.5px;color:var(--ink)}
.rp-cat,.rp-flag{font-family:var(--mono);font-size:10px;letter-spacing:.07em;text-transform:uppercase;
  border:1px solid var(--hair-strong);border-radius:99px;padding:2px 8px;color:var(--muted)}
.rp-crit{color:var(--fail);border-color:var(--fail)}
.rp-pscore{margin-left:auto;font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600}
.rp-judge{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-bottom:7px;word-break:break-word}
.rp-resp{border-left:2px solid var(--hair-strong);padding:2px 0 2px 12px;white-space:pre-wrap;
  font-size:13.5px;color:var(--ink);overflow-wrap:anywhere}
.rp-omitted{color:var(--muted);font-size:12.5px;margin:10px 0 2px}
.rp-foot{color:var(--muted);font-size:12.5px;border-top:1px solid var(--hair);margin:44px 0 0;padding:18px 0 70px}
</style>"""


def render_report(lander_html: str, entry: dict, data: dict, token: str) -> str:
    import re
    style = re.search(r"<style>.*?</style>", lander_html, re.DOTALL).group(0)
    runs = data.get("runs") or []
    grade = data.get("grade") or {}
    subs = entry.get("subscores", {})
    name = html.escape(entry["name"])
    vendor = html.escape(entry.get("vendor") or "")
    conf = grade.get("confidence") or {}
    lo, hi = (entry.get("ci95") or [None, None])
    ci = f"{lo:.2f}&ndash;{hi:.2f}" if lo is not None else "n/a"

    ranked = sorted(((k, float(subs.get(k, 0.0))) for k in DIM_KEYS), key=lambda x: x[1])
    weak_rows = "".join(
        f'<div class="rp-weak-row"><span class="rp-weak-name">{html.escape(DIM_KEYS[k])}</span>'
        f'{_bar(v)}<span class="rp-dim-score">{v:.2f}</span></div>'
        for k, v in ranked[:3]
    )
    dims_html = "".join(dimension_html(entry, runs, k, DIM_KEYS[k]) for k, _v in ranked)
    crit = grade.get("critical_failures", 0)

    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        f'<title>{name} — Proving Ground scorecard</title>'
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        f'{style}{PAGE_CSS}{REPORT_CSS}</head><body>'
    )
    bar = ('<header class="bar"><div class="wrap bar-in">'
           '<a class="brand" href="/" style="text-decoration:none">'
           '<span><b>PROVING&nbsp;GROUND</b></span></a>'
           '<nav><a class="navlink" href="/methodology">Method</a>'
           '<a class="navlink" href="/leaderboard/">Leaderboard</a></nav>'
           '</div></header>')

    body = f"""<main class="rp-wrap">
<section class="rp-head">
  <span class="eyebrow">Private scorecard &middot; not published</span>
  <h1 class="rp-title">{name}</h1>
  <p class="rp-sub">{vendor} &middot; graded {html.escape(entry.get('graded_at',''))} on the held-out private suite by the four-lab judge panel.</p>
  <div class="rp-kpis">
    <div class="rp-kpi"><b>{entry['composite']:.2f}</b><span>Composite / 100</span></div>
    <div class="rp-kpi"><b>{html.escape(entry.get('tier','') or 'Unrated')}</b><span>Tier</span></div>
    <div class="rp-kpi"><b>{crit}</b><span>Critical failures</span></div>
    <div class="rp-kpi"><b>{entry.get('runs',1)}</b><span>Runs &middot; CI {ci}</span></div>
  </div>
</section>

<div class="rp-note"><b>This page is unlisted and is not on the leaderboard.</b> It exists so you can
check the grade rather than take it on trust. Nothing about {name} is published unless you say so.
The probe prompts are withheld because the suite is held out and rotated, so tuning to it is not
possible; what you see is your agent's own reply and the judge's reasoning for every probe that
lost a point. Method and rubrics are public at
<a href="https://github.com/AIVONIC/proving-ground">github.com/AIVONIC/proving-ground</a>.</div>

<div class="rp-two">
  <div>{radar_svg(subs)}</div>
  <div class="rp-weakbox">
    <h3>Where it slipped</h3>
    {weak_rows}
    <p class="rp-omitted">Lowest three of twelve. A dimension under {PREMIUM_FLOOR} blocks the
    Premium tier however high the composite goes, so these are the ones that move a grade.</p>
  </div>
</div>

<h2 style="margin:34px 0 4px;font-size:1.15rem">Every dimension, worst first</h2>
<p class="rp-sub">Open a row for the probes that lost points. Scores are the mean across
{entry.get('runs',1)} runs; the transcript shown is from the worst run, because a probe that fails
one time in three is the one worth reading.</p>
{dims_html}

<p class="rp-foot">Judge agreement {conf.get('judge_agreement',{}).get('overall','n/a')} &middot;
cross-run variance {conf.get('variance','n/a')} &middot; scorecard {token}.
Grades expire after 90 days because agents drift.</p>
</main>"""
    return head + bar + body + "</body></html>"


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a private per-agent scorecard.")
    ap.add_argument("--run", required=True, help="path to the grade run artifact JSON")
    ap.add_argument("--id", required=True, help="promoted leaderboard entry id")
    ap.add_argument("--lander", required=True, help="lander HTML, for the shared style block")
    ap.add_argument("--out-dir", required=True, help="directory to write <token>.html into")
    a = ap.parse_args()

    entry = next((e for e in load() if e["id"] == a.id), None)
    if entry is None:
        raise SystemExit(f"no promoted entry with id {a.id!r}; promote the run first")
    data = json.loads(Path(a.run).read_text())
    token = token_for(entry, a.run)
    out = Path(a.out_dir) / f"{token}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(Path(a.lander).read_text(), entry, data, token))
    print(f"scorecard {entry['name']} -> {out} ({out.stat().st_size} bytes)")
    print(f"URL when deployed: https://provingground.aivonic.ai/report/{token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
